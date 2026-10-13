"""Configuration avec BORNES ABSOLUES non modifiables par l'utilisateur."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orionx.core.errors import ConfigError

# Valeurs maximales codées en dur : aucun fichier de configuration ne peut les dépasser.
ABSOLUTE_LIMITS: dict[str, float] = {
    "risk_per_trade_pct": 1.0,
    "max_daily_loss_pct": 5.0,
    "max_weekly_loss_pct": 10.0,
    "max_drawdown_pct": 20.0,
    "max_open_positions": 5,
    "max_total_open_risk_pct": 4.0,
    "max_consecutive_losses": 8,
}
ABSOLUTE_MIN_RR = 1.0


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 6.0
    max_drawdown_pct: float = 10.0
    max_open_positions: int = 3
    max_total_open_risk_pct: float = 2.0
    min_rr: float = 1.5
    max_consecutive_losses: int = 4
    max_spread_points: float = 30.0
    min_margin_level_pct: float = 300.0
    max_slippage_points: int = 10
    approval_ttl_seconds: float = 5.0

    def __post_init__(self) -> None:
        for name, limit in ABSOLUTE_LIMITS.items():
            value = getattr(self, name)
            if value <= 0:
                raise ConfigError(f"{name} doit être > 0 (reçu {value})")
            if value > limit:
                raise ConfigError(f"{name}={value} dépasse la borne absolue ({limit})")
        if self.min_rr < ABSOLUTE_MIN_RR:
            raise ConfigError(f"min_rr={self.min_rr} est inférieur au minimum absolu")
        if self.max_total_open_risk_pct < self.risk_per_trade_pct:
            raise ConfigError("max_total_open_risk_pct doit être >= risk_per_trade_pct")
        if self.approval_ttl_seconds <= 0 or self.max_spread_points <= 0:
            raise ConfigError("approval_ttl_seconds et max_spread_points doivent être > 0")


@dataclass(frozen=True)
class AppConfig:
    mode: str = "demo"
    autonomy: str = "semi_auto"
    symbols: tuple[str, ...] = ("EURUSD",)
    max_data_age_seconds: float = 3.0
    risk: RiskConfig = field(default_factory=RiskConfig)

    def __post_init__(self) -> None:
        if self.mode != "demo":
            raise ConfigError("Seul le mode 'demo' est autorisé dans cette version.")
        if self.autonomy not in {"signals_only", "semi_auto", "auto"}:
            raise ConfigError(f"autonomy invalide : {self.autonomy}")
        if not self.symbols:
            raise ConfigError("Au moins un symbole est requis")
        if self.max_data_age_seconds <= 0:
            raise ConfigError("max_data_age_seconds doit être > 0")


def load_config(path: str | Path) -> AppConfig:
    import yaml  # import tardif : seule la lecture d'un fichier YAML en a besoin

    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    try:
        risk = RiskConfig(**(raw.pop("risk", None) or {}))
        if "symbols" in raw:
            raw["symbols"] = tuple(raw["symbols"])
        return AppConfig(risk=risk, **raw)
    except TypeError as exc:  # clé inconnue
        raise ConfigError(f"Configuration invalide : {exc}") from exc
