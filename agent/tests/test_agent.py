import tempfile
import unittest
from datetime import UTC, timedelta
from pathlib import Path

from orionx.agent import Agent
from orionx.audit.log import AuditLog
from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.config import AppConfig, RiskConfig
from orionx.core.errors import GatewayError, OrionxError
from orionx.core.models import Bar, Side, TradeProposal, utcnow
from orionx.monitoring.flag import write_kill_flag
from tests.helpers import EURUSD, T0, synthetic_bars

NOWEEKEND = RiskConfig(weekend_guard=False)  # ces tests utilisent l'horloge réelle : ils ne doivent pas dépendre du jour


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
        cfg = AppConfig(symbols=("EURUSD",), autonomy=autonomy, risk=NOWEEKEND)
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


class AgentAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def build(self, calendar=None, cfg=None, bars=None, strategy=True):
        self.gw = SimulatedGateway({"EURUSD": EURUSD})
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, utcnow())
        self.gw.set_bars("EURUSD", "H1", bars or [new_bar(i) for i in range(5)])
        self.audit = AuditLog()
        cfg = cfg or AppConfig(symbols=("EURUSD",), autonomy="auto", risk=NOWEEKEND)
        self.agent = Agent(self.gw, cfg, strategy=StubStrategy() if strategy else None, audit=self.audit,
                           calendar=calendar, allow_auto=True)  # fmt: skip
        await self.agent.start()

    def kinds(self):
        return [e["kind"] for e in self.audit.tail(100)]

    async def test_news_blackout_blocks_new_entry(self) -> None:
        from orionx.analysis.calendar import EconomicCalendar, EconomicEvent

        cal = EconomicCalendar([EconomicEvent(utcnow() + timedelta(minutes=10), "USD", "high", "CPI")])
        await self.build(calendar=cal)
        self.gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(6)])
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, utcnow())
        r = await self.agent.run_cycle()
        self.assertEqual((r.signals, r.orders, self.gw.orders_sent), (1, 0, 0))
        rejected = [e for e in self.audit.tail(100) if e["kind"] == "TradeRejected"]
        self.assertIn("NEWS_BLACKOUT", rejected[-1]["payload"]["reasons"])

    async def test_open_position_stop_is_tightened_when_news_is_imminent(self) -> None:
        from orionx.analysis.calendar import EconomicCalendar, EconomicEvent
        from orionx.core.models import Side

        cal = EconomicCalendar([EconomicEvent(utcnow() + timedelta(minutes=10), "EUR", "high", "BCE")])
        await self.build(calendar=cal, strategy=False)
        await self.gw.send_market_order("EURUSD", Side.BUY, 0.1, 1.0820, 1.0990, 10, "t")
        self.agent._prev = {p.ticket: p for p in await self.gw.get_positions()}
        self.gw.set_tick("EURUSD", 1.0855, 1.0856, utcnow())  # sous +1R : seule l'annonce resserre le stop
        r = await self.agent.run_cycle()
        self.assertTrue(any(a.startswith("THESIS_STOP") for a in r.actions), r.actions)
        (p,) = await self.gw.get_positions()
        self.assertGreater(p.sl, 1.0840)
        self.assertIn("ThesisDegraded", self.kinds())
        again = await self.agent.run_cycle()  # même situation : pas de nouvelle réaction
        self.assertFalse(any(a.startswith("THESIS") for a in again.actions))

    async def test_thesis_reaction_never_undoes_the_trailing_stop_of_the_same_cycle(self) -> None:
        from orionx.analysis.calendar import EconomicCalendar, EconomicEvent
        from orionx.core.models import Side

        cal = EconomicCalendar([EconomicEvent(utcnow() + timedelta(minutes=10), "EUR", "high", "BCE")])
        await self.build(calendar=cal, strategy=False)
        await self.gw.send_market_order("EURUSD", Side.BUY, 0.1, 1.0822, 1.0990, 10, "t")  # entrée 1.0842, R = 20 pips
        self.agent._prev = {p.ticket: p for p in await self.gw.get_positions()}
        self.gw.set_tick("EURUSD", 1.0882, 1.0883, utcnow())  # +2R : le suiveur monte le stop à 1.0862
        r = await self.agent.run_cycle()
        self.assertTrue(any(a.startswith("STOP_MOVED") for a in r.actions), r.actions)
        (p,) = await self.gw.get_positions()
        self.assertGreaterEqual(p.sl, 1.0862 - 1e-9, r.actions)  # la réaction à l'annonce ne l'a pas redescendu

    async def test_market_state_is_computed_and_journaled(self) -> None:
        await self.build(bars=synthetic_bars(300, seed=7))
        self.assertIn("EURUSD", self.agent.states)
        self.assertNotEqual(self.agent.states["EURUSD"].regime.value, "unknown")
        self.assertIn("MarketStateUpdated", self.kinds())

    async def test_failed_history_fetch_retries_the_regime_on_next_cycle(self) -> None:
        bars = synthetic_bars(300, seed=7)
        await self.build(bars=bars[:-1])
        before = self.agent.states["EURUSD"].bar_time
        self.gw.set_bars("EURUSD", "H1", bars)  # nouvelle bougie
        real = self.gw.get_bars

        async def history_down(sym, tf, count):
            if count > 2:
                raise GatewayError("historique indisponible")
            return await real(sym, tf, count)

        self.gw.get_bars = history_down
        r = await self.agent.run_cycle()
        self.assertIsNotNone(r.error)
        self.gw.get_bars = real
        await self.agent.run_cycle()
        self.assertGreater(self.agent.states["EURUSD"].bar_time, before)  # recalculé, pas figé pendant une heure

    async def test_high_volatility_and_blocked_session_checks(self) -> None:
        from orionx.analysis.state import MarketState, Regime, Session
        from orionx.core.config import AnalysisConfig

        cfg = AppConfig(symbols=("EURUSD",), autonomy="auto", risk=NOWEEKEND, analysis=AnalysisConfig(blocked_sessions=("asia",)))
        await self.build(cfg=cfg)
        p = StubStrategy().generate("EURUSD", [new_bar(0)], await self.gw.get_tick("EURUSD"), utcnow(), set())
        tk = await self.gw.get_tick("EURUSD")
        self.assertIsNone(self.agent._regime_check(p, tk, datetime_at(10)))
        self.assertEqual(self.agent._regime_check(p, tk, datetime_at(3)), "SESSION_BLOCKED")
        self.agent.states["EURUSD"] = MarketState("EURUSD", T0, T0, Regime.HIGH_VOL, 3.0, 0.0, 0.1, 0.002, Session.LONDON)
        self.assertEqual(self.agent._regime_check(p, tk, datetime_at(10)), "HIGH_VOLATILITY")

    async def test_server_offset_is_measured_from_a_fresh_tick(self) -> None:
        from orionx.core.models import Tick

        await self.build()
        now = utcnow()
        self.agent.market.last["EURUSD"] = Tick("EURUSD", 1.1, 1.1001, now + timedelta(hours=3), received_at=now)
        self.agent._update_offset()
        self.assertEqual(self.agent.server_offset, timedelta(hours=3))
        self.assertEqual(self.agent._to_utc(now + timedelta(hours=3)), now)


