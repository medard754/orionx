import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from orionx.audit.log import AuditLog
from orionx.backtest.h1 import (
    FilterReport,
    HoldoutAlreadyOpened,
    judge_dev,
    judge_holdout,
    load_snapshot,
    loser_breakdown,
    open_holdout_once,
    pick_candidate,
    run_dev,
    save_snapshot,
    split_holdout,
)
from orionx.backtest.runner import BacktestResult, Trade
from orionx.backtest.walkforward import RandomReference
from orionx.strategies.trend_pullback import TrendPullback
from tests.helpers import EURUSD, synthetic_bars


def res(trades=60, pf=1.5, dd=5.0, net=100.0) -> BacktestResult:
    return BacktestResult(trades=trades, wins=trades // 2, net_profit=net, profit_factor=pf, max_drawdown_pct=dd)


def ref(share=0.97, runs=40) -> RandomReference:
    return RandomReference(runs=runs, mean_net=0.0, std_net=1.0, mean_pf=1.0, share_worse=share)


GOOD_FOLDS = [res(30, 1.5), res(30, 1.4), res(30, 1.3), res(30, 0.9)]  # 3 blocs sur 4 au-dessus de 1,2


class SplitTests(unittest.TestCase):
    def test_holdout_is_the_last_183_days_and_nothing_overlaps(self) -> None:
        bars = synthetic_bars(6000)  # 250 jours : plus que la réserve (183 jours)
        dev, hold = split_holdout(bars)
        self.assertEqual(len(dev) + len(hold), len(bars))
        self.assertLess(dev[-1].time, hold[0].time)
        self.assertEqual(hold[-1], bars[-1])
        self.assertGreaterEqual(hold[0].time, bars[-1].time - timedelta(days=183))
        self.assertLess(dev[-1].time, bars[-1].time - timedelta(days=183))


class JudgeDevTests(unittest.TestCase):
    def test_all_criteria_met(self) -> None:
        v = judge_dev(res(120, 1.4, 8.0), GOOD_FOLDS, ref())
        self.assertTrue(v.passed, v)

    def test_each_criterion_is_required(self) -> None:
        bad = {
            "trades": judge_dev(res(99, 1.4, 8.0), GOOD_FOLDS, ref()),
            "blocs": judge_dev(res(120, 1.4, 8.0), [res(30, 1.5), res(30, 1.4), res(30, 1.0), res(30, 0.9)], ref()),
            "drawdown": judge_dev(res(120, 1.4, 10.0), GOOD_FOLDS, ref()),  # strictement < 10 %
            "hasard": judge_dev(res(120, 1.4, 8.0), GOOD_FOLDS, ref(share=0.9)),
            "tirages": judge_dev(res(120, 1.4, 8.0), GOOD_FOLDS, ref(runs=20)),
        }
        for name, v in bad.items():
            self.assertFalse(v.passed, name)
        self.assertEqual([c.name for c in bad["trades"].criteria if not c.passed], ["trades"])
        self.assertEqual([c.name for c in bad["blocs"].criteria if not c.passed], ["blocs"])
        self.assertEqual([c.name for c in bad["drawdown"].criteria if not c.passed], ["drawdown"])

    def test_profit_factor_exactly_1_2_does_not_count(self) -> None:
        folds = [res(30, 1.2), res(30, 1.2), res(30, 1.2), res(30, 2.0)]
        self.assertFalse(judge_dev(res(120, 1.4, 8.0), folds, ref()).passed)

    def test_blocks_without_losses_count_and_empty_blocks_do_not(self) -> None:
        folds = [res(30, float("inf")), res(30, 1.5), res(30, 1.5), res(0, 0.0)]
        self.assertTrue(judge_dev(res(120, 1.4, 8.0), folds, ref()).passed)
        self.assertFalse(judge_dev(res(120, 1.4, 8.0), folds[:3], ref()).passed)  # il faut 4 blocs


class JudgeHoldoutTests(unittest.TestCase):
    def test_holdout_criteria(self) -> None:
        self.assertTrue(judge_holdout(res(20, 1.3, 9.0, 50.0)).passed)
        self.assertFalse(judge_holdout(res(19, 1.3, 9.0, 50.0)).passed, "moins de 20 trades = non concluant")
        self.assertFalse(judge_holdout(res(30, 1.2, 9.0, 50.0)).passed)
        self.assertFalse(judge_holdout(res(30, 1.3, 9.0, -1.0)).passed)
        self.assertFalse(judge_holdout(res(30, 1.3, 10.0, 50.0)).passed)


def report(name, share, pf, passed=True) -> FilterReport:
    total = res(120, pf, 8.0 if passed else 20.0)
    return FilterReport(name, total, GOOD_FOLDS, ref(share), judge_dev(total, GOOD_FOLDS, ref(share)))


class CandidateTests(unittest.TestCase):
    def test_no_candidate_keeps_the_holdout_closed(self) -> None:
        self.assertIsNone(pick_candidate([report("vol", 0.99, 1.5, passed=False), report("none", 0.99, 2.0)]))

    def test_none_is_a_reference_never_a_candidate(self) -> None:
        self.assertIsNone(pick_candidate([report("none", 0.99, 2.0)]))

    def test_best_share_then_best_profit_factor(self) -> None:
        reports = [report("vol", 0.96, 1.9), report("session", 0.99, 1.3), report("trend", 0.99, 1.6), report("none", 1.0, 3.0)]
        self.assertEqual(pick_candidate(reports).name, "trend")


class HoldoutOnceTests(unittest.TestCase):
    def test_second_opening_is_refused_and_the_first_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            marker, audit = Path(d) / "holdout.json", AuditLog()
            open_holdout_once(marker, "vol", audit)
            self.assertEqual(audit.tail(1)[0]["kind"], "HoldoutOpened")
            with self.assertRaises(HoldoutAlreadyOpened):
                open_holdout_once(marker, "session", audit)
            self.assertIn("vol", marker.read_text(encoding="utf-8"))  # la première ouverture reste la trace


class SnapshotTests(unittest.TestCase):
    def test_roundtrip_is_exact(self) -> None:
        bars = synthetic_bars(50)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bars.csv"
            save_snapshot(path, bars)
            self.assertEqual(load_snapshot(path), bars)


class LoserTests(unittest.TestCase):
    def test_breakdown_counts_losers_per_group(self) -> None:
        def trade(opened, side, profit, reason):
            return Trade(opened, side, 1.1, 1.1, 0.1, profit, reason)

        # 2026-10-12 est un lundi ; 10:00 heure serveur (UTC+3 en octobre avant le 25) = 07:00 UTC = Londres
        log = [trade("2026-10-12T10:00:00+00:00", "buy", -10.0, "stop"), trade("2026-10-12T11:00:00+00:00", "buy", 20.0, "objectif"),
               trade("2026-10-13T03:00:00+00:00", "sell", -5.0, "stop")]  # fmt: skip
        b = loser_breakdown(log)
        self.assertEqual(b["sens"]["buy"].trades, 2)
        self.assertEqual((b["sens"]["buy"].losers, b["sens"]["sell"].losers), (1, 1))
        self.assertEqual(b["motif de sortie"]["stop"].losers, 2)
        self.assertAlmostEqual(b["séance"]["london"].net, 10.0)
        self.assertEqual(b["jour"]["lundi"].trades, 2)


class RunDevTests(unittest.TestCase):
    def test_dev_run_is_deterministic_and_reports_every_filter(self) -> None:
        bars = synthetic_bars(3000, seed=11)
        a = run_dev(bars, EURUSD, "EURUSD", TrendPullback, random_runs=2)
        b = run_dev(bars, EURUSD, "EURUSD", TrendPullback, random_runs=2)
        self.assertEqual([r.name for r in a], ["none", "vol", "session", "trend", "all"])
        self.assertEqual([(r.total.trades, r.total.net_profit, r.ref.share_worse) for r in a],
                         [(r.total.trades, r.total.net_profit, r.ref.share_worse) for r in b])  # fmt: skip
        self.assertTrue(all(len(r.folds) == 4 for r in a))


class H1CommandTests(unittest.IsolatedAsyncioTestCase):
    """La commande complète (main.h1_cmd) sur courtier simulé : couvre aussi l'import de orionx.main."""

    async def run_cmd(self, d: str, open_holdout: str | None):
        import contextlib
        import io
        from unittest import mock

        import orionx.main as m
        from orionx.connector_mt5.simulated import SimulatedGateway
        from orionx.core.config import AppConfig

        gw = SimulatedGateway({"EURUSD": EURUSD})
        buf = io.StringIO()
        with (mock.patch.object(m, "DATA_DIR", Path(d)), mock.patch.object(m, "AUDIT_DB", Path(d) / "audit.db"),
              mock.patch.object(m, "_mt5_gateway", lambda: gw), contextlib.redirect_stdout(buf)):  # fmt: skip
            code = await m.h1_cmd("EURUSD", open_holdout, AppConfig())
        return code, buf.getvalue()

    async def test_dev_run_never_touches_the_holdout_and_refuses_an_unqualified_opening(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            save_snapshot(Path(d) / "h1_EURUSD_H1.csv", synthetic_bars(6000, seed=5))
            code, out = await self.run_cmd(d, None)
            self.assertEqual(code, 0, out)
            self.assertIn("NON évaluée ici", out)
            self.assertIn("RÉSUMÉ DÉVELOPPEMENT", out)
            for name in ("none", "vol", "session", "trend", "all"):
                self.assertIn(name, out)
            code, out = await self.run_cmd(d, "vol")  # personne n'a le droit d'ouvrir la réserve sans être le candidat désigné
            self.assertEqual(code, 3, out)
            self.assertFalse((Path(d) / "h1_holdout_opened.json").exists())


if __name__ == "__main__":
    unittest.main()
