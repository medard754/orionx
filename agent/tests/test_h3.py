"""H-3 : détecteurs de chandeliers (famille a). Figures construites à la main : détectées, leur contraire non, rien ne regarde le futur."""

import random
import unittest
from datetime import UTC, datetime, timedelta

from orionx.backtest.h3 import CANDLESTICK_NAMES, detect_candlesticks, trend_context
from orionx.core.models import Bar

T0 = datetime(2026, 1, 5, tzinfo=UTC)
PAD = 0.00005


def bar(k: int, o: float, c: float, h: float | None = None, low: float | None = None) -> Bar:
    return Bar(T0 + timedelta(hours=k), o, max(o, c) + PAD if h is None else h, min(o, c) - PAD if low is None else low, c)


def trend(n: int, start: float, step: float) -> list[Bar]:
    bars, price = [], start
    for k in range(n):
        bars.append(bar(k, price, price + step))
        price += step
    return bars


def down() -> list[Bar]:
    return trend(70, 1.2000, -0.0005)  # dernière clôture 1,1650


def up() -> list[Bar]:
    return trend(70, 1.1000, 0.0005)  # dernière clôture 1,1350


def names_at_last(bars: list[Bar]) -> dict[str, int]:
    last = len(bars) - 1
    return {s.name: s.direction for s in detect_candlesticks(bars) if s.index == last}


class ContextTests(unittest.TestCase):
    def test_trend_context_needs_history_and_a_clear_slope(self) -> None:
        self.assertEqual(trend_context(down(), 69), -1)
        self.assertEqual(trend_context(up(), 69), 1)
        self.assertEqual(trend_context(down()[:30], 29), 0, "moins de 60 bougies : indéterminé")
        flat = [bar(k, 1.1, 1.1) for k in range(80)]
        self.assertEqual(trend_context(flat, 79), 0)


class SingleCandleTests(unittest.TestCase):
    def test_hammer_in_a_downtrend_and_hanging_man_in_an_uptrend(self) -> None:
        self.assertEqual(names_at_last(down() + [bar(70, 1.1650, 1.1652, h=1.1652, low=1.1630)]).get("marteau"), 1)
        self.assertEqual(names_at_last(up() + [bar(70, 1.1350, 1.1352, h=1.1352, low=1.1330)]).get("pendu"), -1)
        flat = [bar(k, 1.1, 1.1) for k in range(80)]
        self.assertNotIn("marteau", names_at_last(flat + [bar(80, 1.1, 1.1002, h=1.1002, low=1.0980)]), "sans contexte, pas de marteau")

    def test_inverted_hammer_and_shooting_star(self) -> None:
        self.assertEqual(names_at_last(down() + [bar(70, 1.1650, 1.1648, h=1.1670, low=1.1648)]).get("marteau_inverse"), 1)
        self.assertEqual(names_at_last(up() + [bar(70, 1.1350, 1.1348, h=1.1370, low=1.1348)]).get("etoile_filante"), -1)

    def test_doji_reads_as_a_reversal_of_the_context(self) -> None:
        self.assertEqual(names_at_last(down() + [bar(70, 1.1650, 1.1650, h=1.1660, low=1.1640)]).get("doji"), 1)
        self.assertEqual(names_at_last(up() + [bar(70, 1.1350, 1.1350, h=1.1360, low=1.1340)]).get("doji"), -1)
        self.assertNotIn("doji", names_at_last(down() + [bar(70, 1.1650, 1.1660)]))

    def test_marobozu_is_a_continuation_signal(self) -> None:
        self.assertEqual(names_at_last(down() + [bar(70, 1.1650, 1.1670, h=1.1670, low=1.1650)]).get("marobozu"), 1)
        self.assertEqual(names_at_last(down() + [bar(70, 1.1670, 1.1650, h=1.1670, low=1.1650)]).get("marobozu"), -1)
        self.assertNotIn("marobozu", names_at_last(down() + [bar(70, 1.1650, 1.1660, h=1.1675, low=1.1645)]))


