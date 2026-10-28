"""Stratégies disponibles et régimes où chacune s'applique. Seule `trend_pullback` est activée par défaut.

ATTENTION : aucune de ces stratégies n'a d'avantage démontré (voir le backtest par périodes et le témoin aléatoire).
"""

from __future__ import annotations

from collections.abc import Sequence

from orionx.analysis.state import Regime
from orionx.core.config import AnalysisConfig, DecisionConfig
from orionx.core.errors import ConfigError
from orionx.decision.engine import DecisionEngine, StrategyEntry
from orionx.strategies.breakout import Breakout
from orionx.strategies.mean_reversion import MeanReversion
from orionx.strategies.trend_pullback import TrendPullback

TREND = frozenset({Regime.TREND_UP, Regime.TREND_DOWN})
AVAILABLE = {  # nom -> (classe, régimes d'application, poids)
    "trend_pullback": (TrendPullback, TREND, 1.0),
    "mean_reversion": (MeanReversion, frozenset({Regime.RANGE}), 1.0),
    "breakout": (Breakout, TREND | {Regime.RANGE}, 0.8),
}


def build_engine(names: Sequence[str], decision: DecisionConfig | None = None, analysis: AnalysisConfig | None = None) -> DecisionEngine:
    unknown = [n for n in names if n not in AVAILABLE]
    if unknown:
        raise ConfigError(f"Stratégies inconnues : {unknown}. Disponibles : {list(AVAILABLE)}")
    entries = [StrategyEntry(cls(), regimes, True, weight) for n in names for cls, regimes, weight in [AVAILABLE[n]]]
    return DecisionEngine(entries, decision, analysis)
