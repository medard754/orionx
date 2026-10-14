import tempfile
import unittest
from pathlib import Path

from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.bus import EventBus
from orionx.core.events import KillSwitchActivated
from orionx.core.models import Side
from orionx.execution.executor import Executor
from orionx.monitoring.flag import clear_kill_flag, read_kill_flag, write_kill_flag
from orionx.monitoring.killswitch import KillSwitch
from tests.helpers import EURUSD, T0, manager


class KillSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.gw = SimulatedGateway({"EURUSD": EURUSD})
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, T0)
        for _ in range(2):
            await self.gw.send_market_order("EURUSD", Side.BUY, 0.10, 1.0820, 1.0950, 10, "t")
        self.gw.set_tick("EURUSD", 1.0880, 1.0881, T0)  # positions en gain
        self.rm, signer, _ = manager()
        self.executor = Executor(self.gw, signer)
        self.bus = EventBus()
        self.events: list[object] = []

        async def keep(e: object) -> None:
            self.events.append(e)

        self.bus.subscribe(KillSwitchActivated, keep)
        self.ks = KillSwitch(self.gw, self.executor, self.rm, self.bus)

    async def test_level1_blocks_entries_only(self) -> None:
        rep = await self.ks.activate(1, "test")
        self.assertTrue(rep.ok)
        self.assertEqual(self.executor.blocked, "KILL_SWITCH_L1")
        self.assertTrue(self.rm.halted)
        self.assertEqual(len(await self.gw.get_positions()), 2)
        self.assertEqual(len(self.events), 1)

    async def test_level2_tightens_stops_never_loosens(self) -> None:
        await self.ks.activate(2, "test")
        positions = await self.gw.get_positions()
        self.assertEqual(len(positions), 2)
        self.assertTrue(all(p.sl is not None and p.sl > 1.0840 for p in positions))
        before = [p.sl for p in positions]
        await self.ks.activate(2, "again")  # idempotent : ne bouge plus
        self.assertEqual([p.sl for p in await self.gw.get_positions()], before)

    async def test_level3_closes_everything(self) -> None:
        rep = await self.ks.activate(3, "test")
        self.assertTrue(rep.ok, rep.failures)
        self.assertEqual(await self.gw.get_positions(), [])
        self.assertEqual(self.executor.blocked, "KILL_SWITCH_L3")

    async def test_level3_reports_positions_it_could_not_close(self) -> None:
        self.gw.fail_close = True
        rep = await self.ks.activate(3, "test")
        self.assertFalse(rep.ok)
        self.assertEqual(len(rep.failures), 2)
        self.assertIsNotNone(self.executor.blocked)

    async def test_report_says_explicitly_when_there_is_nothing_to_close(self) -> None:
        await self.gw.close_position(1000)
        await self.gw.close_position(1001)
        rep = await self.ks.activate(3, "test")
        self.assertIn("0 position(s) ouverte(s) vue(s) par l'agent", rep.actions)
        self.assertIn("aucune position à clôturer", rep.actions)

    async def test_level_never_decreases(self) -> None:
        await self.ks.activate(3, "test")
        rep = await self.ks.activate(1, "test")
        self.assertEqual(self.ks.level, 3)
        self.assertIn("déjà", rep.actions[0])

    async def test_reset_is_explicit(self) -> None:
        await self.ks.activate(1, "test")
        self.ks.reset()
        self.assertIsNone(self.executor.blocked)
        self.assertFalse(self.rm.halted)

    async def test_invalid_level(self) -> None:
        with self.assertRaises(ValueError):
            await self.ks.activate(4, "x")


class FlagTests(unittest.TestCase):
    def test_write_read_clear(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x" / "KILL.json"
            self.assertIsNone(read_kill_flag(p))
            write_kill_flag(p, 3, "test")
            self.assertEqual(read_kill_flag(p)["level"], 3)
            clear_kill_flag(p)
            self.assertIsNone(read_kill_flag(p))

    def test_unreadable_flag_is_treated_as_level_1(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "KILL.json"
            p.write_text("{pas du json", encoding="utf-8")
            self.assertEqual(read_kill_flag(p)["level"], 1)


if __name__ == "__main__":
    unittest.main()