class TwoCandleTests(unittest.TestCase):
    def test_engulfing_both_ways_and_a_non_engulfing_pair(self) -> None:
        base = down()
        self.assertEqual(names_at_last(base + [bar(70, 1.1660, 1.1650), bar(71, 1.1648, 1.1665)]).get("avalement"), 1)
        self.assertEqual(names_at_last(base + [bar(70, 1.1650, 1.1660), bar(71, 1.1662, 1.1645)]).get("avalement"), -1)
        self.assertNotIn(
            "avalement", names_at_last(base + [bar(70, 1.1660, 1.1650), bar(71, 1.1648, 1.1655)]), "la 2e bougie n'englobe pas la 1re"
        )

    def test_harami_needs_a_big_first_body_containing_the_second(self) -> None:
        base = down()
        big_red = bar(70, 1.1670, 1.1650, h=1.1671, low=1.1649)
        self.assertEqual(names_at_last(base + [big_red, bar(71, 1.1655, 1.1662)]).get("harami"), 1)
        self.assertNotIn("harami", names_at_last(base + [big_red, bar(71, 1.1655, 1.1675)]), "la 2e bougie déborde du corps de la 1re")
        small_first = bar(70, 1.1660, 1.1655, h=1.1675, low=1.1640)  # corps trop petit par rapport à l'amplitude
        self.assertNotIn("harami", names_at_last(base + [small_first, bar(71, 1.1657, 1.1658)]))


class ThreeCandleTests(unittest.TestCase):
    def test_morning_and_evening_star(self) -> None:
        base = down()
        morning = [
            bar(70, 1.1680, 1.1650, h=1.1681, low=1.1649),
            bar(71, 1.1642, 1.1644, h=1.1650, low=1.1638),
            bar(72, 1.1646, 1.1672, h=1.1673, low=1.1645),
        ]
        self.assertEqual(names_at_last(base + morning).get("etoile_matin"), 1)
        evening = [
            bar(70, 1.1650, 1.1680, h=1.1681, low=1.1649),
            bar(71, 1.1688, 1.1686, h=1.1698, low=1.1686),
            bar(72, 1.1684, 1.1658, h=1.1685, low=1.1657),
        ]
        self.assertEqual(names_at_last(base + evening).get("etoile_soir"), -1)
        no_gap = [
            bar(70, 1.1680, 1.1650, h=1.1681, low=1.1649),
            bar(71, 1.1656, 1.1658, h=1.1664, low=1.1652),
            bar(72, 1.1660, 1.1672, h=1.1673, low=1.1659),
        ]
        self.assertNotIn("etoile_matin", names_at_last(base + no_gap), "l'étoile doit être en dessous de la clôture précédente")

    def test_three_soldiers_and_three_crows(self) -> None:
        base = down()
        soldiers = [
            bar(70, 1.1650, 1.1670, h=1.1671, low=1.1649),
            bar(71, 1.1660, 1.1685, h=1.1686, low=1.1659),
            bar(72, 1.1675, 1.1700, h=1.1701, low=1.1674),
        ]
        self.assertEqual(names_at_last(base + soldiers).get("trois_soldats"), 1)
        crows = [
            bar(70, 1.1700, 1.1680, h=1.1701, low=1.1679),
            bar(71, 1.1690, 1.1665, h=1.1691, low=1.1664),
            bar(72, 1.1675, 1.1650, h=1.1676, low=1.1649),
        ]
        self.assertEqual(names_at_last(base + crows).get("trois_corbeaux"), -1)
        weak = [soldiers[0], soldiers[1], bar(72, 1.1675, 1.1678, h=1.1690, low=1.1670)]
        self.assertNotIn("trois_soldats", names_at_last(base + weak), "la 3e bougie n'a pas un grand corps")


class IntegrityTests(unittest.TestCase):
    def test_signals_never_look_into_the_future(self) -> None:
        rng = random.Random(1)
        bars, price = [], 1.10
        for k in range(300):
            o = price
            c = o + rng.uniform(-0.002, 0.002)
            bars.append(bar(k, o, c, h=max(o, c) + rng.uniform(0, 0.001), low=min(o, c) - rng.uniform(0, 0.001)))
            price = c
        full = detect_candlesticks(bars)
        for cut in (120, 200, 299):
            prefix = detect_candlesticks(bars[:cut])
            self.assertEqual(prefix, [s for s in full if s.index < cut], f"signaux différents sur le préfixe de {cut} bougies")
        self.assertGreater(len(full), 20, "du bruit aléatoire doit produire des figures")
        self.assertTrue({s.name for s in full} <= set(CANDLESTICK_NAMES))
        self.assertTrue(all(s.direction in (-1, 1) for s in full))

    def test_degenerate_bars_do_not_crash_or_signal(self) -> None:
        flat = [Bar(T0 + timedelta(hours=k), 1.1, 1.1, 1.1, 1.1) for k in range(80)]
        self.assertEqual(detect_candlesticks(flat), [])


if __name__ == "__main__":
    unittest.main()
