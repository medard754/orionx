import unittest

from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.models import Side, Tick
from orionx.positions.manager import PositionManager, PositionRules, protective_stop
from tests.helpers import EURUSD, SPECS, T0, FixedClock


class PositionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.clock = FixedClock()
        self.gw = SimulatedGateway({"EURUSD": EURUSD})
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, T0)
        self.pm = PositionManager(self.gw, PositionRules(), clock=self.clock)

    async def step(self, bid: float, ask: float):
        self.gw.set_tick("EURUSD", bid, ask, T0)
        tick = Tick("EURUSD", bid, ask, T0)
        return await self.pm.step(await self.gw.get_positions(), {"EURUSD": tick}, SPECS)

    async def open_buy(self, vol: float = 0.20):
        await self.gw.send_market_order("EURUSD", Side.BUY, vol, 1.0820, 1.0990, 10, "t")

    async def test_breakeven_then_trailing_then_partial_and_never_loosens(self) -> None:
        await self.open_buy()
        await self.step(1.08412, 1.08420)  # première observation : R = 0,0022
        acts = await self.step(1.08645, 1.08655)  # ≈ +1,02R
        self.assertTrue(any(a.startswith("STOP_MOVED") for a in acts), acts)
        (p,) = await self.gw.get_positions()
        self.assertAlmostEqual(p.sl, 1.0843, places=5)

        acts = await self.step(1.0880, 1.0881)  # ≈ +1,73R
        self.assertTrue(any(a.startswith("PARTIAL_CLOSE") for a in acts), acts)
        (p,) = await self.gw.get_positions()
        self.assertEqual(p.volume, 0.10)
        self.assertAlmostEqual(p.sl, 1.0858, places=5)

        sl_high = p.sl
        acts = await self.step(1.0870, 1.0871)  # le prix recule : le stop ne bouge PAS
        (p,) = await self.gw.get_positions()
        self.assertEqual(p.sl, sl_high)
        self.assertFalse(any(a.startswith("PARTIAL") for a in acts))  # partielle une seule fois

    async def test_sell_side_symmetry(self) -> None:
        await self.gw.send_market_order("EURUSD", Side.SELL, 0.10, 1.0863, 1.0790, 10, "t")
        await self.step(1.08412, 1.08420)
        await self.step(1.0799, 1.0800)
        (p,) = await self.gw.get_positions()
        self.assertLess(p.sl, 1.0863)
        self.assertGreater(p.sl, 1.0800)

    async def test_unprotected_position_is_flagged_once(self) -> None:
        gw = SimulatedGateway({"EURUSD": EURUSD}, drop_sl_on_fill=True)
        gw.set_tick("EURUSD", 1.08412, 1.08420, T0)
        await gw.send_market_order("EURUSD", Side.BUY, 0.1, 1.0820, 1.0950, 10, "t")
        pm = PositionManager(gw, clock=self.clock)
        tick = Tick("EURUSD", 1.08412, 1.08420, T0)
        first = await pm.step(await gw.get_positions(), {"EURUSD": tick}, SPECS)
        second = await pm.step(await gw.get_positions(), {"EURUSD": tick}, SPECS)
        self.assertTrue(first[0].startswith("UNPROTECTED"))
        self.assertEqual(second, [])

    async def test_time_exit_uses_local_clock(self) -> None:
        pm = PositionManager(self.gw, PositionRules(max_hold_minutes=30), clock=self.clock)
        await self.open_buy()
        tick = Tick("EURUSD", 1.08412, 1.08420, T0)
        await pm.step(await self.gw.get_positions(), {"EURUSD": tick}, SPECS)
        self.clock.advance(minutes=31)
        acts = await pm.step(await self.gw.get_positions(), {"EURUSD": tick}, SPECS)
        self.assertTrue(any(a.startswith("TIME_EXIT") for a in acts))
        self.assertEqual(await self.gw.get_positions(), [])

    async def test_protective_stop_none_when_already_tighter(self) -> None:
        await self.open_buy()
        (p,) = await self.gw.get_positions()
        tick = Tick("EURUSD", 1.0880, 1.0881, T0)
        new = protective_stop(p, tick, EURUSD)
        self.assertIsNotNone(new)
        await self.gw.modify_position(p.ticket, new, p.tp)
        (p2,) = await self.gw.get_positions()
        self.assertIsNone(protective_stop(p2, tick, EURUSD))


if __name__ == "__main__":
    unittest.main()
