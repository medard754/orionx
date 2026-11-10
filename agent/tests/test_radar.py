"""Radar des signaux : il doit dire EXACTEMENT la même chose que les stratégies (jamais « prêt » sans signal, ni l'inverse)."""

import random
import unittest
from datetime import UTC, datetime, timedelta

from orionx.core.models import Bar, Tick
from orionx.decision.radar import breakout_radar, build_radar, mean_reversion_radar, trend_pullback_radar
from orionx.strategies.breakout import Breakout
from orionx.strategies.mean_reversion import MeanReversion
from orionx.strategies.trend_pullback import TrendPullback

T0 = datetime(2026, 1, 5, tzinfo=UTC)


def walk(n: int, seed: int, drift: float = 0.0) -> list[Bar]:
    rng = random.Random(seed)
    price, out = 1.1, []
    for k in range(n):
        o = price
        c = o + drift + rng.gauss(0, 0.0008)
        out.append(Bar(T0 + timedelta(hours=k), o, max(o, c) + abs(rng.gauss(0, 0.0003)), min(o, c) - abs(rng.gauss(0, 0.0003)), c, 100.0))
        price = c
    return out


def tick_for(bars: list[Bar]) -> Tick:
    p = bars[-1].close
    return Tick("EURUSD", p - 0.00005, p + 0.00005, bars[-1].time)


def all_ok(checks: list[dict]) -> bool:
    return all(c["ok"] for c in checks)


class RadarMatchesStrategyTests(unittest.TestCase):
    def sweep(self, strategy, radar) -> int:  # type: ignore[no-untyped-def]
        proposals = 0
        for seed in range(6):
            bars = walk(900, seed, drift=0.00008 * (1 if seed % 2 else -1))
            for end in range(strategy.min_bars + 5, len(bars), 3):
                window = bars[:end]
                proposal = strategy.generate("EURUSD", window, tick_for(window), T0, set())
                side, checks = radar(window)
                self.assertEqual(proposal is not None, all_ok(checks), f"seed {seed}, bougie {end} : radar et stratégie divergent")
                if proposal is not None:
                    proposals += 1
                    self.assertEqual(side, "achat" if proposal.side.value == "buy" else "vente")
        return proposals

    def test_trend_pullback(self) -> None:
        self.assertGreater(self.sweep(TrendPullback(), trend_pullback_radar), 0, "le balayage doit contenir de vrais signaux")

    def test_mean_reversion(self) -> None:
        self.assertGreater(self.sweep(MeanReversion(), mean_reversion_radar), 0)

    def test_breakout(self) -> None:
        self.assertGreater(self.sweep(Breakout(), breakout_radar), 0)

    def test_not_enough_history_is_reported_not_crashed(self) -> None:
        side, checks = trend_pullback_radar(walk(10, 1))
        self.assertEqual((side, all_ok(checks)), ("-", False))


class BuildRadarTests(unittest.TestCase):
    def test_a_strategy_outside_its_regime_is_never_ready(self) -> None:
        bars = walk(300, 2)
        rows = build_radar("EURUSD", bars, "range", [("trend_pullback", {"trend_up", "trend_down"}), ("mean_reversion", {"range"})])
        by = {r["strategy"]: r for r in rows}
        self.assertFalse(by["trend_pullback"]["applies"])
        self.assertFalse(by["trend_pullback"]["ready"])
        self.assertIn("range", by["trend_pullback"]["regime_note"])
        self.assertTrue(by["mean_reversion"]["applies"])

    def test_unknown_strategy_is_ignored(self) -> None:
        self.assertEqual(build_radar("EURUSD", walk(300, 3), None, [("inconnue", None)]), [])


if __name__ == "__main__":
    unittest.main()
