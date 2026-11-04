"""H-3 : banc d'essai. Coûts exacts à la main, tirages appariés, test du maximum : le hasard ne passe pas, un avantage planté est vu."""

import random
import unittest

from orionx.backtest.h3_bench import (
    FamilyTable,
    SymbolData,
    build_symbol_data,
    build_table,
    commission_in_price,
    family_max_test,
    forward_net,
    make_ref,
    point_size,
)
from orionx.backtest.h3_freeze import Row

HOUR = 3600
T0 = 1_700_000_000 - (1_700_000_000 % 86400)  # minuit pile (heure serveur)


def walk(n: int, seed: int, spread: int = 10, vol: float = 0.0005) -> list[Row]:
    rng, price, rows = random.Random(seed), 1.10, []
    for k in range(n):
        o = price
        c = o + rng.gauss(0, vol)
        h, lo = max(o, c) + abs(rng.gauss(0, vol / 2)), min(o, c) - abs(rng.gauss(0, vol / 2))
        rows.append((T0 + k * HOUR, o, h, lo, c, 100, spread))
        price = c
    return rows


def flat(n: int, spread: int, symbol_price: float = 1.10) -> list[Row]:
    return [(T0 + k * HOUR, symbol_price, symbol_price + 0.0002, symbol_price - 0.0002, symbol_price, 100, spread) for k in range(n)]


class CostTests(unittest.TestCase):
    def test_point_and_commission_conversions(self) -> None:
        self.assertEqual(point_size("EURUSD"), 0.00001)
        self.assertEqual(point_size("USDJPY"), 0.001)
        self.assertAlmostEqual(commission_in_price("GBPUSD", 1.27), 0.00007)
        self.assertAlmostEqual(commission_in_price("USDJPY", 150.0), 0.0105, msg="7 USD / 100 000 unités, converti en yen")
        self.assertAlmostEqual(commission_in_price("USDCAD", 1.40), 0.000098)

    def test_a_flat_market_costs_exactly_spread_plus_commission(self) -> None:
        sd = build_symbol_data("EURUSD", flat(80, spread=20))
        atr = sd.atr[40]
        assert atr is not None
        expected = -(20 * 0.00001 + 0.00007) / atr
        self.assertAlmostEqual(forward_net(sd, 40, 1, 4) or 0, expected, places=9)
        self.assertAlmostEqual(
            forward_net(sd, 40, -1, 4) or 0, expected, places=9, msg="la vente paie le spread à la sortie : même coût ici"
        )

    def test_long_and_short_are_mirror_images_before_costs(self) -> None:
        rows = flat(60, spread=0)
        rows = rows[:50] + [
            (r[0], r[1] + 0.001 * k, r[2] + 0.001 * k, r[3] + 0.001 * k, r[4] + 0.001 * k, r[5], r[6]) for k, r in enumerate(rows[50:], 1)
        ]
        sd = build_symbol_data("EURUSD", rows)
        up, down = forward_net(sd, 48, 1, 4), forward_net(sd, 48, -1, 4)
        assert up is not None and down is not None
        self.assertGreater(up, 0)
        self.assertLess(down, 0)
        commission = 0.00007 / (sd.atr[48] or 1)
        self.assertAlmostEqual(up + commission, -(down + commission), places=9)

    def test_missing_data_gives_none(self) -> None:
        sd = build_symbol_data("EURUSD", flat(60, 5))
        self.assertIsNone(forward_net(sd, 5, 1, 4), "ATR pas encore disponible")
        self.assertIsNone(forward_net(sd, 58, 1, 4), "pas assez de bougies après le signal")
        self.assertIsNone(forward_net(sd, 40, 0, 4), "pas de sens, pas de trade")

    def test_atr_terciles_split_the_series_in_three_groups(self) -> None:
        sd = build_symbol_data("EURUSD", walk(3000, 1))
        counts = [sd.tercile.count(t) for t in (0, 1, 2)]
        self.assertTrue(all(c > 600 for c in counts), counts)


def tables_for(sd: SymbolData) -> dict[str, FamilyTable]:
    return {sd.symbol: build_table(sd, (12,))}


class FamilyTestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sd = build_symbol_data("EURUSD", walk(6000, 7))
        self.tables = tables_for(self.sd)

    def random_signals(self, n: int, seed: int) -> list:
        rng = random.Random(seed)
        idx = [i for i in range(60, 5900)]
        return [make_ref(self.sd, i, rng.choice((1, -1))) for i in rng.sample(idx, n)]

    def test_signals_picked_without_looking_at_the_future_show_no_edge(self) -> None:
        variants = {(f"regle{k}", 12): self.random_signals(300, k) for k in range(6)}
        result = family_max_test(variants, self.tables, permutations=300, seed=1)
        self.assertEqual(result.considered, 6)
        self.assertGreater(result.p_value, 0.05, "du hasard pur ne doit pas passer le seuil")

    def test_an_edge_planted_in_the_data_is_detected(self) -> None:
        net = self.tables["EURUSD"].net[(1, 12)]
        winners = [i for i in range(60, 5900) if (net[i] or -1) > 0.3][:300]  # signaux choisis EN regardant le futur : avantage artificiel
        variants = {("regle_truquee", 12): [make_ref(self.sd, i, 1) for i in winners]}
        result = family_max_test(variants, self.tables, permutations=300, seed=2)
        self.assertGreater(result.best.mean_net if result.best else -1, 0.3)
        self.assertLessEqual(result.p_value, 0.01)

    def test_variants_with_too_few_signals_are_excluded(self) -> None:
        variants = {("rare", 12): self.random_signals(40, 3)}
        result = family_max_test(variants, self.tables, permutations=50, min_signals=100)
        self.assertEqual((result.best, result.p_value, result.considered), (None, 1.0, 0))

    def test_same_seed_same_answer(self) -> None:
        variants = {("a", 12): self.random_signals(200, 5), ("b", 12): self.random_signals(200, 6)}
        first = family_max_test(variants, self.tables, permutations=100, seed=9)
        second = family_max_test(variants, self.tables, permutations=100, seed=9)
        self.assertEqual(first, second)

    def test_random_dates_come_from_the_same_hour_and_volatility_class(self) -> None:
        ref = make_ref(self.sd, 600, 1)
        pool = self.tables["EURUSD"].strata[ref.stratum]
        self.assertTrue(all(self.sd.hour[i] == ref.stratum[0] and self.sd.tercile[i] == ref.stratum[1] for i in pool))
        self.assertIn(600, pool)


if __name__ == "__main__":
    unittest.main()
