"""H-3 : pivots, figures, divergences, étranglement et Fibonacci sur des trajectoires construites à la main (aucune donnée de marché)."""

import random
import unittest
from datetime import UTC, datetime, timedelta

from orionx.backtest.h3 import (
    FIB_RATIOS,
    FIB_WITNESS_RATIOS,
    atr_series,
    detect_divergences,
    detect_figures,
    detect_squeezes,
    fib_entries,
    pivots,
    race_outcome,
    rsi_series,
    volatility_ratio,
)
from orionx.core.models import Bar

T0 = datetime(2026, 1, 5, tzinfo=UTC)
PAD = 0.0003


def path(points: list[float], step: float = 0.0005, warmup: int = 30) -> list[Bar]:
    """Trajectoire linéaire par morceaux à travers `points`, précédée de `warmup` bougies plates."""
    closes = [points[0]] * (warmup + 1)
    for p, q in zip(points, points[1:], strict=False):
        n = max(1, round(abs(q - p) / step))
        closes += [p + (q - p) * j / n for j in range(1, n + 1)]
    return [
        Bar(T0 + timedelta(hours=k), o, max(o, c) + PAD, min(o, c) - PAD, c)
        for k, (o, c) in enumerate(zip(closes, closes[1:], strict=False))
    ]


def names(signals) -> list[str]:  # type: ignore[no-untyped-def]
    return [s.name for s in signals]


class IndicatorTests(unittest.TestCase):
    def test_atr_and_rsi_need_history_and_stay_in_bounds(self) -> None:
        bars = path([1.10, 1.12, 1.10])
        atr = atr_series(bars)
        self.assertIsNone(atr[5])
        self.assertTrue(all(a is not None and a > 0 for a in atr[20:]))
        rsi = rsi_series([b.close for b in bars])
        self.assertIsNone(rsi[5])
        self.assertTrue(all(0 <= r <= 100 for r in rsi if r is not None))
        self.assertGreater(max(r for r in rsi if r is not None), 90, "une hausse continue donne un RSI très élevé")


class PivotTests(unittest.TestCase):
    def test_pivots_are_confirmed_after_the_extreme_never_before(self) -> None:
        bars = path([1.10, 1.11, 1.105, 1.112, 1.10])
        pv = pivots(bars)
        self.assertGreaterEqual(len(pv), 3)
        for p in pv:
            self.assertGreater(p.confirmed_at, p.index, "un pivot n'est connu qu'après son extrême")
        self.assertEqual([p.kind for p in pv], ["H", "L", "H", "L"][: len(pv)] if pv[0].kind == "H" else ["L", "H", "L", "H"][: len(pv)])

    def test_pivots_never_look_into_the_future(self) -> None:
        rng = random.Random(3)
        bars, price = [], 1.10
        for k in range(400):
            o, c = price, price + rng.uniform(-0.002, 0.002)
            bars.append(Bar(T0 + timedelta(hours=k), o, max(o, c) + rng.uniform(0, 0.001), min(o, c) - rng.uniform(0, 0.001), c))
            price = c
        full = pivots(bars)
        for cut in (150, 250, 399):
            self.assertEqual(pivots(bars[:cut]), [p for p in full if p.confirmed_at < cut], f"préfixe de {cut}")
        self.assertGreater(len(full), 3)


