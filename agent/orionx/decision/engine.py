"""Moteur de décision (M4) : transforme les analyses en propositions, ou décide de ne rien faire.

 F4.1  bibliothèque de stratégies activables, chacune avec les régimes de marché où elle s'applique
 F4.2  sélection selon le régime détecté par M3
 F4.3  proposition structurée (TradeProposal) + F4.4 rapport risque/rendement calculé
 F4.5  classement si plusieurs opportunités, avec diversification par devise
 F4.6  signaux contradictoires => AUCUN trade
 F4.7  renforcement : jamais en moyennant à la baisse ; seulement sur une position DÉJÀ sans risque (option)
       (réduire / clôturer / resserrer est exécuté par M7 selon ses règles)
Il ne passe jamais d'ordre : le gestionnaire de risques (M5) décide ensuite.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from orionx.analysis.state import MarketState, Regime, analyze
from orionx.analysis.tzutil import server_to_utc_eet
from orionx.core.config import AnalysisConfig, DecisionConfig
from orionx.core.models import Bar, Position, Side, Tick, TradeProposal
from orionx.strategies.trend_pullback import Strategy


@dataclass(frozen=True)
class StrategyEntry:
    strategy: Strategy
    regimes: frozenset[Regime] | None = None  # None : aucun filtrage par régime
    enabled: bool = True
    weight: float = 1.0


@dataclass(frozen=True)
class Candidate:
    proposal: TradeProposal
    score: float
    strategies: tuple[str, ...]
    rr: float


@dataclass(frozen=True)
class EngineResult:
    symbol: str
    candidates: tuple[Candidate, ...] = ()
    notes: tuple[str, ...] = ()


def _risk_free(pos: Position) -> bool:
    if pos.sl is None:
        return False
    return pos.sl >= pos.price_open if pos.side is Side.BUY else pos.sl <= pos.price_open


class DecisionEngine:
    name = "decision_engine"

    def __init__(self, entries: Sequence[StrategyEntry], cfg: DecisionConfig | None = None,
                 analysis: AnalysisConfig | None = None) -> None:  # fmt: skip
        self.entries, self.cfg, self.analysis = tuple(entries), cfg or DecisionConfig(), analysis or AnalysisConfig()
        enabled = [e for e in self.entries if e.enabled]
        if not enabled:
            raise ValueError("Aucune stratégie activée")
        if len({e.strategy.timeframe for e in enabled}) > 1:
            raise ValueError("Les stratégies activées doivent partager la même unité de temps")
        self.timeframe = enabled[0].strategy.timeframe
        needs_state = any(e.regimes is not None for e in enabled)
        self.min_bars = max(max(e.strategy.min_bars for e in enabled), self.analysis.vol_window + 60 if needs_state else 0)

    # --- F4.1 à F4.4, F4.6, F4.7 --------------------------------------------------------
    def evaluate(self, symbol: str, bars: Sequence[Bar], tick: Tick, now: datetime,
                 positions: Sequence[Position], state: MarketState | None) -> EngineResult:  # fmt: skip
        mine = [p for p in positions if p.symbol == symbol]
        if mine:
            if not self.cfg.allow_pyramiding:
                return EngineResult(symbol, (), ("POSITION_OPEN",))
            if not all(_risk_free(p) for p in mine):
                return EngineResult(symbol, (), ("PYRAMID_NOT_RISK_FREE",))
        eligible: list[StrategyEntry] = []
        for e in self.entries:
            if not e.enabled:
                continue
            if e.regimes is not None and state is not None:
                if state.regime is Regime.UNKNOWN:
                    return EngineResult(symbol, (), ("REGIME_UNKNOWN",))
                if state.regime not in e.regimes:
                    continue
            eligible.append(e)
        if not eligible:
            return EngineResult(symbol, (), (f"NO_STRATEGY_FOR_REGIME:{state.regime.value if state else '?'}",))
        raw = [(e, p) for e in eligible if (p := e.strategy.generate(symbol, bars, tick, now, set()))]
        if not raw:
            return EngineResult(symbol)
        if len({p.side for _, p in raw}) > 1:  # F4.6 : en cas de doute, pas de trade
            return EngineResult(symbol, (), ("CONFLICT:" + ",".join(f"{e.strategy.name}={p.side.value}" for e, p in raw),))
        best_e, best_p = max(raw, key=lambda ep: ep[1].confidence * ep[0].weight)
        if mine and any(m.side is not best_p.side for m in mine):
            return EngineResult(symbol, (), ("PYRAMID_OPPOSITE",))
        score = min(0.99, best_p.confidence * best_e.weight + self.cfg.consensus_bonus * (len(raw) - 1))
        entry = tick.ask if best_p.side is Side.BUY else tick.bid
        rr = abs(best_p.tp - entry) / abs(entry - best_p.sl) if entry != best_p.sl else 0.0
        return EngineResult(symbol, (Candidate(best_p, score, tuple(e.strategy.name for e, _ in raw), rr),))

    # --- F4.5 ---------------------------------------------------------------------------
    def rank(self, results: Sequence[EngineResult]) -> tuple[list[Candidate], list[Candidate]]:
        """Classe les opportunités de tous les symboles ; écarte celles qui doublonnent une direction sur une devise.

        Renvoie (retenues, écartées). Ex. : acheter EURUSD et GBPUSD revient à vendre le dollar deux fois."""
        ordered = sorted((c for r in results for c in r.candidates), key=lambda c: c.score, reverse=True)
        used: dict[tuple[str, int], int] = {}
        kept: list[Candidate] = []
        dropped: list[Candidate] = []
        for c in ordered:
            sym, sign = c.proposal.symbol, 1 if c.proposal.side is Side.BUY else -1
            keys = [(sym[:3], sign), (sym[3:6], -sign)]
            if any(used.get(k, 0) >= self.cfg.max_per_currency_direction for k in keys):
                dropped.append(c)
                continue
            for k in keys:
                used[k] = used.get(k, 0) + 1
            kept.append(c)
        return kept, dropped

    def as_strategy(self) -> EngineStrategy:
        return EngineStrategy(self)


class EngineStrategy:
    """Adaptateur : permet de passer le moteur (stratégies + régimes) au backtest, qui attend une Strategy."""

    def __init__(self, engine: DecisionEngine) -> None:
        self.engine, self.name, self.timeframe, self.min_bars = engine, engine.name, engine.timeframe, engine.min_bars

    def generate(self, symbol: str, bars: Sequence[Bar], tick: Tick, now: datetime, open_symbols: set[str]) -> TradeProposal | None:
        gated = any(e.regimes is not None for e in self.engine.entries if e.enabled)
        state = analyze(symbol, bars, self.engine.analysis, server_to_utc_eet) if gated else None
        res = self.engine.evaluate(symbol, bars, tick, now, [], state)
        return res.candidates[0].proposal if res.candidates else None
