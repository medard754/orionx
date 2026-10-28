import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from orionx.agent import Agent
from orionx.audit.log import AuditLog
from orionx.connector_mt5.mt5_real import Mt5Gateway
from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.bus import EventBus
from orionx.core.config import AppConfig
from orionx.core.errors import InvalidQuoteError
from orionx.core.events import DataStale
from orionx.data.engine import MarketData
from tests.helpers import EURUSD, T0, FixedClock
from tests.test_agent import NOWEEKEND, new_bar
from tests.test_reconnect import FakeMt5


class MarketDataQuoteTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_crossed_quote_is_skipped_and_the_last_valid_price_ages(self) -> None:
        clock, bus, events = FixedClock(T0), EventBus(), []

        async def keep(e):
            events.append(e)

        bus.subscribe(DataStale, keep)
        gw = SimulatedGateway({"EURUSD": EURUSD})
        gw.set_tick("EURUSD", 1.1000, 1.1001, T0)
        md = MarketData(gw, ["EURUSD"], bus, stale_after=3.0, clock=clock)
        await md.poll()
        gw.invalid_quotes.add("EURUSD")
        clock.advance(seconds=1)
        self.assertEqual(await md.poll(), [], "aucun prix inventé, aucune exception")
        self.assertEqual(md.last["EURUSD"].bid, 1.1000)
        clock.advance(seconds=5)
        await md.poll()
        self.assertEqual(len(events), 1, "le dernier prix valide vieillit : flux signalé périmé")


class AgentQuoteTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_persistent_crossed_quote_never_breaks_the_cycle_and_ends_in_safe_mode(self) -> None:
        clock = FixedClock(T0)
        gw = SimulatedGateway({"EURUSD": EURUSD})
        gw.set_tick("EURUSD", 1.08412, 1.08420, T0)
        gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(5)])
        cfg = AppConfig(symbols=("EURUSD",), autonomy="signals_only", risk=NOWEEKEND)
        agent = Agent(gw, cfg, strategy=None, audit=AuditLog(), clock=clock)
        await agent.start()
        await agent.run_cycle()
        gw.set_tick("EURUSD", 1.08415, 1.08423, T0)  # le prix a changé une fois : flux réellement vivant
        await agent.run_cycle()
        gw.invalid_quotes.add("EURUSD")
        managed = []
        real_step = agent.positions.step

        async def spy(*args, **kwargs):
            managed.append(1)
            return await real_step(*args, **kwargs)

        agent.positions.step = spy
        for _ in range(3):
            clock.advance(seconds=1)
            rep = await agent.run_cycle()
            self.assertIsNone(rep.error, "une cotation incohérente n'est pas une panne")
        self.assertEqual(len(managed), 3, "les positions restent gérées")
        self.assertNotIn("GATEWAY_ERRORS", agent.risk.halt_reasons)
        clock.advance(seconds=30)
        await agent.run_cycle()
        self.assertIn("DATA_STALE", agent.risk.halt_reasons, "plus de 30 s sans prix valide : mode sûr")


class OtherReadersTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_approval_keeps_the_proposal_when_the_quote_is_crossed(self) -> None:
        from orionx.core.models import Side, TradeProposal, utcnow

        gw = SimulatedGateway({"EURUSD": EURUSD})
        gw.set_tick("EURUSD", 1.08412, 1.08420, utcnow())
        gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(5)])
        agent = Agent(gw, AppConfig(symbols=("EURUSD",), risk=NOWEEKEND), strategy=None, audit=AuditLog())
        await agent.start()
        now = utcnow()
        from datetime import timedelta

        prop = TradeProposal("p1", "EURUSD", Side.BUY, 1.0820, 1.0900, 0.6, "test", ("t",), now, now + timedelta(minutes=1))
        agent.pending["p1"] = prop
        gw.invalid_quotes.add("EURUSD")
        res = await agent.approve("p1")
        self.assertFalse(res.ok)
        self.assertIn("réessayez", res.message)
        self.assertIn("p1", agent.pending)
        self.assertEqual(gw.orders_sent, 0)

    async def test_wait_fresh_tick_waits_through_crossed_quotes(self) -> None:
        from orionx.core.models import utcnow
        from orionx.demo_trade import wait_fresh_tick

        gw = SimulatedGateway({"EURUSD": EURUSD})
        gw.set_tick("EURUSD", 1.1000, 1.1001, utcnow())
        gw.invalid_quotes.add("EURUSD")
        pauses = []

        async def sleep(s):
            pauses.append(s)
            if len(pauses) == 2:
                gw.invalid_quotes.clear()
                gw.set_tick("EURUSD", 1.1002, 1.1003, utcnow())

        tick = await wait_fresh_tick(gw, "EURUSD", sleep)
        self.assertIsNotNone(tick)
        self.assertEqual(tick.bid, 1.1002)


class Mt5QuoteTests(unittest.IsolatedAsyncioTestCase):
    async def test_crossed_quote_from_mt5_is_rejected_without_counting_as_fresh(self) -> None:
        fake = FakeMt5()
        fake.symbol_info_tick = lambda s: SimpleNamespace(bid=1.1136, ask=1.1135, time=1, time_msc=1000)
        with mock.patch.dict(sys.modules, {"MetaTrader5": fake}):
            gw = Mt5Gateway()
            await gw.connect()
            try:
                with self.assertRaises(InvalidQuoteError):
                    await gw.get_tick("EURUSD")
                self.assertEqual(gw._last_seen, {})
            finally:
                await gw.shutdown()


if __name__ == "__main__":
    unittest.main()
