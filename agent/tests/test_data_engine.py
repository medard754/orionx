import unittest
from datetime import timedelta

from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.bus import EventBus
from orionx.core.events import BarClosed, DataRecovered, DataStale, TickReceived
from orionx.core.models import Bar
from orionx.data.engine import BarBuilder, MarketData, MarketStore
from tests.helpers import EURUSD, T0, FixedClock


class BarBuilderTests(unittest.TestCase):
    def test_m1_bar_ohlc_and_close_on_next_minute(self) -> None:
        b = BarBuilder("M1")
        self.assertIsNone(b.update(T0, 1.10))
        self.assertIsNone(b.update(T0 + timedelta(seconds=20), 1.12))
        self.assertIsNone(b.update(T0 + timedelta(seconds=40), 1.09))
        self.assertIsNone(b.update(T0 + timedelta(seconds=59), 1.11))
        bar = b.update(T0 + timedelta(seconds=61), 1.115)
        self.assertEqual((bar.open, bar.high, bar.low, bar.close, bar.volume), (1.10, 1.12, 1.09, 1.11, 4.0))
        self.assertEqual(bar.time, T0)

    def test_late_tick_is_ignored(self) -> None:
        b = BarBuilder("M1")
        b.update(T0 + timedelta(minutes=2), 1.10)
        self.assertIsNone(b.update(T0, 5.0))
        bar = b.update(T0 + timedelta(minutes=3), 1.10)
        self.assertEqual(bar.high, 1.10)


class MarketStoreTests(unittest.TestCase):
    def test_roundtrip_and_replace(self) -> None:
        s = MarketStore()
        for i in range(3):
            s.save_bar("EURUSD", "H1", Bar(T0 + timedelta(hours=i), 1.0, 1.1, 0.9, 1.05, 10))
        s.save_bar("EURUSD", "H1", Bar(T0, 1.0, 1.2, 0.9, 1.05, 10))  # même clé : remplacée
        bars = s.load_bars("EURUSD", "H1", 10)
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0].high, 1.2)
        self.assertEqual(len(s.load_bars("EURUSD", "H1", 2)), 2)


class MarketDataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.clock = FixedClock()
        self.gw = SimulatedGateway({"EURUSD": EURUSD})
        self.bus, self.events = EventBus(), []

        async def keep(e: object) -> None:
            self.events.append(e)

        for cls in (TickReceived, BarClosed, DataStale, DataRecovered):
            self.bus.subscribe(cls, keep)
        self.md = MarketData(self.gw, ["EURUSD"], self.bus, stale_after=3.0, timeframes=("M1",), clock=self.clock)

    async def test_only_changed_ticks_are_published(self) -> None:
        self.gw.set_tick("EURUSD", 1.1000, 1.1001, T0)
        self.assertEqual(len(await self.md.poll()), 1)
        self.assertEqual(len(await self.md.poll()), 0)  # même prix : rien
        self.gw.set_tick("EURUSD", 1.1002, 1.1003, T0 + timedelta(seconds=1))
        self.assertEqual(len(await self.md.poll()), 1)
        self.assertEqual(len([e for e in self.events if isinstance(e, TickReceived)]), 2)

    async def test_bar_closed_event(self) -> None:
        self.gw.set_tick("EURUSD", 1.1000, 1.1001, T0)
        await self.md.poll()
        self.gw.set_tick("EURUSD", 1.1010, 1.1011, T0 + timedelta(seconds=61))
        await self.md.poll()
        bars = [e for e in self.events if isinstance(e, BarClosed)]
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].timeframe, "M1")

    async def test_stale_then_recovered(self) -> None:
        self.gw.set_tick("EURUSD", 1.1000, 1.1001, T0)
        await self.md.poll()
        self.clock.advance(seconds=10)
        await self.md.poll()
        await self.md.poll()  # pas de doublon d'alerte
        self.assertEqual(len([e for e in self.events if isinstance(e, DataStale)]), 1)
        self.gw.set_tick("EURUSD", 1.1002, 1.1003, self.clock.now)
        await self.md.poll()
        self.assertEqual(len([e for e in self.events if isinstance(e, DataRecovered)]), 1)


if __name__ == "__main__":
    unittest.main()
