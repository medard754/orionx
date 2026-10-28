"""Configuration avec BORNES ABSOLUES non modifiables par l'utilisateur."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orionx.core.errors import ConfigError

if TYPE_CHECKING:
    from orionx.positions.manager import PositionRules

# Valeurs maximales codées en dur : aucun fichier de configuration ne peut les dépasser.
ABSOLUTE_LIMITS: dict[str, float] = {
    "risk_per_trade_pct": 1.0,
    "max_daily_loss_pct": 5.0,
    "max_weekly_loss_pct": 10.0,
    "max_drawdown_pct": 20.0,
    "max_open_positions": 5,
    "max_total_open_risk_pct": 4.0,
    "max_consecutive_losses": 8,
    "max_currency_exposure_pct": 4.0,
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
    max_currency_exposure_pct: float = 2.0  # risque NET (en % du capital) sur une même devise
    weekend_guard: bool = True  # aucune nouvelle entrée du vendredi soir à la réouverture
    weekend_no_entry_from_utc: int = 18  # heure UTC du vendredi à partir de laquelle on n'ouvre plus
    max_spread_points_by_symbol: dict[str, float] = field(default_factory=dict)  # sinon max_spread_points

    def __post_init__(self) -> None:
        if not 0 <= self.weekend_no_entry_from_utc <= 23:
            raise ConfigError("weekend_no_entry_from_utc doit être entre 0 et 23")
        if any(v <= 0 for v in self.max_spread_points_by_symbol.values()):
            raise ConfigError("max_spread_points_by_symbol : valeurs > 0 requises")
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


PROFILES: dict[str, dict[str, float]] = {
    "prudent": {"risk_per_trade_pct": 0.5, "max_daily_loss_pct": 3.0, "max_weekly_loss_pct": 6.0, "max_drawdown_pct": 10.0,
                "max_open_positions": 3, "max_total_open_risk_pct": 2.0, "max_currency_exposure_pct": 2.0,
                "min_rr": 1.5, "max_consecutive_losses": 4},
    "moderate": {"risk_per_trade_pct": 0.75, "max_daily_loss_pct": 4.0, "max_weekly_loss_pct": 8.0, "max_drawdown_pct": 15.0,
                 "max_open_positions": 4, "max_total_open_risk_pct": 3.0, "max_currency_exposure_pct": 3.0,
                 "min_rr": 1.5, "max_consecutive_losses": 5},
    "dynamic": {"risk_per_trade_pct": 1.0, "max_daily_loss_pct": 5.0, "max_weekly_loss_pct": 10.0, "max_drawdown_pct": 20.0,
                "max_open_positions": 5, "max_total_open_risk_pct": 4.0, "max_currency_exposure_pct": 4.0,
                "min_rr": 1.2, "max_consecutive_losses": 6},
}  # fmt: skip


def risk_from_profile(name: str, **overrides: Any) -> RiskConfig:
    """Profil prédéfini (prudent / moderate / dynamic), avec réglages fins possibles DANS les bornes absolues."""
    if name not in PROFILES:
        raise ConfigError(f"Profil inconnu : {name}. Disponibles : {list(PROFILES)}")
    return RiskConfig(**{**PROFILES[name], **overrides})


@dataclass(frozen=True)
class AnalysisConfig:
    """Réglages de l'analyse de marché (M3 v1, à base de règles)."""

    vol_window: int = 120  # nb de bougies H1 servant de « volatilité normale »
    high_vol_ratio: float = 1.8  # ATR actuel / ATR médian à partir duquel on parle de forte volatilité
    trend_er: float = 0.25  # ratio d'efficacité minimal (Kaufman) pour parler de tendance
    trend_er_period: int = 20
    trend_strength_min: float = 0.3  # |EMA20 - EMA50| / ATR minimal
    block_high_vol: bool = True  # refuser les nouvelles entrées en forte volatilité
    blocked_sessions: tuple[str, ...] = ()  # ex. ("off", "asia")
    news_before_min: float = 30.0
    news_after_min: float = 30.0
    news_min_impact: str = "high"  # low | medium | high
    calendar_path: str | None = None

    def __post_init__(self) -> None:
        if self.vol_window < 30 or self.high_vol_ratio <= 1.0 or self.trend_er_period < 5:
            raise ConfigError("analysis : vol_window >= 30, high_vol_ratio > 1, trend_er_period >= 5")
        if self.news_min_impact not in {"low", "medium", "high"}:
            raise ConfigError("analysis.news_min_impact doit valoir low, medium ou high")
        if self.news_before_min < 0 or self.news_after_min < 0:
            raise ConfigError("analysis : fenêtres d'annonce négatives")


