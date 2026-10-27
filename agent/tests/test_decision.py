import unittest
from datetime import timedelta

from orionx.analysis.state import MarketState, Regime, Session
from orionx.backtest.runner import run_backtest
from orionx.core.config import AnalysisConfig, DecisionConfig
from orionx.core.errors import ConfigError
from orionx.core.models import Position, Side, Tick, TradeProposal
from orionx.decision.engine import DecisionEngine, EngineResult, StrategyEntry
from orionx.decision.registry import AVAILABLE, build_engine
from orionx.strategies.breakout import Breakout
from orionx.strategies.mean_reversion import MeanReversion
from tests.helpers import EURUSD, T0, synthetic_bars


class Fixed:
    """Stratégie factice : propose toujours le même sens (pour tester le moteur, pas la stratégie)."""

    timeframe, min_bars = "H1", 3

    def __init__(self, name: str, side: Side | None, conf: float = 0.6, symbol_filter: str | None = None) -> None:
        self.name, self.side, self.conf, self.only = name, side, conf, symbol_filter

    def generate(self, symbol, bars, tick, now, open_symbols):
        if self.side is None or (self.only and symbol != self.only):
            return None
        buy = self.side is Side.BUY
        e = tick.ask if buy else tick.bid
        sl, tp = (e - 0.0020, e + 0.0060) if buy else (e + 0.0020, e - 0.0060)
        return TradeProposal(f"{self.name}-{symbol}", symbol, self.side, sl, tp, self.conf, self.name, ("t",), now,
                             now + timedelta(seconds=60))  # fmt: skip


def st(regime: Regime) -> MarketState:
    return MarketState("EURUSD", T0, T0, regime, 1.0, 0.0, 0.5, 0.001, Session.LONDON)


def tk(symbol: str = "EURUSD") -> Tick:
    return Tick(symbol, 1.1000, 1.1001, T0)


def pos(side: Side = Side.BUY, sl: float | None = 1.0980, open_: float = 1.1000, symbol: str = "EURUSD") -> Position:
    return Position(1, symbol, side, 0.1, open_, sl, 1.12, 0.0, T0)


def engine(entries, **cfg) -> DecisionEngine:
    return DecisionEngine(entries, DecisionConfig(**cfg), AnalysisConfig())


class EngineEvaluateTests(unittest.TestCase):
    def ev(self, eng, state=None, positions=(), symbol="EURUSD"):
        return eng.evaluate(symbol, [], tk(symbol), T0, list(positions), state)

    def test_regime_gating(self) -> None:
        eng = engine([StrategyEntry(Fixed("a", Side.BUY), frozenset({Regime.TREND_UP}))])
        self.assertEqual(len(self.ev(eng, st(Regime.TREND_UP)).candidates), 1)
        r = self.ev(eng, st(Regime.RANGE))
        self.assertEqual((r.candidates, r.notes), ((), ("NO_STRATEGY_FOR_REGIME:range",)))
        self.assertEqual(self.ev(eng, st(Regime.UNKNOWN)).notes, ("REGIME_UNKNOWN",))
        self.assertEqual(len(self.ev(eng, None).candidates), 1)  # pas d'état : pas de filtrage

    def test_legacy_strategy_without_regimes_is_always_eligible(self) -> None:
        eng = engine([StrategyEntry(Fixed("a", Side.BUY))])
        self.assertEqual(len(self.ev(eng, st(Regime.UNKNOWN)).candidates), 1)

    def test_conflicting_signals_mean_no_trade(self) -> None:
        eng = engine([StrategyEntry(Fixed("a", Side.BUY)), StrategyEntry(Fixed("b", Side.SELL))])
        r = self.ev(eng)
        self.assertEqual(r.candidates, ())
        self.assertTrue(r.notes[0].startswith("CONFLICT:"))

    def test_consensus_raises_score_and_lists_strategies(self) -> None:
        eng = engine([StrategyEntry(Fixed("a", Side.BUY, 0.6)), StrategyEntry(Fixed("b", Side.BUY, 0.7))], consensus_bonus=0.05)
        (c,) = self.ev(eng).candidates
        self.assertAlmostEqual(c.score, 0.75)
        self.assertEqual(c.proposal.strategy, "b")  # la meilleure confiance
        self.assertEqual(set(c.strategies), {"a", "b"})
        self.assertAlmostEqual(c.rr, 3.0, places=3)

    def test_disabled_strategies_are_ignored(self) -> None:
        eng = engine([StrategyEntry(Fixed("a", Side.BUY)), StrategyEntry(Fixed("b", Side.SELL), enabled=False)])
        self.assertEqual(len(self.ev(eng).candidates), 1)
        with self.assertRaises(ValueError):
            engine([StrategyEntry(Fixed("a", Side.BUY), enabled=False)])

    def test_no_signal_gives_empty_result(self) -> None:
        r = self.ev(engine([StrategyEntry(Fixed("a", None))]))
        self.assertEqual((r.candidates, r.notes), ((), ()))

    def test_position_open_blocks_by_default(self) -> None:
        eng = engine([StrategyEntry(Fixed("a", Side.BUY))])
        self.assertEqual(self.ev(eng, positions=[pos()]).notes, ("POSITION_OPEN",))
        self.assertEqual(len(self.ev(eng, positions=[pos(symbol="GBPUSD")]).candidates), 1)  # autre symbole : libre

    def test_pyramiding_only_on_risk_free_same_side(self) -> None:
        eng = engine([StrategyEntry(Fixed("a", Side.BUY))], allow_pyramiding=True)
        self.assertEqual(self.ev(eng, positions=[pos(sl=1.0980)]).notes, ("PYRAMID_NOT_RISK_FREE",))  # stop sous l'entrée
        self.assertEqual(self.ev(eng, positions=[pos(sl=None)]).notes, ("PYRAMID_NOT_RISK_FREE",))
        self.assertEqual(len(self.ev(eng, positions=[pos(sl=1.1000)]).candidates), 1)  # stop au point d'entrée
        self.assertEqual(len(self.ev(eng, positions=[pos(sl=1.1010)]).candidates), 1)  # profit déjà verrouillé
        short = engine([StrategyEntry(Fixed("a", Side.SELL))], allow_pyramiding=True)
        self.assertEqual(self.ev(short, positions=[pos(sl=1.1000)]).notes, ("PYRAMID_OPPOSITE",))

    def test_timeframes_must_match(self) -> None:
        other = Fixed("x", Side.BUY)
        other.timeframe = "M15"
        with self.assertRaises(ValueError):
            engine([StrategyEntry(Fixed("a", Side.BUY)), StrategyEntry(other)])


