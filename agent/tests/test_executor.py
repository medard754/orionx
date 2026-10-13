import dataclasses
import unittest

from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.bus import EventBus
from orionx.core.errors import ApprovalError
from orionx.core.events import OrderFailed, OrderFilled
from orionx.execution.executor import Executor
from tests.helpers import EURUSD, SPECS, T0, account, manager, proposal, tick


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    def build(self, **gw_kw):
        gw = SimulatedGateway({"EURUSD": EURUSD}, **gw_kw)
        gw.set_tick("EURUSD", 1.08412, 1.08420, T0)
        rm, signer, clock = manager()
        bus = EventBus()
        events: list[object] = []

        async def keep(e: object) -> None:
            events.append(e)

        bus.subscribe(OrderFilled, keep)
        bus.subscribe(OrderFailed, keep)
        d = rm.evaluate(proposal(), tick(), account(), [], SPECS)
        self.assertTrue(d.approved, d.reasons)
        return gw, Executor(gw, signer, bus), d.order, events

    async def test_signed_order_is_executed_with_sl_tp(self) -> None:
        gw, ex, order, events = self.build()
        res = await ex.execute(order)
        self.assertTrue(res.ok)
        (p,) = await gw.get_positions()
        self.assertEqual((p.sl, p.tp, p.volume), (1.0820, 1.0910, 0.22))
        self.assertIsInstance(events[0], OrderFilled)

    async def test_unsigned_order_refused_and_nothing_sent(self) -> None:
        gw, ex, order, _ = self.build()
        with self.assertRaises(ApprovalError):
            await ex.execute(dataclasses.replace(order, signature=""))
        self.assertEqual(gw.orders_sent, 0)

    async def test_tampered_order_refused(self) -> None:
        gw, ex, order, _ = self.build()
        with self.assertRaises(ApprovalError):
            await ex.execute(dataclasses.replace(order, volume=50.0))
        self.assertEqual(gw.orders_sent, 0)

    async def test_idempotent_no_duplicate(self) -> None:
        gw, ex, order, _ = self.build()
        r1, r2 = await ex.execute(order), await ex.execute(order)
        self.assertEqual(r1, r2)
        self.assertEqual(gw.orders_sent, 1)
        self.assertEqual(len(await gw.get_positions()), 1)

    async def test_blocked_executor_sends_nothing(self) -> None:
        gw, ex, order, _ = self.build()
        ex.block("KILL_SWITCH")
        res = await ex.execute(order)
        self.assertFalse(res.ok)
        self.assertEqual(gw.orders_sent, 0)

    async def test_broker_failure_reported(self) -> None:
        gw, ex, order, events = self.build()
        gw.reject_next = True
        res = await ex.execute(order)
        self.assertFalse(res.ok)
        self.assertIsInstance(events[0], OrderFailed)

    async def test_missing_sl_is_repaired(self) -> None:
        gw, ex, order, _ = self.build(drop_sl_on_fill=True)
        res = await ex.execute(order)
        self.assertTrue(res.ok)
        (p,) = await gw.get_positions()
        self.assertEqual(p.sl, 1.0820)

    async def test_unrepairable_position_is_closed(self) -> None:
        gw, ex, order, _ = self.build(drop_sl_on_fill=True, fail_modify=True)
        res = await ex.execute(order)
        self.assertFalse(res.ok)
        self.assertEqual(await gw.get_positions(), [])
        self.assertEqual(ex.blocked, "PROTECTION_FAILED")


if __name__ == "__main__":
    unittest.main()
