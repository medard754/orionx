import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from orionx.agent import Agent
from orionx.audit.log import AuditLog
from orionx.connector_mt5.mt5_real import Mt5Gateway
from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.config import AppConfig
from orionx.core.errors import GatewayError
from tests.helpers import EURUSD, T0, FixedClock
from tests.test_agent import NOWEEKEND, new_bar


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def build(self) -> None:
        self.clock = FixedClock(T0)
        self.gw = SimulatedGateway({"EURUSD": EURUSD})
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, T0)
        self.gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(5)])
        self.audit = AuditLog()
        cfg = AppConfig(symbols=("EURUSD",), autonomy="signals_only", risk=NOWEEKEND)
        self.agent = Agent(self.gw, cfg, strategy=None, audit=self.audit, clock=self.clock)
        await self.agent.start()

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.audit.tail(200)]

    async def test_outage_is_retried_with_backoff_then_recovers(self) -> None:
        await self.build()
        self.assertIsNone((await self.agent.run_cycle()).error)
        self.gw.disconnect()
        self.gw.reconnect_failures = 2
        for _ in range(2):
            self.assertIsNotNone((await self.agent.run_cycle()).error)
        self.assertEqual(self.gw.reconnect_calls, 0, "pas de reconnexion avant 3 erreurs de suite")
        await self.agent.run_cycle()  # 3e erreur : arrêt des entrées + 1re tentative (échoue)
        self.assertIn("GATEWAY_ERRORS", self.agent.risk.halt_reasons)
        self.assertEqual(self.gw.reconnect_calls, 1)
        await self.agent.run_cycle()
        self.assertEqual(self.gw.reconnect_calls, 1, "pas de nouvelle tentative avant 5 s")
        self.clock.advance(seconds=5)
        await self.agent.run_cycle()  # 2e tentative (échoue) : prochaine dans 10 s
        self.assertEqual(self.gw.reconnect_calls, 2)
        self.clock.advance(seconds=9)
        await self.agent.run_cycle()
        self.assertEqual(self.gw.reconnect_calls, 2, "délai doublé : 10 s")
        self.clock.advance(seconds=1)
        await self.agent.run_cycle()  # 3e tentative : réussit
        self.assertEqual(self.gw.reconnect_calls, 3)
        self.assertTrue(self.gw.connected)
        for _ in range(29):  # le mode sûr n'est levé qu'après 30 cycles réussis de suite
            self.assertIsNone((await self.agent.run_cycle()).error)
            self.assertIn("GATEWAY_ERRORS", self.agent.risk.halt_reasons)
        await self.agent.run_cycle()
        self.assertNotIn("GATEWAY_ERRORS", self.agent.risk.halt_reasons)
        kinds = self.kinds()
        for expected in ("GatewayReconnectFailed", "GatewayReconnected", "GatewayRecovered"):
            self.assertIn(expected, kinds)
        self.assertEqual(self.gw.orders_sent, 0)
        self.assertIsNone(self.agent.aborted)

    async def test_downtime_is_measured_from_the_first_failure_not_from_the_first_attempt(self) -> None:
        await self.build()
        self.gw.disconnect()  # la reconnexion réussira dès la 1re tentative, comme sur MT5 le 28 oct. (terminal relancé seul)
        for _ in range(3):  # une erreur par seconde : 1re à T0, 3e (et reconnexion) à T0 + 2 s
            await self.agent.run_cycle()
            self.clock.advance(seconds=1)
        events = [e for e in self.audit.tail(200) if e["kind"] == "GatewayReconnected"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["down_seconds"], 2.0, "durée depuis la 1re erreur, pas « 0.0 s »")

    async def test_the_outage_alert_comes_before_the_reconnected_one(self) -> None:
        from orionx.alerts.router import AlertRouter
        from orionx.core.config import AlertsConfig
        from tests.test_alerts import FakeNotifier

        self.clock = FixedClock(T0)
        self.gw = SimulatedGateway({"EURUSD": EURUSD})
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, T0)
        self.gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(5)])
        notifier = FakeNotifier()
        router = AlertRouter(notifier, AlertsConfig(), clock=self.clock)
        self.addCleanup(router.close, 1.0)
        cfg = AppConfig(symbols=("EURUSD",), autonomy="signals_only", risk=NOWEEKEND)
        agent = Agent(self.gw, cfg, strategy=None, audit=AuditLog(), clock=self.clock, alerts=router)
        await agent.start()
        self.gw.disconnect()
        for _ in range(3):
            await agent.run_cycle()  # la reconnexion réussit dès la 1re tentative (3e erreur)
        router.flush()
        texts = notifier.sent
        down = next(i for i, t in enumerate(texts) if "liaison MT5 en erreur" in t)
        back = next(i for i, t in enumerate(texts) if "Liaison MT5 rétablie" in t)
        self.assertLess(down, back, "on annonce la panne avant son rétablissement")

    async def test_backoff_is_capped_at_one_minute(self) -> None:
        await self.build()
        self.gw.disconnect()
        self.gw.reconnect_failures = 99
        gaps = []
        for _ in range(3):
            await self.agent.run_cycle()
        for _ in range(8):
            before = self.gw.reconnect_calls
            waited = 0
            while self.gw.reconnect_calls == before and waited < 200:  # borné : sans reconnexion, le test échoue au lieu de tourner
                self.clock.advance(seconds=1)
                waited += 1
                await self.agent.run_cycle()
            gaps.append(waited)
        self.assertEqual(gaps[:4], [5, 10, 20, 40])
        self.assertTrue(all(g <= 60 for g in gaps), gaps)

    async def test_account_switched_after_reconnect_aborts_everything(self) -> None:
        await self.build()
        await self.agent.run_cycle()
        self.gw.disconnect()
        for _ in range(3):
            await self.agent.run_cycle()  # la 3e tentative de reconnexion réussit
        self.assertTrue(self.gw.connected)
        self.gw.login = 2002  # l'utilisateur s'est connecté à un autre compte dans le terminal
        calls = 0
        real = self.gw.get_positions

        async def spy():
            nonlocal calls
            calls += 1
            return await real()

        self.gw.get_positions = spy
        rep = await self.agent.run_cycle()
        self.assertIn("compte", rep.error or "")
        self.assertIsNotNone(self.agent.aborted)
        self.assertIn("ACCOUNT_CHANGED", self.agent.risk.halt_reasons)
        self.assertIn("AccountChanged", self.kinds())
        await self.agent.run_cycle()
        self.assertEqual(calls, 0, "après abandon, aucune position n'est lue ni modifiée")

    async def test_switch_to_a_non_demo_account_without_any_outage_aborts(self) -> None:
        await self.build()
        await self.agent.run_cycle()
        self.gw._is_demo = False
        rep = await self.agent.run_cycle()
        self.assertIn("non démo", rep.error or "")
        self.assertIsNotNone(self.agent.aborted)

    async def test_run_loop_stops_when_the_account_changes(self) -> None:
        await self.build()
        self.gw.login = 3003
        await self.agent.run(asyncio.Event(), cycle_seconds=0.0, max_cycles=50)
        self.assertEqual(self.agent.cycles, 1)
        self.assertIsNotNone(self.agent.aborted)

    async def test_gateway_without_login_still_checks_demo(self) -> None:
        self.clock = FixedClock(T0)
        self.gw = SimulatedGateway({"EURUSD": EURUSD})
        self.gw.login = None
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, T0)
        self.gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(5)])
        cfg = AppConfig(symbols=("EURUSD",), autonomy="signals_only", risk=NOWEEKEND)
        self.agent = Agent(self.gw, cfg, strategy=None, audit=AuditLog(), clock=self.clock)
        await self.agent.start()
        self.assertIsNone((await self.agent.run_cycle()).error)
        self.gw._is_demo = False
        self.assertIn("non démo", (await self.agent.run_cycle()).error or "")