class FigureTests(unittest.TestCase):
    def test_double_top_and_double_bottom_are_detected_after_the_neckline_break(self) -> None:
        top = path([1.1000, 1.1100, 1.1060, 1.1098, 1.1000])
        found = [s for s in detect_figures(top) if s.name == "double_sommet"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].direction, -1)
        self.assertLess(top[found[0].index].close, 1.1057, "le signal est la clôture sous la ligne de cou")
        bottom = path([1.1100, 1.1000, 1.1040, 1.1002, 1.1100])
        found = [s for s in detect_figures(bottom) if s.name == "double_creux"]
        self.assertEqual((len(found), found[0].direction), (1, 1))

    def test_a_second_top_clearly_higher_is_not_a_double_top(self) -> None:
        self.assertNotIn("double_sommet", names(detect_figures(path([1.1000, 1.1100, 1.1060, 1.1160, 1.1000]))))

    def test_an_invalidated_figure_gives_no_signal(self) -> None:
        # le prix repart au-dessus des deux sommets avant de casser la ligne de cou
        self.assertNotIn("double_sommet", names(detect_figures(path([1.1000, 1.1100, 1.1060, 1.1098, 1.1140, 1.1000]))))

    def test_head_and_shoulders_and_its_inverse(self) -> None:
        hs = path([1.1000, 1.1060, 1.1020, 1.1100, 1.1020, 1.1062, 1.1000])
        found = [s for s in detect_figures(hs) if s.name == "tete_epaules"]
        self.assertEqual((len(found), found[0].direction), (1, -1))
        inv = path([1.1100, 1.1040, 1.1080, 1.1000, 1.1080, 1.1038, 1.1100])
        found = [s for s in detect_figures(inv) if s.name == "tete_epaules_inverse"]
        self.assertEqual((len(found), found[0].direction), (1, 1))

    def test_figure_signals_never_look_into_the_future(self) -> None:
        bars = path([1.1000, 1.1100, 1.1060, 1.1098, 1.1000, 1.0950, 1.1040])
        full = detect_figures(bars)
        self.assertTrue(full)
        for cut in range(60, len(bars), 15):
            self.assertEqual(detect_figures(bars[:cut]), [s for s in full if s.index < cut], f"préfixe de {cut}")


class DivergenceTests(unittest.TestCase):
    def test_bullish_regular_divergence(self) -> None:
        # chute rapide jusqu'à 1,0800 (RSI écrasé), rebond, puis glissade lente et heurtée jusqu'à un creux plus bas (RSI moins bas)
        fast = path([1.1000, 1.0800, 1.0880], step=0.0010)
        closes = [b.close for b in fast]
        price = closes[-1]
        for k in range(120):
            price += -0.0003 if k % 2 == 0 else 0.0001
            closes.append(price)
        closes += [price + 0.0006 * j for j in range(1, 20)]  # rebond final qui confirme le creux
        bars = [
            Bar(T0 + timedelta(hours=k), o, max(o, c) + 0.0002, min(o, c) - 0.0002, c)
            for k, (o, c) in enumerate(zip(closes, closes[1:], strict=False))
        ]
        found = names(detect_divergences(bars))
        self.assertIn("divergence_directe_haussiere", found)

    def test_bearish_regular_divergence_is_the_mirror(self) -> None:
        fast = path([1.1000, 1.1200, 1.1120], step=0.0010)
        closes = [b.close for b in fast]
        price = closes[-1]
        for k in range(120):
            price += 0.0003 if k % 2 == 0 else -0.0001
            closes.append(price)
        closes += [price - 0.0006 * j for j in range(1, 20)]
        bars = [
            Bar(T0 + timedelta(hours=k), o, max(o, c) + 0.0002, min(o, c) - 0.0002, c)
            for k, (o, c) in enumerate(zip(closes, closes[1:], strict=False))
        ]
        self.assertIn("divergence_directe_baissiere", names(detect_divergences(bars)))

    def test_no_divergence_on_a_flat_series(self) -> None:
        self.assertEqual(detect_divergences(path([1.10, 1.10], warmup=200)), [])


class SqueezeTests(unittest.TestCase):
    @staticmethod
    def noisy(n: int, scale: float, seed: int, start: float = 1.10) -> list[Bar]:
        rng, bars, price = random.Random(seed), [], start
        for k in range(n):
            o, c = price, price + rng.uniform(-scale, scale)
            bars.append(Bar(T0 + timedelta(hours=k), o, max(o, c) + scale / 2, min(o, c) - scale / 2, c))
            price = c
        return bars

    def test_a_quiet_stretch_after_a_volatile_one_is_a_squeeze_and_volatility_returns(self) -> None:
        bars = self.noisy(300, 0.002, 1) + self.noisy(40, 0.00005, 2, start=1.10)
        signals = detect_squeezes(bars)
        self.assertTrue(any(s.index >= 320 for s in signals), "le calme après l'agitation doit être détecté")
        self.assertTrue(all(s.direction == 0 for s in signals), "non directionnel")
        expanded = bars + self.noisy(40, 0.004, 3, start=bars[-1].close)
        idx = max(s.index for s in signals if s.index < len(bars))
        ratio = volatility_ratio(expanded, idx)
        self.assertIsNotNone(ratio)
        self.assertGreater(ratio or 0, 1.2, "la volatilité repart après l'étranglement dans cette série construite")

    def test_with_constant_volatility_squeezes_stay_near_ten_percent(self) -> None:
        signals = detect_squeezes(self.noisy(1500, 0.001, 7))
        rate = len(signals) / (1500 - 270)
        self.assertTrue(0.03 < rate < 0.20, rate)

    def test_squeezes_never_look_into_the_future(self) -> None:
        bars = self.noisy(500, 0.001, 4)
        full = detect_squeezes(bars)
        for cut in (300, 400):
            self.assertEqual(detect_squeezes(bars[:cut]), [s for s in full if s.index < cut])

    def test_volatility_ratio_uses_only_the_past_for_the_baseline(self) -> None:
        bars = self.noisy(400, 0.001, 5)
        self.assertIsNone(volatility_ratio(bars, 100), "moins de 250 bougies d'historique")
        self.assertIsNone(volatility_ratio(bars, 395), "pas assez de bougies après")
        self.assertIsNotNone(volatility_ratio(bars, 300))


