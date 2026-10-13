import unittest

from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.models import Side
from tests.helpers import EURUSD, T0


class SimulatedGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.gw = SimulatedGateway({"EURUSD": EURUSD})
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, T0)

    async def open_buy(self):
        return await self.gw.send_market_order("EURUSD", Side.BUY, 0.22, 1.0820, 1.0910, 10, "t")

    async def test_stop_loss_closes_position_and_books_loss(self) -> None:
        await self.open_buy()
        self.gw.set_tick("EURUSD", 1.0820, 1.0821, T0)
        self.assertEqual(await self.gw.get_positions(), [])
        acc = await self.gw.get_account()
        self.assertAlmostEqual(acc.balance, 10_000 - 48.4, places=6)

    async def test_gap_fills_at_worse_price(self) -> None:
        await self.open_buy()
        self.gw.set_tick("EURUSD", 1.0819, 1.0820, T0)
        self.assertAlmostEqual((await self.gw.get_account()).balance, 10_000 - 50.6, places=6)

    async def test_take_profit(self) -> None:
        await self.open_buy()
        self.gw.set_tick("EURUSD", 1.0910, 1.0911, T0)
        self.assertGreater((await self.gw.get_account()).balance, 10_000)

    async def test_unrealized_pnl_in_equity(self) -> None:
        await self.open_buy()
        self.gw.set_tick("EURUSD", 1.08520, 1.08530, T0)
        acc = await self.gw.get_account()
        self.assertAlmostEqual(acc.equity - acc.balance, (1.08520 - 1.08420) / 1e-5 * 0.22, places=6)


if __name__ == "__main__":
    unittest.main()
