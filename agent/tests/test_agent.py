import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from orionx.agent import Agent
from orionx.audit.log import AuditLog
from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.config import AppConfig
from orionx.core.errors import GatewayError, OrionxError
from orionx.core.models import Bar, Side, TradeProposal, utcnow
from orionx.monitoring.flag import write_kill_flag
from tests.helpers import EURUSD, T0


class StubStrategy:
    """Propose un achat à chaque nouvelle bougie (pour tester la plomberie, pas la stratégie)."""

    name, timeframe, min_bars = "stub", "H1", 3

    def generate(self, symbol, bars, tick, now, open_symbols):
        if symbol in open_symbols:
            return None
        e = tick.ask
        return TradeProposal(f"stub-{bars[-1].time:%d%H%M}", symbol, Side.BUY, round(e - 0.0020, 5), round(e + 0.0060, 5),
                             0.6, "stub", ("test",), now, now + timedelta(seconds=60))  # fmt: skip


class FlakyGateway(SimulatedGateway):
    fail = False

    async def get_account(self):
        if self.fail:
            raise GatewayError("coupure simulée")
        return await super().get_account()


def new_bar(i: int) -> Bar:
    return Bar(T0 + timedelta(hours=i), 1.0840, 1.0850, 1.0830, 1.0845, 100.0)


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def build(self, autonomy="auto", allow_auto=True, gw=None, flag=None, audit=True):
        self.gw = gw or SimulatedGateway({"EURUSD": EURUSD})
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, utcnow())
        self.gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(5)])
        self.audit = AuditLog() if audit else None
        cfg = AppConfig(symbols=("EURUSD",), autonomy=autonomy)
        self.agent = Agent(self.gw, cfg, strategy=StubStrategy(), audit=self.audit, allow_auto=allow_auto, kill_flag=flag)
        await self.agent.start()
        self.bar_i = 5

    async def cycle(self, new: bool = False, bid: float = 1.08412):
        if new:
            bars = list(self.gw._bars[("EURUSD", "H1")]) + [new_bar(self.bar_i)]
            self.bar_i += 1
            self.gw.set_bars("EURUSD", "H1", bars)
        self.gw.set_tick("EURUSD", bid, bid + 0.00008, utcnow())
        return await self.agent.run_cycle()

    async def test_auto_executes_on_new_bar_and_audit_chain_is_valid(self) -> None:
        await self.build()
        r0 = await self.cycle()  # pas de nouvelle bougie : pas d'ordre
        self.assertEqual(r0.orders, 0)
        r1 = await self.cycle(new=True)
        self.assertEqual((r1.signals, r1.orders), (1, 1), r1)
        (p,) = await self.gw.get_positions()
        self.assertIsNotNone(p.sl)
        await self.cycle(new=True)  # position déjà ouverte sur le symbole : pas de second ordre
        self.assertEqual(len(await self.gw.get_positions()), 1)
        kinds = [e["kind"] for e in self.audit.tail(50)]
        self.assertIn("AgentStarted", kinds)
        self.assertIn("OrderFilled", kinds)
        self.assertTrue(self.audit.verify().ok)

    async def test_signals_only_never_sends_orders(self) -> None:
        await self.build(autonomy="signals_only")
        await self.cycle()
        r = await self.cycle(new=True)
        self.assertEqual((r.signals, r.orders, self.gw.orders_sent), (1, 0, 0))
        self.assertIn("SignalOnly", [e["kind"] for e in self.audit.tail(10)])

    async def test_semi_auto_waits_for_human_approval(self) -> None:
        await self.build(autonomy="semi_auto")
        await self.cycle()
        await self.cycle(new=True)
        self.assertEqual(self.gw.orders_sent, 0)
        (pid,) = self.agent.pending
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, utcnow())
        res = await self.agent.approve(pid)
        self.assertTrue(res.ok)
        self.assertEqual(len(await self.gw.get_positions()), 1)
        self.assertFalse((await self.agent.approve(pid)).ok)  # déjà traitée

    async def test_auto_without_permission_downgrades(self) -> None:
        await self.build(autonomy="auto", allow_auto=False)
        self.assertEqual(self.agent.autonomy, "semi_auto")

    async def test_kill_flag_closes_everything_and_blocks_new_orders(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            flag = Path(d) / "KILL.json"
            await self.build(flag=flag)
            await self.cycle()
            await self.cycle(new=True)
            self.assertEqual(len(await self.gw.get_positions()), 1)
            write_kill_flag(flag, 3, "test")
            r = await self.cycle()
            self.assertTrue(any("ARRÊT D'URGENCE" in a for a in r.actions), r.actions)
            self.assertEqual(await self.gw.get_positions(), [])
            n = self.gw.orders_sent
            await self.cycle(new=True)
            self.assertEqual(self.gw.orders_sent, n)  # plus aucun ordre
            self.assertIn("KillSwitchActivated", [e["kind"] for e in self.audit.tail(50)])

    async def test_daily_loss_limit_blocks_new_orders(self) -> None:
        await self.build()
        await self.cycle()
        self.gw._balance = 9_600.0  # -4 % depuis le début de journée
        r = await self.cycle(new=True)
        self.assertTrue(self.agent.risk.halted)
        self.assertEqual((r.orders, self.gw.orders_sent), (0, 0))

    async def test_gateway_errors_halt_then_recover(self) -> None:
        gw = FlakyGateway({"EURUSD": EURUSD})
        await self.build(gw=gw)
        gw.fail = True
        for _ in range(3):
            r = await self.cycle()
            self.assertIsNotNone(r.error)
        self.assertIn("GATEWAY_ERRORS", self.agent.risk.halt_reasons)
        gw.fail = False
        for _ in range(30):
            await self.cycle()
        self.assertNotIn("GATEWAY_ERRORS", self.agent.risk.halt_reasons)

    async def test_closed_position_feeds_consecutive_loss_counter(self) -> None:
        await self.build()
        await self.cycle()
        await self.cycle(new=True)  # ouvre
        await self.cycle(bid=1.0830)  # le prix baisse : instantané en perte
        self.gw.set_tick("EURUSD", 1.0815, 1.0816, utcnow())  # le stop est touché
        await self.agent.run_cycle()
        self.assertEqual(self.agent.risk.state.consecutive_losses, 1)
        self.assertIn("PositionClosed", [e["kind"] for e in self.audit.tail(50)])

    async def test_refuses_to_start_on_non_demo_account(self) -> None:
        gw = SimulatedGateway({"EURUSD": EURUSD}, is_demo=False)
        with self.assertRaises(OrionxError):
            await self.build(gw=gw)

    async def test_run_loop_stops_after_max_cycles_and_logs_shutdown(self) -> None:
        import asyncio

        await self.build()
        await self.agent.run(asyncio.Event(), cycle_seconds=0.0, max_cycles=3)
        self.assertEqual(self.agent.cycles, 3 + 0)
        self.assertEqual(self.audit.tail(1)[0]["kind"], "AgentStopped")


if __name__ == "__main__":
    unittest.main()
