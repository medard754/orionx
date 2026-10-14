import unittest
from datetime import timedelta

from orionx.backtest.runner import run_backtest
from orionx.core.models import Bar, Side, Tick
from orionx.strategies.indicators import atr_last, ema_series, rsi_last
from orionx.strategies.trend_pullback import TrendPullback
from tests.helpers import EURUSD, T0, synthetic_bars


class IndicatorTests(unittest.TestCase):
    def test_ema_of_constant(self) -> None:
        self.assertTrue(all(abs(x - 5.0) < 1e-12 for x in ema_series([5.0] * 30, 10)))

    def test_rsi_extremes(self) -> None:
        self.assertEqual(rsi_last([float(i) for i in range(40)]), 100.0)
        self.assertAlmostEqual(rsi_last([float(40 - i) for i in range(40)]), 0.0)
        self.assertIsNone(rsi_last([1.0] * 5))

    def test_atr_of_constant_range(self) -> None:
        bars = [Bar(T0 + timedelta(hours=i), 1.0, 1.01, 0.99, 1.0) for i in range(40)]
        self.assertAlmostEqual(atr_last(bars), 0.02)
        self.assertIsNone(atr_last(bars[:5]))


class StrategyTests(unittest.TestCase):
    def test_not_enough_bars(self) -> None:
        s = TrendPullback()
        bars = synthetic_bars(30)
        tick = Tick("EURUSD", 1.1, 1.1001, T0)
        self.assertIsNone(s.generate("EURUSD", bars, tick, T0, set()))

    def test_signals_are_well_formed_and_symbol_with_position_is_skipped(self) -> None:
        s, found = TrendPullback(), []
        bars = synthetic_bars(2500, seed=3)
        for i in range(s.min_bars, len(bars)):
            hist = bars[max(0, i + 1 - s.min_bars - 5) : i + 1]
            tick = Tick("EURUSD", hist[-1].close, hist[-1].close + 0.0001, T0)
            p = s.generate("EURUSD", hist, tick, T0, set())
            if p:
                found.append((p, tick))
                self.assertIsNone(s.generate("EURUSD", hist, tick, T0, {"EURUSD"}))
        self.assertGreater(len(found), 5, "la stratégie ne produit presque aucun signal")
        self.assertTrue({p.side for p, _ in found} == {Side.BUY, Side.SELL})
        for p, t in found:
            entry = t.ask if p.side is Side.BUY else t.bid
            if p.side is Side.BUY:
                self.assertTrue(p.sl < entry < p.tp)
            else:
                self.assertTrue(p.tp < entry < p.sl)
            self.assertAlmostEqual(abs(p.tp - entry) / abs(entry - p.sl), 2.0, places=6)
            self.assertTrue(0.5 <= p.confidence <= 0.9)


class BacktestTests(unittest.TestCase):
    def run_bt(self, **kw):
        return run_backtest(synthetic_bars(3000, seed=5), TrendPullback(), EURUSD, **kw)

    def test_deterministic_and_consistent(self) -> None:
        a, b = self.run_bt(), self.run_bt()
        self.assertEqual((a.trades, a.net_profit), (b.trades, b.net_profit))
        self.assertGreater(a.trades, 5)
        self.assertAlmostEqual(a.final_equity, 10_000 + sum(t.profit for t in a.log), places=6)
        self.assertAlmostEqual(a.net_profit, a.final_equity - 10_000, places=6)
        self.assertLessEqual(a.wins, a.trades)
        self.assertGreaterEqual(a.max_drawdown_pct, 0)

    def test_costs_reduce_results(self) -> None:
        cheap, costly = self.run_bt(spread_points=1), self.run_bt(spread_points=30, commission_per_lot=10)
        self.assertLess(costly.net_profit, cheap.net_profit)

    def test_each_loss_is_bounded_by_the_risk_budget(self) -> None:
        r = self.run_bt(spread_points=10)
        for t in r.log:
            if t.reason == "stop":
                # perte <= risque cible (0,5 % du capital) + marge pour gap/spread
                self.assertGreater(t.profit, -10_000 * 0.02)

    def test_no_lookahead(self) -> None:
        bars, seen = synthetic_bars(400, seed=2), []

        class Spy:
            name, timeframe, min_bars = "spy", "H1", 60

            def generate(self, symbol, hist, tick, now, open_symbols):
                seen.append((len(hist), hist[-1].time, now))
                return None

        run_backtest(bars, Spy(), EURUSD)
        self.assertTrue(all(h <= Spy.min_bars + 5 for h, _, _ in seen))
        self.assertTrue(all(t == now for _, t, now in seen))  # la dernière bougie vue est celle de l'instant
        times = [t for _, t, _ in seen]
        self.assertEqual(times, sorted(times))
        self.assertLess(times[-1], bars[-1].time)  # jamais la toute dernière bougie


if __name__ == "__main__":
    unittest.main()


class WalkForwardTests(unittest.TestCase):
    def test_split_folds_covers_everything_without_overlap(self) -> None:
        from orionx.backtest.walkforward import split_folds

        bars = synthetic_bars(1003)
        folds = split_folds(bars, 4)
        self.assertEqual([len(f) for f in folds], [250, 250, 250, 253])
        self.assertEqual([b for f in folds for b in f], bars)
        with self.assertRaises(ValueError):
            split_folds(bars, 0)

    def test_random_baseline_is_deterministic_and_comparable(self) -> None:
        from orionx.backtest.walkforward import random_reference

        bars = synthetic_bars(2000, seed=4)
        a = random_reference(bars, EURUSD, "EURUSD", 0.0, runs=5)
        b = random_reference(bars, EURUSD, "EURUSD", 0.0, runs=5)
        self.assertEqual(a, b)
        self.assertGreaterEqual(a.share_worse, 0.0)
        self.assertLessEqual(a.share_worse, 1.0)
        self.assertEqual(a.runs, 5)

    def test_random_entry_proposals_are_valid(self) -> None:
        from orionx.strategies.baseline import RandomEntry

        s, bars, n = RandomEntry(seed=1, prob=0.5), synthetic_bars(400, seed=2), 0
        for i in range(s.min_bars, len(bars)):
            hist = bars[: i + 1]
            tick = Tick("EURUSD", hist[-1].close, hist[-1].close + 0.0001, T0)
            p = s.generate("EURUSD", hist, tick, T0, set())
            if p:
                n += 1
                entry = tick.ask if p.side is Side.BUY else tick.bid
                self.assertTrue((p.sl < entry < p.tp) if p.side is Side.BUY else (p.tp < entry < p.sl))
        self.assertGreater(n, 20)