class FibonacciTests(unittest.TestCase):
    def test_a_pullback_to_the_382_level_is_an_entry_and_the_race_can_be_won_or_lost(self) -> None:
        # jambe 1,1000 -> 1,1100, repli jusqu'à environ 1,1062 (38,2 %), puis reprise
        win = path([1.1000, 1.1100, 1.1060, 1.1130])
        entries = [e for e in fib_entries(win, FIB_RATIOS) if e.ratio == 0.382 and e.direction == 1]
        self.assertEqual(len(entries), 1, "une seule entrée par niveau et par jambe")
        e = entries[0]
        self.assertAlmostEqual(e.price, 1.1100 - 0.382 * (1.1100 - 1.1000 + PAD), delta=0.0008)
        self.assertEqual(race_outcome(win, e), 1)
        lose = path([1.1000, 1.1100, 1.1060, 1.1000])
        e2 = [x for x in fib_entries(lose, FIB_RATIOS) if x.ratio == 0.382 and x.direction == 1][0]
        self.assertEqual(race_outcome(lose, e2), -1)

    def test_deeper_levels_trigger_in_order_and_witness_levels_are_separate(self) -> None:
        bars = path([1.1000, 1.1100, 1.1030, 1.1120])
        up = [e.ratio for e in fib_entries(bars, FIB_RATIOS) if e.direction == 1]
        self.assertEqual(up, sorted(up), "du repli le moins profond au plus profond")
        self.assertTrue({0.382, 0.5, 0.618} <= set(up))
        witness = {e.ratio for e in fib_entries(bars, FIB_WITNESS_RATIOS)}
        self.assertTrue(witness <= set(FIB_WITNESS_RATIOS))
        self.assertFalse(witness & set(FIB_RATIOS), "les niveaux témoins ne sont jamais des niveaux de Fibonacci")

    def test_a_broken_leg_extreme_cancels_the_pending_levels(self) -> None:
        bars = path([1.1000, 1.1100, 1.1060, 1.1112, 1.1000])  # repli léger, puis le sommet est dépassé, puis chute
        up = {e.ratio for e in fib_entries(bars, FIB_RATIOS) if e.direction == 1}
        self.assertEqual(up, {0.382}, "les niveaux 50 et 61,8 % de la jambe haussière sont annulés quand son sommet est dépassé")

    def test_downward_leg_mirrors_the_upward_one(self) -> None:
        bars = path([1.1100, 1.1000, 1.1040, 1.0970])
        e = [x for x in fib_entries(bars, FIB_RATIOS) if x.ratio == 0.382 and x.direction == -1][0]
        self.assertEqual(race_outcome(bars, e), 1)

    def test_entries_never_look_into_the_future(self) -> None:
        bars = path([1.1000, 1.1100, 1.1060, 1.1130, 1.1090, 1.1180])
        full = fib_entries(bars, FIB_RATIOS)
        for cut in range(60, len(bars), 12):
            part = fib_entries(bars[:cut], FIB_RATIOS)
            self.assertEqual(part, [e for e in full if e.index < cut], f"préfixe de {cut}")


if __name__ == "__main__":
    unittest.main()