class EngineRankTests(unittest.TestCase):
    def result(self, eng, symbol, side, conf):
        s = Fixed(f"s-{symbol}", side, conf)
        return eng.evaluate(symbol, [], tk(symbol), T0, [], None) if False else EngineResult(
            symbol, eng.evaluate(symbol, [], tk(symbol), T0, [], None).candidates
        )

    def build(self, **cfg):
        eng = engine([StrategyEntry(Fixed("e", Side.BUY, 0.8, "EURUSD")), StrategyEntry(Fixed("g", Side.BUY, 0.7, "GBPUSD")),
                      StrategyEntry(Fixed("j", Side.BUY, 0.6, "USDJPY")), StrategyEntry(Fixed("h", Side.SELL, 0.5, "EURGBP"))], **cfg)  # fmt: skip
        results = [eng.evaluate(s, [], tk(s), T0, [], None) for s in ("EURUSD", "GBPUSD", "USDJPY", "EURGBP")]
        return eng, results

    def test_same_currency_direction_is_deduplicated(self) -> None:
        eng, results = self.build()
        kept, dropped = eng.rank(results)
        names = [c.proposal.symbol for c in kept]
        self.assertEqual(names[0], "EURUSD")  # meilleur score d'abord
        self.assertNotIn("GBPUSD", names)  # acheter EURUSD ET GBPUSD = vendre deux fois le dollar
        self.assertEqual([c.proposal.symbol for c in dropped], ["GBPUSD"])
        self.assertIn("USDJPY", names)  # long USD : ne doublonne pas
        self.assertIn("EURGBP", names)  # vendre EUR : sens opposé à l'achat d'EURUSD

    def test_limit_can_be_raised(self) -> None:
        eng, results = self.build(max_per_currency_direction=2)
        kept, dropped = eng.rank(results)
        self.assertEqual(len(kept), 4)
        self.assertEqual(dropped, [])


class StrategyLibraryTests(unittest.TestCase):
    def signals(self, strat, n: int = 3000, seed: int = 11):
        bars, found = synthetic_bars(n, seed=seed), []
        for i in range(strat.min_bars + 5, len(bars)):
            hist = bars[i + 1 - strat.min_bars - 5 : i + 1]
            tick = Tick("EURUSD", hist[-1].close, hist[-1].close + 0.0001, T0)
            p = strat.generate("EURUSD", hist, tick, T0, set())
            if p:
                found.append((p, tick))
        return found

    def check_valid(self, found) -> None:
        for p, t in found:
            entry = t.ask if p.side is Side.BUY else t.bid
            self.assertTrue((p.sl < entry < p.tp) if p.side is Side.BUY else (p.tp < entry < p.sl))
            self.assertTrue(0 <= p.confidence <= 1)

    def test_breakout_produces_valid_signals_both_ways(self) -> None:
        found = self.signals(Breakout())
        self.assertGreater(len(found), 5)
        self.assertEqual({p.side for p, _ in found}, {Side.BUY, Side.SELL})
        self.check_valid(found)

    def test_mean_reversion_produces_valid_signals(self) -> None:
        found = self.signals(MeanReversion(min_rr=0.5), n=6000)
        self.assertGreater(len(found), 3)
        self.check_valid(found)

    def test_symbol_with_position_is_skipped_and_short_history_ignored(self) -> None:
        for strat in (Breakout(), MeanReversion()):
            bars = synthetic_bars(100)
            self.assertIsNone(strat.generate("EURUSD", bars[:10], tk(), T0, set()))
            self.assertIsNone(strat.generate("EURUSD", bars, tk(), T0, {"EURUSD"}))


class RegistryAndBacktestTests(unittest.TestCase):
    def test_build_engine(self) -> None:
        eng = build_engine(("trend_pullback", "mean_reversion"))
        self.assertEqual(len(eng.entries), 2)
        self.assertGreaterEqual(eng.min_bars, 180)  # assez de bougies pour calculer l'état du marché
        with self.assertRaises(ConfigError):
            build_engine(("magique",))
        self.assertIn("trend_pullback", AVAILABLE)

    def test_engine_runs_in_backtest_and_is_deterministic(self) -> None:
        bars = synthetic_bars(2500, seed=6)
        eng = build_engine(("trend_pullback", "breakout"))
        a = run_backtest(bars, eng.as_strategy(), EURUSD)
        b = run_backtest(bars, build_engine(("trend_pullback", "breakout")).as_strategy(), EURUSD)
        self.assertEqual((a.trades, a.net_profit), (b.trades, b.net_profit))
        self.assertGreaterEqual(a.trades, 0)

    def test_default_config_enables_only_trend_pullback(self) -> None:
        from orionx.core.config import AppConfig

        self.assertEqual(AppConfig().strategies, ("trend_pullback",))


if __name__ == "__main__":
    unittest.main()
