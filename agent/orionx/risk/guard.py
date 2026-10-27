"""Verrouillage des paramètres de risque (M5, F5.7).

Toute modification qui AUGMENTE le risque n'est appliquée qu'après un délai (24 h par défaut), et s'annule si une
modification plus prudente est demandée entre-temps. Une baisse du risque s'applique tout de suite. Modifier le
fichier config.yaml ne contourne pas ce délai : au démarrage, l'agent compare avec la dernière configuration ACTIVE.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from orionx.core.config import RiskConfig
from orionx.core.models import utcnow

HIGHER_IS_RISKIER = (
    "risk_per_trade_pct", "max_daily_loss_pct", "max_weekly_loss_pct", "max_drawdown_pct", "max_open_positions",
    "max_total_open_risk_pct", "max_currency_exposure_pct", "max_consecutive_losses", "max_spread_points",
    "max_slippage_points",
)  # fmt: skip
LOWER_IS_RISKIER = ("min_rr", "min_margin_level_pct")


def is_riskier(old: RiskConfig, new: RiskConfig) -> bool:
    """Vrai si `new` autorise PLUS de risque que `old` sur au moins un paramètre."""
    if any(getattr(new, f) > getattr(old, f) for f in HIGHER_IS_RISKIER):
        return True
    if any(getattr(new, f) < getattr(old, f) for f in LOWER_IS_RISKIER):
        return True
    if old.weekend_guard and not new.weekend_guard:
        return True
    olds, news = old.max_spread_points_by_symbol, new.max_spread_points_by_symbol
    return any(v > olds.get(k, old.max_spread_points) for k, v in news.items())


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

    # --- fichiers -----------------------------------------------------------------
    @staticmethod
    def _load(path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None

    def active(self) -> RiskConfig | None:
        data = self._load(self._active_file)
        return RiskConfig(**data) if data else None

    def pending(self) -> tuple[RiskConfig, datetime] | None:
        data = self._load(self._pending_file)
        if not data:
            return None
        return RiskConfig(**data["config"]), datetime.fromisoformat(data["effective_at"])

    def _set_active(self, cfg: RiskConfig) -> None:
        self._active_file.write_text(json.dumps(asdict(cfg)), encoding="utf-8")

    def _set_pending(self, cfg: RiskConfig, effective_at: datetime) -> None:
        self._pending_file.write_text(json.dumps({"config": asdict(cfg), "effective_at": effective_at.isoformat(),
                                                  "requested_at": self._clock().isoformat()}), encoding="utf-8")  # fmt: skip

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

    def resolve_startup(self, configured: RiskConfig) -> RiskConfig:
        """Configuration à utiliser au démarrage : jamais plus risquée que la dernière active sans passer par le délai."""
        active = self.active()
        if active is None:
            self._set_active(configured)
            return configured
        if configured == active:
            return active
        if is_riskier(active, configured):
            self.request(configured)  # le fichier demande PLUS de risque : mise en attente, on garde l'ancienne
            return active
        self._set_active(configured)
        self.cancel_pending()
        return configured
