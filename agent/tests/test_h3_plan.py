"""H-3f : parabolique SAR et « plan de trading » du cours (D1) : causalité, stop respecté, hasard refusé, tendance acceptée."""

import random
import unittest
from datetime import UTC, datetime

from orionx.backtest.h3 import atr_series, sar_series
from orionx.backtest.h3_bench import (
    PlanTrade,
    build_symbol_data,
    judge_plan,
    max_drawdown_pct,
    plan_trades,
    profit_factor,
)
from orionx.core.models import Bar

DAY = 86_400
T0 = int(datetime(2010, 1, 4, tzinfo=UTC).timestamp())


def daily_rows(n: int, drift: float, seed: int, segment: int = 250) -> list[tuple]:
    """Marche aléatoire par segments de `segment` jours à tendance alternée (|drift| en écarts-types par jour)."""
    rng = random.Random(seed)
    price, rows = 1.1, []
    for k in range(n):
        d = drift * (1 if (k // segment) % 2 == 0 else -1) * 0.004
        o = price
        c = o + d + rng.gauss(0, 0.004)
        hi = max(o, c) + abs(rng.gauss(0, 0.0015))
        lo = min(o, c) - abs(rng.gauss(0, 0.0015))
        rows.append((T0 + k * DAY, o, hi, lo, c, 100, 3))
        price = c
    return rows


def bars_of(rows: list[tuple]) -> list[Bar]:
    return [Bar(datetime.fromtimestamp(r[0], UTC), r[1], r[2], r[3], r[4], float(r[5])) for r in rows]


class SarTests(unittest.TestCase):
    def test_sar_does_not_look_ahead(self) -> None:
        bars = bars_of(daily_rows(400, 1.0, 1))
        full = sar_series(bars)
        for cut in (60, 150, 333):
            self.assertEqual(sar_series(bars[:cut]), full[:cut], "ajouter des bougies futures ne change pas le passé")

    def test_sar_follows_an_uptrend_then_flips_after_a_reversal(self) -> None:
        rows = [(T0 + k * DAY, 1 + 0.01 * k, 1.005 + 0.01 * k, 0.995 + 0.01 * k, 1.002 + 0.01 * k, 1, 1) for k in range(30)]
        rows += [(T0 + (30 + k) * DAY, 1.3 - 0.02 * k, 1.302 - 0.02 * k, 1.28 - 0.02 * k, 1.285 - 0.02 * k, 1, 1) for k in range(30)]
        states = [s for s in sar_series(bars_of(rows)) if s is not None]
        self.assertTrue(all(s.trend == 1 for s in states[:25]))
        self.assertEqual(states[-1].trend, -1)
        self.assertTrue(any(s.flipped for s in states))


class PlanTests(unittest.TestCase):
    def test_trades_are_in_the_direction_of_the_trend_and_losses_stay_near_one_r(self) -> None:
        sd = build_symbol_data("EURUSD", daily_rows(2500, 1.0, 3))
        trades = plan_trades(sd)
        self.assertGreater(len(trades), 8)
        for t in trades:
            self.assertGreater(t.exit, t.entry)
        self.assertGreater(
            min(t.r for t in trades), -3.0, "le SAR est le stop : une perte reste de l'ordre de 1 R, plus les coûts et les écarts"
        )

    def test_no_look_ahead_in_the_plan(self) -> None:
        rows = daily_rows(1500, 1.0, 4)
        full = plan_trades(build_symbol_data("EURUSD", rows))
        head = plan_trades(build_symbol_data("EURUSD", rows[:900]))
        closed = [t for t in full if t.exit < 899]
        self.assertEqual([(t.entry, t.exit, t.direction) for t in head if t.exit < 899], [(t.entry, t.exit, t.direction) for t in closed])

    def test_profit_factor_and_drawdown(self) -> None:
        self.assertAlmostEqual(profit_factor([2.0, -1.0, 1.0, -1.0]), 1.5)
        trades = [PlanTrade("X", 1, 0, 1, k, 1.0, r) for k, r in enumerate([-1.0] * 10)]
        self.assertAlmostEqual(max_drawdown_pct(trades), (1 - 0.995**10) * 100, places=6)


class JudgePlanTests(unittest.TestCase):
    SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD")

    def data(self, drift: float, seed: int) -> dict:
        return {s: build_symbol_data(s, daily_rows(4000, drift, seed + i)) for i, s in enumerate(self.SYMBOLS)}

    def span(self, data: dict) -> tuple[int, int]:
        return T0, T0 + 4000 * DAY

    def test_a_pure_random_walk_is_refused(self) -> None:
        for seed in (10, 20, 30):
            d = self.data(0.0, seed)
            v = judge_plan(d, *self.span(d), draws=40, seed=0)
            self.assertFalse(v.passed, f"seed {seed}: {v.checks}")

    def test_a_market_that_really_trends_is_accepted_on_the_statistics(self) -> None:
        d = self.data(1.0, 50)
        v = judge_plan(d, *self.span(d), draws=40, seed=0)
        self.assertGreaterEqual(v.trades, 30)
        self.assertTrue(v.checks["pf_superieur_1_2_dans_3_blocs_sur_4"], v.block_pf)
        self.assertTrue(v.checks["meilleur_que_97_5_pct_des_tirages"], v.random_better)

    def test_atr_available(self) -> None:
        self.assertTrue(any(a for a in atr_series(bars_of(daily_rows(50, 0.0, 1)))))


if __name__ == "__main__":
    unittest.main()
