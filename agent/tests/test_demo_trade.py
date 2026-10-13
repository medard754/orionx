import unittest

from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.models import utcnow
from orionx.demo_trade import run_demo_trade
from tests.helpers import EURUSD


async def no_sleep(_: float) -> None:
    return None


class DemoTradeTests(unittest.IsolatedAsyncioTestCase):
    def gw(self, **kw) -> SimulatedGateway:
        gw = SimulatedGateway({"EURUSD": EURUSD}, balance=100_000.0, **kw)
        gw.set_tick("EURUSD", 1.11777, 1.11778, utcnow())
        return gw

    async def run_it(self, gw, confirm=lambda _m: True):
        lines: list[str] = []
        code = await run_demo_trade(gw, hold_seconds=0, confirm=confirm, out=lines.append, sleep=no_sleep)
        return code, lines

    async def test_full_flow_opens_then_closes_one_minimal_lot(self) -> None:
        gw = self.gw()
        code, lines = await self.run_it(gw)
        self.assertEqual(code, 0, lines)
        self.assertEqual(len(gw.closed), 1)
        self.assertEqual(gw.closed[0][0].volume, 0.01)
        self.assertEqual(await gw.get_positions(), [])

    async def test_declined_sends_nothing(self) -> None:
        gw = self.gw()
        code, _ = await self.run_it(gw, confirm=lambda _m: False)
        self.assertEqual(code, 6)
        self.assertEqual(gw.orders_sent, 0)

    async def test_non_demo_account_refused(self) -> None:
        gw = self.gw(is_demo=False)
        code, _ = await self.run_it(gw)
        self.assertEqual(code, 3)
        self.assertEqual(gw.orders_sent, 0)

    async def test_broker_rejection_reported(self) -> None:
        gw = self.gw()
        gw.reject_next = True
        code, _ = await self.run_it(gw)
        self.assertEqual(code, 7)


if __name__ == "__main__":
    unittest.main()
