"""H-3 : banc d'essai. Coûts exacts à la main, tirages appariés, test du maximum : le hasard ne passe pas, un avantage planté est vu."""

import random
import unittest

from orionx.backtest.h3 import detect_candlesticks
from orionx.backtest.h3_bench import (
    FamilyTable,
    SymbolData,
    block_of,
    build_symbol_data,
    build_table,
    commission_in_price,
    consistency,
    family_max_test,
    forward_net,
    judge_directional,
    make_ref,
    point_size,
    split_dev_reserve,
    variants_from_detector,
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


class SplitTests(unittest.TestCase):
    def test_reserve_is_the_last_183_days_and_nothing_overlaps(self) -> None:
        rows = [(T0 + k * 86400, 1.1, 1.1, 1.1, 1.1, 1, 1) for k in range(400)]
        dev, reserve = split_dev_reserve(rows)
        self.assertEqual(len(dev) + len(reserve), 400)
        self.assertEqual(len(reserve), 184, "les 183 derniers jours, bornes incluses")
        self.assertLess(dev[-1][0], reserve[0][0])
        self.assertEqual(split_dev_reserve([]), ([], []))

    def test_blocks_have_equal_duration_and_cover_everything(self) -> None:
        self.assertEqual([block_of(t, 0, 400) for t in (0, 99, 100, 250, 399, 400)], [0, 0, 1, 2, 3, 3])


def planted(symbols: tuple[str, ...], seed: int, n: int = 6000, count: int = 300):  # type: ignore[no-untyped-def]
    sds, tables, refs = {}, {}, []
    for k, sym in enumerate(symbols):
        sd = build_symbol_data(sym, walk(n, seed + k))
        sds[sym], tables[sym] = sd, build_table(sd, (12,))
        net = tables[sym].net[(1, 12)]
        winners = [i for i in range(60, n - 100) if (net[i] or -1) > 0.3]
        refs += [
            make_ref(sd, i, 1) for i in sorted(random.Random(seed + k).sample(winners, min(count, len(winners))))
        ]  # sur toute la période
    return sds, tables, refs


class JudgeTests(unittest.TestCase):
    SYMBOLS = ("GBPUSD", "USDJPY", "AUDUSD", "USDCAD")

    def test_a_planted_edge_passes_every_criterion(self) -> None:
        sds, tables, refs = planted(self.SYMBOLS, 10)
        verdict = judge_directional("test", {("truquee", 12): refs}, sds, tables, decision_symbols=self.SYMBOLS, permutations=200, seed=1)
        self.assertTrue(verdict.passed, verdict.checks)
        self.assertGreaterEqual(verdict.consistency.positive_symbols, 3, "le « USDJPY » simulé à 1,10 n'a aucun gagnant : 3 sur 4 suffit")

    def test_candlestick_patterns_on_random_walks_do_not_pass(self) -> None:
        sds = {s: build_symbol_data(s, walk(6000, 20 + k)) for k, s in enumerate(self.SYMBOLS)}
        tables = {s: build_table(sd, (4, 12, 24)) for s, sd in sds.items()}
        variants = variants_from_detector(sds, detect_candlesticks, (4, 12, 24))
        self.assertGreater(len(variants), 10)
        verdict = judge_directional("chandeliers", variants, sds, tables, decision_symbols=self.SYMBOLS, permutations=200, seed=3)
        self.assertFalse(verdict.passed, "des chandeliers sur du hasard pur ne doivent pas passer (coûts compris)")

    def test_too_few_signals_never_pass(self) -> None:
        sds, tables, refs = planted(self.SYMBOLS, 30, count=20)
        verdict = judge_directional("rare", {("rare", 12): refs}, sds, tables, decision_symbols=self.SYMBOLS, permutations=50)
        self.assertFalse(verdict.passed)

    def test_only_decision_symbols_count(self) -> None:
        sds, tables, refs = planted(("EURUSD", "GBPUSD"), 40)
        gbp = sum(1 for r in refs if r.symbol == "GBPUSD")
        verdict = judge_directional("x", {("v", 12): refs}, sds, tables, decision_symbols=("GBPUSD",), permutations=50)
        assert verdict.result.best is not None
        self.assertLessEqual(verdict.result.best.signals, gbp, "EURUSD est calculé pour information, il ne compte pas dans la décision")

    def test_consistency_counts_positive_blocks_and_symbols(self) -> None:
        sds, tables, refs = planted(("GBPUSD",), 50)
        start, end = sds["GBPUSD"].times[0], sds["GBPUSD"].times[-1]
        c = consistency(refs, 12, sds, tables, start, end)
        self.assertEqual(c.positive_symbols, 1)
        self.assertGreaterEqual(c.positive_blocks, 3)


if __name__ == "__main__":
    unittest.main()