class FakeMt5:
    ACCOUNT_TRADE_MODE_DEMO = 0

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.init_ok, self.account_ok = True, True

    def initialize(self, *args, **kwargs) -> bool:
        self.calls.append(("initialize", kwargs))
        return self.init_ok

    def shutdown(self) -> None:
        self.calls.append(("shutdown", {}))

    def last_error(self) -> tuple[int, str]:
        return (-10004, "No IPC connection")

    def account_info(self):
        if not self.account_ok:
            return None
        return SimpleNamespace(login=123456, balance=1000.0, equity=1000.0, margin_free=1000.0, margin_level=0,
                               currency="USD", trade_mode=0)  # fmt: skip


class Mt5GatewayReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fake = FakeMt5()
        patcher = mock.patch.dict(sys.modules, {"MetaTrader5": self.fake})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gw = Mt5Gateway()
        await self.gw.connect()
        self.addAsyncCleanup(self.gw.shutdown)

    async def test_reconnect_closes_then_reopens_with_a_short_timeout_and_forgets_old_prices(self) -> None:
        self.gw._last_seen["EURUSD"] = (1, T0)
        self.fake.calls.clear()
        await self.gw.reconnect()
        self.assertEqual([c[0] for c in self.fake.calls], ["shutdown", "initialize"])
        self.assertEqual(self.fake.calls[1][1].get("timeout"), 10_000)
        self.assertEqual(self.gw._last_seen, {}, "un prix d'avant la coupure ne doit pas passer pour frais")

    async def test_reconnect_fails_loudly(self) -> None:
        self.fake.init_ok = False
        with self.assertRaises(GatewayError):
            await self.gw.reconnect()
        self.fake.init_ok, self.fake.account_ok = True, False
        with self.assertRaises(GatewayError):
            await self.gw.reconnect()  # terminal rouvert mais compte illisible

    async def test_account_exposes_the_login(self) -> None:
        acc = await self.gw.get_account()
        self.assertEqual((acc.login, acc.is_demo), (123456, True))


if __name__ == "__main__":
    unittest.main()