def datetime_at(hour: int):
    from datetime import datetime

    return datetime(2026, 10, 14, hour, 0, tzinfo=UTC)


GBPUSD_SPEC = EURUSD.__class__("GBPUSD", 5, 1e-5, 1e-5, 1.0, 0.01, 100.0, 0.01)


class AgentM4M5Tests(unittest.IsolatedAsyncioTestCase):
    async def build(self, symbols=("EURUSD",), engine=None, guard=None, cfg=None):
        self.gw = SimulatedGateway({"EURUSD": EURUSD, "GBPUSD": GBPUSD_SPEC})
        for s in symbols:
            self.gw.set_tick(s, 1.08412, 1.08420, utcnow())
            self.gw.set_bars(s, "H1", [new_bar(i) for i in range(5)])
        self.audit = AuditLog()
        cfg = cfg or AppConfig(symbols=symbols, autonomy="auto", risk=NOWEEKEND)
        self.agent = Agent(self.gw, cfg, engine=engine, risk_guard=guard, audit=self.audit, allow_auto=True)
        await self.agent.start()
        self.i = 5

    async def cycle(self, new=False, symbols=("EURUSD",), bid=1.08412):
        for s in symbols:
            if new:
                self.gw.set_bars(s, "H1", list(self.gw._bars[(s, "H1")]) + [new_bar(self.i)])
            self.gw.set_tick(s, bid, bid + 0.00008, utcnow())
        self.i += int(new)
        return await self.agent.run_cycle()

    def kinds(self):
        return [e["kind"] for e in self.audit.tail(200)]

    async def test_two_symbols_same_currency_direction_only_one_order(self) -> None:
        from orionx.core.config import DecisionConfig
        from orionx.decision.engine import DecisionEngine, StrategyEntry

        eng = DecisionEngine([StrategyEntry(StubStrategy())], DecisionConfig(max_per_currency_direction=1))
        await self.build(("EURUSD", "GBPUSD"), engine=eng, cfg=AppConfig(symbols=("EURUSD", "GBPUSD"), autonomy="auto", risk=NOWEEKEND))
        await self.cycle(symbols=("EURUSD", "GBPUSD"))
        r = await self.cycle(new=True, symbols=("EURUSD", "GBPUSD"))
        self.assertEqual(r.orders, 1)  # acheter EURUSD ET GBPUSD = vendre deux fois le dollar
        self.assertEqual(len(await self.gw.get_positions()), 1)
        notes = [e for e in self.audit.tail(200) if e["kind"] == "DecisionNote"]
        self.assertTrue(any("CURRENCY_DUPLICATE" in str(n["payload"]) for n in notes))

    async def test_execution_quality_is_journaled(self) -> None:
        await self.build(engine=None)
        from orionx.decision.engine import DecisionEngine, StrategyEntry

        self.agent.engine = DecisionEngine([StrategyEntry(StubStrategy())])
        await self.cycle()
        await self.cycle(new=True)
        self.assertIn("ExecutionQuality", self.kinds())
        self.assertEqual(self.agent.executor.stats()["orders"], 1)

    async def test_frozen_prices_put_the_agent_in_safe_mode_then_recover(self) -> None:
        from datetime import timedelta as td

        await self.build()
        await self.cycle(bid=1.08412)
        await self.cycle(bid=1.08420)  # le prix a changé : le flux est « confirmé vivant »
        self.gw.set_tick("EURUSD", 1.08430, 1.08438, utcnow() - td(seconds=60))  # puis il se fige depuis 60 s
        await self.agent.run_cycle()
        self.assertIn("DATA_STALE", self.agent.risk.halt_reasons)
        self.assertIn("CircuitBreakerTripped", self.kinds())
        await self.cycle(bid=1.08440)  # le flux repart
        self.assertNotIn("DATA_STALE", self.agent.risk.halt_reasons)
        self.assertIn("DataRestored", self.kinds())

    async def test_automatic_halts_are_published_once(self) -> None:
        await self.build()
        await self.cycle()
        self.gw._balance = 9_600.0
        await self.cycle()
        await self.cycle()
        trips = [e for e in self.audit.tail(200) if e["kind"] == "CircuitBreakerTripped"]
        self.assertEqual([t["payload"]["reason"] for t in trips], ["DAILY_LOSS_LIMIT"])

    async def test_risk_change_applies_only_after_the_safety_delay(self) -> None:
        import tempfile

        from orionx.core.config import risk_from_profile
        from orionx.risk.guard import RiskParamGuard

        with tempfile.TemporaryDirectory() as d:
            guard = RiskParamGuard(d, 24)
            guard.resolve_startup(risk_from_profile("prudent"))
            cfg = AppConfig(symbols=("EURUSD",), autonomy="auto", risk=risk_from_profile("dynamic"))  # demande PLUS de risque
            await self.build(guard=guard, cfg=cfg)
            self.assertEqual(self.agent.risk.cfg, risk_from_profile("prudent"))  # le délai de 24 h n'est pas contournable
            self.assertIsNotNone(guard.pending())
            await self.cycle()
            self.assertEqual(self.agent.risk.cfg, risk_from_profile("prudent"))
            guard.request(RiskConfig(risk_per_trade_pct=0.2))  # plus prudent : appliqué au prochain cycle
            await self.cycle()
            self.assertEqual(self.agent.risk.cfg.risk_per_trade_pct, 0.2)
            self.assertIn("RiskConfigChanged", self.kinds())


    async def test_position_on_unconfigured_symbol_is_understood_and_managed(self) -> None:
        from orionx.core.models import Side

        await self.build()  # le symbole configuré est EURUSD seulement
        self.gw.set_tick("GBPUSD", 1.30000, 1.30010, utcnow())
        self.gw.next_magic = 999  # ouverte à la main
        await self.gw.send_market_order("GBPUSD", Side.BUY, 0.2, 1.2978, 1.3100, 10, "manuel")  # R = 22 pips
        await self.agent._ensure_symbols(await self.gw.get_positions())
        self.assertIn("GBPUSD", self.agent.specs)
        await self.cycle()
        self.gw.set_tick("GBPUSD", 1.30500, 1.30510, utcnow())  # +2,3 R
        r = await self.cycle()
        self.assertTrue(any(a.startswith("STOP_MOVED") for a in r.actions), r.actions)
        (p,) = await self.gw.get_positions()
        self.assertGreater(p.sl, 1.2978)
