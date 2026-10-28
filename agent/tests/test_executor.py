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
        gw.fail_codes = [10019]  # fonds insuffisants : ni réessayable ni ambigu
        res = await ex.execute(order)
        self.assertFalse(res.ok)
        self.assertEqual(gw.orders_sent, 1)
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

    # --- M6 : fiabilité et qualité d'exécution ---------------------------------------
    async def test_requote_is_retried_then_succeeds(self) -> None:
        gw, ex, order, events = self.build()
        gw.fail_codes = [10004, 10020]  # deux rejets « sûrs », puis succès
        res = await ex.execute(order)
        self.assertTrue(res.ok)
        self.assertEqual(gw.orders_sent, 3)
        self.assertEqual(len(await gw.get_positions()), 1)
        self.assertEqual(ex.reports[-1].attempts, 3)

    async def test_retries_are_limited(self) -> None:
        gw, ex, order, _ = self.build()
        gw.fail_codes = [10004] * 10
        res = await ex.execute(order)
        self.assertFalse(res.ok)
        self.assertEqual(gw.orders_sent, 3)  # 1 + 2 nouvelles tentatives, pas plus
        self.assertEqual(await gw.get_positions(), [])

    async def test_ambiguous_timeout_never_duplicates_and_finds_the_position(self) -> None:
        gw, ex, order, _ = self.build()
        gw.timeout_but_executed = True  # l'ordre est PARTI mais la réponse est un délai dépassé
        res = await ex.execute(order)
        self.assertTrue(res.ok)
        self.assertEqual(gw.orders_sent, 1)
        self.assertEqual(len(await gw.get_positions()), 1)
        self.assertEqual(ex.reports[-1].note, "RETROUVE_APRES_REPONSE_AMBIGUE")

    async def test_ambiguous_timeout_without_execution_is_not_retried(self) -> None:
        gw, ex, order, _ = self.build()
        gw.fail_codes = [10012]
        res = await ex.execute(order)
        self.assertFalse(res.ok)
        self.assertEqual(gw.orders_sent, 1)  # pas de renvoi à l'aveugle

    async def test_excessive_slippage_closes_the_position(self) -> None:
        gw, ex, order, _ = self.build()
        gw.slippage_points = 50  # tolérance : 10 points
        res = await ex.execute(order)
        self.assertFalse(res.ok)
        self.assertIn("SLIPPAGE_EXCEEDED", res.message)
        self.assertEqual(await gw.get_positions(), [])

    async def test_slippage_close_refused_leaves_a_protected_position_and_blocks(self) -> None:
        gw, ex, order, _ = self.build(drop_sl_on_fill=True, fail_close=True)
        gw.slippage_points = 50
        res = await ex.execute(order)
        self.assertFalse(res.ok)
        self.assertIn("SLIPPAGE_CLOSE_FAILED", res.message)
        (p,) = await gw.get_positions()  # la position reste ouverte : elle doit au moins avoir son stop
        self.assertEqual(p.sl, 1.0820)
        self.assertEqual(ex.blocked, "SLIPPAGE_CLOSE_FAILED")

    async def test_unrepairable_position_whose_close_fails_is_reported_open(self) -> None:
        gw, ex, order, _ = self.build(drop_sl_on_fill=True, fail_modify=True, fail_close=True)
        res = await ex.execute(order)
        self.assertFalse(res.ok)
        self.assertEqual(res.message, "PROTECTION_FAILED_POSITION_OPEN")
        self.assertEqual(ex.blocked, "PROTECTION_FAILED")

    async def test_small_slippage_is_accepted_and_measured(self) -> None:
        gw, ex, order, _ = self.build()
        gw.slippage_points = 4
        res = await ex.execute(order)
        self.assertTrue(res.ok)
        self.assertAlmostEqual(ex.reports[-1].slippage_points, 4.0, places=3)

    async def test_quality_stats_and_event(self) -> None:
        from orionx.core.events import ExecutionQuality

        gw, ex, order, _ = self.build()
        got: list[object] = []

        async def keep(e: object) -> None:
            got.append(e)

        ex._bus = EventBus()
        ex._bus.subscribe(ExecutionQuality, keep)
        await ex.execute(order)
        st = ex.stats()
        self.assertEqual(st["orders"], 1)
        self.assertEqual(st["success_rate"], 1.0)
        self.assertGreaterEqual(st["mean_latency_ms"], 0)
        self.assertEqual(len(got), 1)

    async def test_expired_approval_stops_retries(self) -> None:
        gw, ex, order, _ = self.build()
        gw.fail_codes = [10004, 10004]
        orig = gw.send_market_order

        async def slow(*a, **k):
            r = await orig(*a, **k)
            ex._signer._clock = lambda: order.expires_at.replace(year=order.expires_at.year + 1)  # le temps a passé
            return r

        gw.send_market_order = slow
        res = await ex.execute(order)
        self.assertFalse(res.ok)
        self.assertEqual(gw.orders_sent, 1)
        self.assertEqual(ex.reports[-1].note, "APPROBATION_EXPIREE_PENDANT_LES_TENTATIVES")


if __name__ == "__main__":
    unittest.main()
