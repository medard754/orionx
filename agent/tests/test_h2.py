import contextlib
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orionx.backtest import h2
from orionx.backtest.h1 import (
    FilterReport,
    Hypothesis,
    judge_dev,
    pick_candidate,
    run_dev,
    run_hypotheses,
    save_snapshot,
)
from orionx.backtest.runner import BacktestResult
from orionx.backtest.walkforward import RandomReference
from orionx.core.config import AppConfig
from orionx.strategies.trend_pullback import TrendPullback
from tests.helpers import EURUSD, synthetic_bars


def res(trades=60, pf=1.5, dd=5.0, net=100.0) -> BacktestResult:
    return BacktestResult(trades=trades, wins=trades // 2, net_profit=net, profit_factor=pf, max_drawdown_pct=dd)


def ref(share=0.97, runs=40) -> RandomReference:
    return RandomReference(runs=runs, mean_net=0.0, std_net=1.0, mean_pf=1.0, share_worse=share)


FOLDS = [res(30, 1.5), res(30, 1.4), res(30, 1.3), res(30, 0.9)]


class StricterRandomCriterionTests(unittest.TestCase):
    def test_h1_threshold_is_unchanged_and_h2_is_stricter(self) -> None:
        total = res(120, 1.4, 8.0)
        self.assertTrue(judge_dev(total, FOLDS, ref(0.95)).passed, "H-1 : 95 % suffit toujours")
        self.assertFalse(judge_dev(total, FOLDS, ref(0.95), h2.BEAT_RANDOM_MIN).passed, "H-2 : 95 % ne suffit plus")
        self.assertTrue(judge_dev(total, FOLDS, ref(0.975), h2.BEAT_RANDOM_MIN).passed)
        self.assertEqual(h2.BEAT_RANDOM_MIN, 0.975)

    def test_detail_states_the_threshold_that_was_applied(self) -> None:
        criteria = judge_dev(res(120, 1.4, 8.0), FOLDS, ref(0.96), h2.BEAT_RANDOM_MIN).criteria
        (detail,) = [c.detail for c in criteria if c.name == "hasard"]
        self.assertIn("96.0%", detail)
        self.assertIn("97.5%", detail)


class CandidateTests(unittest.TestCase):
    def report(self, name, share, pf) -> FilterReport:
        total = res(120, pf, 8.0)
        return FilterReport(name, total, FOLDS, ref(share), judge_dev(total, FOLDS, ref(share), h2.BEAT_RANDOM_MIN))

    def test_h2_has_no_reference_row_so_every_hypothesis_can_be_the_candidate(self) -> None:
        reports = [self.report("H-2a", 0.98, 1.3), self.report("H-2b", 0.99, 1.2), self.report("H-2c", 0.975, 1.9)]
        self.assertEqual(pick_candidate(reports, references=()).name, "H-2b", "le plus de tirages battus, puis le PF")
        self.assertIsNone(pick_candidate([self.report("none", 0.99, 2.0)]), "H-1 : « none » reste une référence")
        self.assertEqual(pick_candidate([self.report("none", 0.99, 2.0)], references=()).name, "none")


class HypothesesTests(unittest.TestCase):
    def test_the_three_preregistered_hypotheses_use_default_config_and_the_right_strategies(self) -> None:
        hs = h2.hypotheses()
        self.assertEqual([h.id for h in hs], ["H-2a", "H-2b", "H-2c"])
        names = [[e.strategy.name for e in h.make().engine.entries] for h in hs]
        self.assertEqual(names, [["mean_reversion_v1"], ["breakout_v1"], ["trend_pullback_v1", "mean_reversion_v1", "breakout_v1"]])
        self.assertTrue(all(h.filter == "none" for h in hs), "aucun filtre d'entrée dans H-2")
        self.assertEqual(h2.hypotheses()[0].make().engine.analysis, AppConfig().analysis, "configuration par défaut, jamais config.yaml")

    def test_run_hypotheses_is_deterministic_and_named_by_id(self) -> None:
        bars = synthetic_bars(3000, seed=11)
        hs = [Hypothesis("X-1", TrendPullback, "none"), Hypothesis("X-2", TrendPullback, "vol")]
        a = run_hypotheses(bars, EURUSD, "EURUSD", hs, random_runs=2)
        b = run_hypotheses(bars, EURUSD, "EURUSD", hs, random_runs=2)
        self.assertEqual([r.name for r in a], ["X-1", "X-2"])
        self.assertTrue(all(len(r.folds) == 4 for r in a))
        self.assertEqual([(r.total.trades, r.total.net_profit) for r in a], [(r.total.trades, r.total.net_profit) for r in b])

    def test_run_dev_still_means_the_five_h1_configurations(self) -> None:
        reports = run_dev(synthetic_bars(3000, seed=11), EURUSD, "EURUSD", TrendPullback, random_runs=2)
        self.assertEqual([r.name for r in reports], ["none", "vol", "session", "trend", "all"])


class SnapshotIntegrityTests(unittest.TestCase):
    def test_wrong_data_is_refused_before_any_computation(self) -> None:
        self.assertIsNone(h2.check_snapshot("88180049abbeb362"))
        problem = h2.check_snapshot("0123456789abcdef")
        self.assertIsNotNone(problem)
        self.assertIn("88180049abbeb362", problem)


class H2CommandTests(unittest.IsolatedAsyncioTestCase):
    async def run_cmd(self, d: str, open_holdout, **kw):
        import orionx.main as m
        from orionx.connector_mt5.simulated import SimulatedGateway

        gw = SimulatedGateway({"EURUSD": EURUSD})
        buf = io.StringIO()
        with (mock.patch.object(m, "DATA_DIR", Path(d)), mock.patch.object(m, "AUDIT_DB", Path(d) / "audit.db"),
              mock.patch.object(m, "_mt5_gateway", lambda: gw), contextlib.redirect_stdout(buf)):  # fmt: skip
            code = await m.h2_cmd("EURUSD", open_holdout, **kw)
        return code, buf.getvalue()

    async def test_snapshot_with_a_different_hash_stops_h2(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            save_snapshot(Path(d) / "h1_EURUSD_H1.csv", synthetic_bars(6000, seed=5))
            code, out = await self.run_cmd(d, None)
        self.assertEqual(code, 4, out)
        self.assertIn("88180049abbeb362", out)
        self.assertNotIn("RÉSUMÉ", out, "aucun calcul n'a eu lieu")

    async def test_full_flow_keeps_the_reserve_closed_and_refuses_an_unqualified_opening(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            snap = Path(d) / "h1_EURUSD_H1.csv"
            save_snapshot(snap, synthetic_bars(6000, seed=5))
            sha = hashlib.sha256(snap.read_bytes()).hexdigest()[:16]
            code, out = await self.run_cmd(d, None, expected_sha=sha, random_runs=2)
            self.assertEqual(code, 0, out)
            for hid in ("H-2a", "H-2b", "H-2c"):
                self.assertIn(hid, out)
            self.assertIn("NON évaluée ici", out)
            self.assertNotIn("RÉFÉRENCE", out, "H-2 n'a pas de ligne de référence")
            code, out = await self.run_cmd(d, "H-2a", expected_sha=sha, random_runs=2)
            self.assertEqual(code, 3, out)
            self.assertFalse((Path(d) / "h2_holdout_opened.json").exists())


if __name__ == "__main__":
    unittest.main()
