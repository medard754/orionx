"""Verrouillage des paramètres de risque (M5, F5.7).

Toute modification qui AUGMENTE le risque n'est appliquée qu'après un délai (24 h par défaut), et s'annule si une
modification plus prudente est demandée entre-temps. Une baisse du risque s'applique tout de suite. Modifier le
fichier config.yaml ne contourne pas ce délai : au démarrage, l'agent compare avec la dernière configuration ACTIVE.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from orionx.core.config import RiskConfig
from orionx.core.errors import ConfigError
from orionx.core.models import utcnow

HIGHER_IS_RISKIER = (
    "risk_per_trade_pct", "max_daily_loss_pct", "max_weekly_loss_pct", "max_drawdown_pct", "max_open_positions",
    "max_total_open_risk_pct", "max_currency_exposure_pct", "max_consecutive_losses", "max_spread_points",
    "max_slippage_points", "approval_ttl_seconds", "weekend_no_entry_from_utc",
)  # fmt: skip
LOWER_IS_RISKIER = ("min_rr", "min_margin_level_pct")
SPECIAL = ("weekend_guard", "max_spread_points_by_symbol")  # comparés à part


def is_riskier(old: RiskConfig, new: RiskConfig) -> bool:
    """Vrai si `new` autorise PLUS de risque que `old` sur au moins un paramètre.

    Un champ non classé ci-dessus est considéré comme plus risqué dès qu'il change : un nouveau paramètre ne peut pas
    contourner le délai par oubli."""
    if any(getattr(new, f) > getattr(old, f) for f in HIGHER_IS_RISKIER):
        return True
    if any(getattr(new, f) < getattr(old, f) for f in LOWER_IS_RISKIER):
        return True
    if old.weekend_guard and not new.weekend_guard:
        return True
    known = {*HIGHER_IS_RISKIER, *LOWER_IS_RISKIER, *SPECIAL}
    if any(getattr(new, f.name) != getattr(old, f.name) for f in fields(RiskConfig) if f.name not in known):
        return True
    olds, news = old.max_spread_points_by_symbol, new.max_spread_points_by_symbol
    return any(news.get(k, new.max_spread_points) > olds.get(k, old.max_spread_points) for k in olds.keys() | news.keys())


@dataclass(frozen=True)
class RiskChange:
    status: str  # "immediate" | "delayed" | "unchanged"
    effective_at: datetime | None
    note: str


class RiskParamGuard:
    def __init__(self, directory: str | Path, latency_hours: float = 24.0, clock: Callable[[], datetime] = utcnow) -> None:
        self._dir, self._latency, self._clock = Path(directory), timedelta(hours=latency_hours), clock
        self._dir.mkdir(parents=True, exist_ok=True)
        self._active_file, self._pending_file = self._dir / "risk_active.json", self._dir / "risk_pending.json"
        self.startup_note = ""  # ce que resolve_startup a décidé (écrit dans le journal au démarrage)

    # --- fichiers -----------------------------------------------------------------
    @staticmethod
    def _write(path: Path, data: dict[str, Any]) -> None:
        """Écriture atomique : un crash pendant l'écriture laisse l'ancien fichier intact, jamais un fichier tronqué."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)

    def active(self) -> RiskConfig | None:
        """None seulement si le fichier n'existe pas (premier démarrage). Illisible : on refuse, sinon config.yaml
        serait accepté sans délai."""
        if not self._active_file.exists():
            return None
        try:
            return RiskConfig(**json.loads(self._active_file.read_text(encoding="utf-8")))
        except (ValueError, OSError, TypeError, ConfigError) as exc:
            raise ConfigError(f"{self._active_file} illisible ({exc}) : le restaurer, ou le supprimer volontairement") from exc

    def pending(self) -> tuple[RiskConfig, datetime] | None:
        """Une demande illisible n'est jamais appliquée (sens prudent)."""
        if not self._pending_file.exists():
            return None
        try:
            data = json.loads(self._pending_file.read_text(encoding="utf-8"))
            return RiskConfig(**data["config"]), datetime.fromisoformat(data["effective_at"])
        except (ValueError, OSError, TypeError, KeyError, ConfigError):
            return None

    def _set_active(self, cfg: RiskConfig) -> None:
        self._write(self._active_file, asdict(cfg))

    def _set_pending(self, cfg: RiskConfig, effective_at: datetime) -> None:
        self._write(self._pending_file, {"config": asdict(cfg), "effective_at": effective_at.isoformat(),
                                         "requested_at": self._clock().isoformat()})  # fmt: skip

    def cancel_pending(self) -> None:
        self._pending_file.unlink(missing_ok=True)

    # --- logique ------------------------------------------------------------------
    def request(self, new: RiskConfig) -> RiskChange:
        """Demande de changement (CLI ou fichier). Comparée à la configuration ACTIVE, pas à une demande en attente."""
        current = self.active()
        now = self._clock()
        if current is None or new == current:
            self.cancel_pending()
            if current is None:
                self._set_active(new)
            return RiskChange("unchanged", None, "aucun changement")
        if is_riskier(current, new):
            when = now + self._latency
            self._set_pending(new, when)
            return RiskChange("delayed", when, f"risque en hausse : appliqué après {self._latency}")
        self._set_pending(new, now)  # plus prudent : appliqué au prochain cycle de l'agent
        return RiskChange("immediate", now, "risque en baisse : appliqué immédiatement")

    def due(self) -> RiskConfig | None:
        """Configuration à appliquer maintenant (délai écoulé), ou None. Elle devient la configuration active."""
        pending = self.pending()
        if pending is None or pending[1] > self._clock():
            return None
        cfg = pending[0]
        self._set_active(cfg)
        self.cancel_pending()
        return cfg

    def preview(self, configured: RiskConfig) -> RiskConfig:
        """Ce que l'agent utiliserait s'il démarrait maintenant (resolve_startup puis due), en LECTURE SEULE."""
        active, pending = self.active(), self.pending()
        if active is None:
            return configured
        if configured != active and not is_riskier(active, configured):
            return configured  # plus prudent : appliqué, la demande en attente serait annulée
        if pending is not None and pending[1] <= self._clock():
            return pending[0]
        return active

    def resolve_startup(self, configured: RiskConfig) -> RiskConfig:
        """Configuration à utiliser au démarrage : jamais plus risquée que la dernière active sans passer par le délai."""
        active, pending = self.active(), self.pending()
        if active is None:
            self._set_active(configured)
            self.startup_note = "premier démarrage : config.yaml devient la configuration active"
            return configured
        if configured == active:
            self.startup_note = "config.yaml identique à la configuration active"
            return active
        if is_riskier(active, configured):  # le fichier demande PLUS de risque : on garde l'ancienne
            if pending is None:
                when = self.request(configured).effective_at
                self.startup_note = f"config.yaml plus risqué : mis en attente jusqu'au {when:%d/%m/%Y %H:%M} UTC"
            elif pending[0] == configured:  # déjà en attente : le délai n'est PAS relancé à chaque redémarrage
                self.startup_note = f"config.yaml plus risqué, déjà en attente jusqu'au {pending[1]:%d/%m/%Y %H:%M} UTC"
            else:  # une autre demande attend (ex. risk-set) : elle n'est pas écrasée en silence
                self.startup_note = "config.yaml plus risqué ignoré : une autre demande est déjà en attente (voir risk-status)"
            return active
        self._set_active(configured)
        self.cancel_pending()
        self.startup_note = "config.yaml plus prudent : appliqué" + (" ; la demande en attente est ANNULÉE" if pending else "")
        return configured
