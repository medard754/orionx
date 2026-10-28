"""Rejeu des signaux passés (essai V-4) : règles de sortie, statistiques, et chaîne complète agent -> journal -> rapport."""

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orionx.agent import Agent
from orionx.audit.log import AuditLog
from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.config import AppConfig
from orionx.core.models import Bar, Side, utcnow
from orionx.signals_report import MIN_SIGNALS, Outcome, Signal, evaluate, load_signals, render, run_report, summarize
from tests.helpers import EURUSD
from tests.test_agent import NOWEEKEND, StubStrategy, new_bar

T0 = datetime(2026, 10, 1, 8, tzinfo=UTC)
ANCHOR = T0 + timedelta(hours=4)


def signal(side: Side = Side.BUY, *, entry: float = 1.1000, sl: float = 1.0980, tp: float = 1.1060, spread: float = 0.0001) -> Signal:
    return Signal("s1", "EURUSD", side, sl, tp, "stub", "H1", ANCHOR, entry, spread, "2026-10-01T12:00:00+00:00", "pending")


def bar(i: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(ANCHOR + timedelta(hours=i), o, h, low, c, 1.0)


class EvaluateTests(unittest.TestCase):
    def test_target_first_gives_the_reward_in_r_minus_cost(self) -> None:
        o = evaluate(signal(), [bar(1, 1.1000, 1.1020, 1.0995, 1.1015), bar(2, 1.1015, 1.1065, 1.1010, 1.1060)], cost_pips=1.0)
        self.assertEqual((o.result, o.bars_used, o.ambiguous), ("tp", 2, False))
        self.assertAlmostEqual(o.r or 0, 3.0 - 0.05)  # objectif 60 pips / stop 20 pips = 3 R ; 1 pip de coût = 1/20 R

    def test_stop_first_loses_one_r_plus_cost(self) -> None:
        o = evaluate(signal(), [bar(1, 1.1000, 1.1005, 1.0975, 1.0980)], cost_pips=1.0)
        self.assertEqual(o.result, "sl")
        self.assertAlmostEqual(o.r or 0, -1.05)

    def test_a_bar_touching_both_counts_the_stop_and_says_so(self) -> None:
        o = evaluate(signal(), [bar(1, 1.1000, 1.1070, 1.0970, 1.1000)])
        self.assertEqual((o.result, o.ambiguous), ("sl", True), "hypothèse pessimiste : jamais un gain par défaut")

    def test_bars_up_to_the_anchor_are_ignored(self) -> None:
        before = Bar(ANCHOR, 1.1000, 1.1100, 1.0900, 1.1000, 1.0)  # la bougie d'ancrage est déjà fermée : elle ne compte pas
        o = evaluate(signal(), [before, bar(1, 1.1000, 1.1010, 1.0995, 1.1005)])
        self.assertEqual(o.result, "open")

    def test_unresolved_and_missing_data_are_never_counted_as_gains(self) -> None:
        self.assertEqual(evaluate(signal(), []).result, "no_data")
        flat = [bar(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(1, 60)]
        o = evaluate(signal(), flat, max_bars=48)
        self.assertEqual((o.result, o.r, o.bars_used), ("open", None, 48))

    def test_a_sell_pays_the_spread_on_the_way_out(self) -> None:
        sell = signal(Side.SELL, entry=1.1000, sl=1.1020, tp=1.0940, spread=0.0002)
        # le bid descend à 1.0941 : sans spread l'objectif (1.0940) n'est pas touché, et avec le spread (ask = 1.0943) non plus
        self.assertEqual(evaluate(sell, [bar(1, 1.1000, 1.1005, 1.0941, 1.0950)]).result, "open")
        # le bid descend à 1.0938 : ask = 1.0940 -> objectif touché
        self.assertEqual(evaluate(sell, [bar(1, 1.1000, 1.1005, 1.0938, 1.0950)]).result, "tp")
        # le bid monte à 1.1019 : ask = 1.1021 >= stop -> stop touché alors que le bid seul ne l'atteint pas
        self.assertEqual(evaluate(sell, [bar(1, 1.1000, 1.1019, 1.0990, 1.1010)]).result, "sl")


class SummaryTests(unittest.TestCase):
    def outcomes(self, results: list[str]) -> list[Outcome]:
        return [Outcome(signal(), r, {"tp": 2.0, "sl": -1.0}.get(r), bars_used=1) for r in results]

    def test_statistics(self) -> None:
        s = summarize(self.outcomes(["tp", "sl", "sl", "tp", "open"]))
        self.assertEqual((s.signals, s.resolved, s.wins, s.unresolved), (5, 4, 2, 1))
        self.assertAlmostEqual(s.win_rate or 0, 0.5)
        self.assertAlmostEqual(s.mean_r or 0, 0.5)
        self.assertAlmostEqual(s.profit_factor or 0, 2.0)
        self.assertFalse(s.conclusive, "4 signaux ne prouvent rien")

    def test_no_conclusion_below_the_minimum_sample(self) -> None:
        lines: list[str] = []
        render(self.outcomes(["tp"] * (MIN_SIGNALS - 1)), cost_pips=1.0, max_bars=48, out=lines.append)
        self.assertTrue(any("CONCLUSION : aucune" in line for line in lines), lines)

    def test_a_positive_mean_inside_the_noise_is_not_an_edge(self) -> None:
        lines: list[str] = []
        results = ["tp", "sl", "sl"] * 12  # moyenne 0, 36 résolus
        render(self.outcomes(results), cost_pips=1.0, max_bars=48, out=lines.append)
        self.assertTrue(any("aucun avantage démontré" in line for line in lines), lines)

    def test_every_report_states_its_limits(self) -> None:
        lines: list[str] = []
        render(self.outcomes(["tp"] * 40), cost_pips=1.0, max_bars=48, out=lines.append)
        self.assertTrue(any(line.startswith("Limites") for line in lines))
        self.assertTrue(any("PAS encore une preuve" in line for line in lines), "même un bon résultat n'est pas une preuve")


class EndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_records_the_context_and_the_report_replays_it(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        gw = SimulatedGateway({"EURUSD": EURUSD})
        gw.set_tick("EURUSD", 1.08412, 1.08420, utcnow())
        gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(5)])
        audit = AuditLog()
        cfg = AppConfig(symbols=("EURUSD",), autonomy="signals_only", risk=NOWEEKEND)
        agent = Agent(gw, cfg, strategy=StubStrategy(), audit=audit, kill_flag=Path(tmp.name) / "flag.json")
        await agent.start()
        await agent.run_cycle()
        gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(6)])  # nouvelle bougie fermée : le stub propose un achat
        gw.set_tick("EURUSD", 1.08412, 1.08420, utcnow())
        await agent.run_cycle()
        (sig,) = load_signals(audit.read())
        self.assertEqual((sig.symbol, sig.side, sig.strategy, sig.timeframe), ("EURUSD", Side.BUY, "stub", "H1"))
        self.assertAlmostEqual(sig.entry, 1.08420, msg="un achat se fait à l'ask")
        self.assertAlmostEqual(sig.spread, 0.00008)
        self.assertEqual(sig.anchor_bar, new_bar(5).time)

        # le marché monte ensuite de 60 pips (objectif du stub = entrée + 0,0060) : le rejeu y voit un gain
        later = [new_bar(i) for i in range(6)] + [Bar(new_bar(5).time + timedelta(hours=1), 1.0845, 1.0910, 1.0840, 1.0905, 1.0)]
        gw.set_bars("EURUSD", "H1", later)
        lines: list[str] = []
        self.assertEqual(await run_report(gw, audit.read(), out=lines.append), 0)
        self.assertTrue(any("résolus : 1" in line for line in lines), lines)
        self.assertTrue(any("CONCLUSION : aucune" in line for line in lines), "1 signal ne conclut jamais")
        audit.close()

    async def test_without_recorded_signals_the_report_says_so(self) -> None:
        lines: list[str] = []
        self.assertEqual(await run_report(SimulatedGateway({"EURUSD": EURUSD}), [], out=lines.append), 2)
        self.assertIn("Aucun signal rejouable", lines[0])

    def test_the_command_is_wired(self) -> None:
        import orionx.main as m

        self.assertTrue(callable(m.signals_report_cmd))


if __name__ == "__main__":
    unittest.main()
