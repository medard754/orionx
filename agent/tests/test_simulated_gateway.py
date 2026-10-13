import unittest
from datetime import timedelta

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

    async def test_partial_close_books_profit_and_reduces_volume(self) -> None:
        res = await self.open_buy()
        self.gw.set_tick("EURUSD", 1.0880, 1.0881, T0)
        await self.gw.close_position(res.ticket, 0.10)
        (p,) = await self.gw.get_positions()
        self.assertAlmostEqual(p.volume, 0.12)
        self.assertGreater((await self.gw.get_account()).balance, 10_000)

    async def test_get_bars_returns_last_n(self) -> None:
        from orionx.core.models import Bar

        bars = [Bar(T0 + timedelta(hours=i), 1.0, 1.1, 0.9, 1.0) for i in range(10)]
        self.gw.set_bars("EURUSD", "H1", bars)
        got = await self.gw.get_bars("EURUSD", "H1", 4)
        self.assertEqual([b.time for b in got], [b.time for b in bars[-4:]])


if __name__ == "__main__":
    unittest.main()