@dataclass(frozen=True)
class PositionsConfig:
    """Gestion des positions ouvertes (M7)."""

    breakeven_r: float = 1.0
    trail_r: float = 1.0
    trail_atr_mult: float | None = None  # si défini : le stop suit à N × ATR au lieu de N × R
    partial_r: float = 1.5
    partial_fraction: float = 0.5
    max_hold_minutes: float | None = None
    weekend_flat: bool = False  # tout clôturer avant la fermeture du vendredi
    weekend_close_minutes_before: int = 30
    news_action: str = "tighten"  # tighten | reduce | close : réaction à une annonce imminente
    manage_foreign: bool = True  # gérer aussi les positions ouvertes à la main
    thesis_exit_min_reasons: int | None = None  # sortie si la thèse est dégradée pour N raisons
    thesis_reduce_min_reasons: int | None = None  # réduction de moitié à partir de N raisons

    def __post_init__(self) -> None:
        if self.news_action not in {"tighten", "reduce", "close"}:
            raise ConfigError("positions.news_action doit valoir tighten, reduce ou close")
        if not 0 < self.partial_fraction < 1 or self.breakeven_r <= 0 or self.trail_r <= 0:
            raise ConfigError("positions : partial_fraction dans ]0,1[, breakeven_r et trail_r > 0")

    def to_rules(self) -> PositionRules:
        from orionx.positions.manager import PositionRules  # import tardif : évite une dépendance circulaire

        return PositionRules(**self.__dict__)


@dataclass(frozen=True)
class DecisionConfig:
    """Moteur de décision (M4)."""

    max_per_currency_direction: int = 1  # pas deux entrées simultanées dans le même sens sur une même devise
    consensus_bonus: float = 0.05  # bonus de score quand plusieurs stratégies concordent
    allow_pyramiding: bool = False  # renforcer une position DÉJÀ sans risque (stop au point d'entrée ou mieux)

    def __post_init__(self) -> None:
        if self.max_per_currency_direction < 1 or not 0 <= self.consensus_bonus <= 0.2:
            raise ConfigError("decision : max_per_currency_direction >= 1, consensus_bonus entre 0 et 0.2")


@dataclass(frozen=True)
class AppConfig:
    mode: str = "demo"
    autonomy: str = "semi_auto"
    symbols: tuple[str, ...] = ("EURUSD",)
    max_data_age_seconds: float = 3.0
    stale_halt_seconds: float = 30.0  # prix figé plus longtemps : mode sûr (plus aucune entrée)
    risk: RiskConfig = field(default_factory=RiskConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    positions: PositionsConfig = field(default_factory=PositionsConfig)
    decision: DecisionConfig = field(default_factory=DecisionConfig)
    strategies: tuple[str, ...] = ("trend_pullback",)  # stratégies activées (voir strategies/registry.py)

    def __post_init__(self) -> None:
        if self.mode != "demo":
            raise ConfigError("Seul le mode 'demo' est autorisé dans cette version.")
        if self.autonomy not in {"signals_only", "semi_auto", "auto"}:
            raise ConfigError(f"autonomy invalide : {self.autonomy}")
        if not self.symbols:
            raise ConfigError("Au moins un symbole est requis")
        if self.max_data_age_seconds <= 0:
            raise ConfigError("max_data_age_seconds doit être > 0")
        if self.stale_halt_seconds <= self.max_data_age_seconds:
            raise ConfigError("stale_halt_seconds doit être supérieur à max_data_age_seconds")


def _section[T](name: str, build: Callable[[], T]) -> T:
    """Construit une section de configuration en nommant la section fautive et la clé inconnue."""
    try:
        return build()
    except TypeError as exc:
        key = str(exc).rpartition("keyword argument ")[2] or str(exc)
        raise ConfigError(
            f"Section « {name} » : clé inconnue ou mal placée {key}. Causes fréquentes : faute de frappe, indentation "
            f"incorrecte, ou section « {name}: » écrite deux fois (il ne doit y en avoir qu'une, en début de ligne)."
        ) from exc


def load_config(path: str | Path) -> AppConfig:
    import yaml  # import tardif : seule la lecture d'un fichier YAML en a besoin

    try:
        raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Fichier YAML illisible (indentation avec des espaces, pas de tabulations ?) : {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Le fichier de configuration doit contenir des paires clé: valeur")
    r = dict(raw.pop("risk", None) or {})
    profile = r.pop("profile", None)
    risk = _section("risk", lambda: risk_from_profile(profile, **r) if profile else RiskConfig(**r))
    positions = _section("positions", lambda: PositionsConfig(**(raw.pop("positions", None) or {})))
    decision = _section("decision", lambda: DecisionConfig(**(raw.pop("decision", None) or {})))
    a = dict(raw.pop("analysis", None) or {})
    if "blocked_sessions" in a:
        a["blocked_sessions"] = tuple(a["blocked_sessions"])
    analysis = _section("analysis", lambda: AnalysisConfig(**a))
    for key in ("strategies", "symbols"):
        if key in raw:
            raw[key] = tuple(raw[key])
    return _section("(racine)", lambda: AppConfig(risk=risk, analysis=analysis, positions=positions, decision=decision, **raw))
