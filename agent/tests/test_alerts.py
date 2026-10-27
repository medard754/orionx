import io
import json
import threading
import unittest
import urllib.error
from unittest import mock

from orionx.agent import Agent
from orionx.alerts.router import AlertRouter, format_alert
from orionx.alerts.telegram import AlertError, TelegramNotifier, load_credentials, store_credentials
from orionx.audit.log import AuditLog
from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.config import AlertsConfig, AppConfig
from tests.helpers import EURUSD, T0, FixedClock
from tests.test_agent import NOWEEKEND, new_bar

TOKEN = "123456:SECRET-token"


class FakeNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail
        self._lock = threading.Lock()

    def send(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("réseau coupé")
        with self._lock:
            self.sent.append(text)


class FormatTests(unittest.TestCase):
    def test_levels_of_the_main_events(self) -> None:
        cases = {
            ("AccountChanged", "critique"): {"reason": "compte différent"},
            ("KillSwitchActivated", "critique"): {"level": 3, "reason": "test"},
            ("CircuitBreakerTripped", "critique"): {"reason": "DAILY_LOSS_LIMIT"},
            ("OrderFilled", "important"): {"order": {"proposal_id": "p", "side": "buy", "symbol": "EURUSD", "volume": 0.1},
                                           "result": {"price": 1.1}},
            ("GatewayReconnected", "important"): {"down_seconds": 42.0, "attempts": 2},
            ("AgentStarted", "info"): {"autonomy": "signals_only", "symbols": ["EURUSD"]},
        }
        for (kind, level), payload in cases.items():
            alert = format_alert(kind, payload)
            self.assertIsNotNone(alert, kind)
            self.assertEqual(alert.level, level, kind)

    def test_unprotected_position_and_uncontrolled_order_are_critical(self) -> None:
        self.assertEqual(format_alert("PositionAdjusted", {"action": "UNPROTECTED", "ticket": 7, "detail": "x"}).level, "critique")
        failed = {"order": {"proposal_id": "p"}, "result": {"message": "PROTECTION_FAILED_POSITION_OPEN"}}
        self.assertEqual(format_alert("OrderFailed", failed).level, "critique")
        self.assertEqual(format_alert("OrderFailed", {"order": {}, "result": {"message": "requote"}}).level, "important")

    def test_noise_and_duplicates_are_not_alerts(self) -> None:
        self.assertIsNone(format_alert("PositionAdjusted", {"action": "STOP_MOVED", "ticket": 1}))
        self.assertIsNone(format_alert("CircuitBreakerTripped", {"reason": "DATA_STALE"}))  # DataFrozen a son message
        self.assertIsNone(format_alert("MarketStateUpdated", {}))
        self.assertIsNone(format_alert("CycleError", {}))
        self.assertEqual(format_alert("HALT_LABELS_UNKNOWN", {}), None)
        self.assertIn("NOUVELLE_RAISON", format_alert("CircuitBreakerTripped", {"reason": "NOUVELLE_RAISON"}).text)


class RouterTests(unittest.TestCase):
    def router(self, notifier=None, **cfg) -> AlertRouter:
        self.clock = FixedClock(T0)
        self.notifier = notifier or FakeNotifier()
        r = AlertRouter(self.notifier, AlertsConfig(**cfg), clock=self.clock)
        self.addCleanup(r.close, 1.0)
        return r

    def test_min_level_filters_and_signals_are_opt_in(self) -> None:
        r = self.router()
        r.handle("AgentStarted", {"autonomy": "auto", "symbols": ["EURUSD"]})  # info : écartée par défaut
        r.handle("SignalOnly", {"proposal": {"id": "s1", "side": "buy", "symbol": "EURUSD"}})
        r.handle("GatewayRecovered", {})
        r.flush()
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertIn("IMPORTANT", self.notifier.sent[0])
        r2 = self.router(signals=True)
        r2.handle("SignalOnly", {"proposal": {"id": "s1", "side": "buy", "symbol": "EURUSD"}})
        r2.flush()
        self.assertIn("Signal", self.notifier.sent[0])

    def test_same_problem_is_not_repeated(self) -> None:
        r = self.router()
        for _ in range(5):
            r.handle("CircuitBreakerTripped", {"reason": "GATEWAY_ERRORS"})
        self.clock.advance(seconds=61)
        r.handle("CircuitBreakerTripped", {"reason": "GATEWAY_ERRORS"})  # critique : 60 s
        r.handle("GatewayRecovered", {})
        self.clock.advance(seconds=100)
        r.handle("GatewayRecovered", {})  # important : 300 s
        r.flush()
        self.assertEqual(len(self.notifier.sent), 3)

    def test_rate_limit_spares_critical_alerts_and_reports_what_was_dropped(self) -> None:
        r = self.router(max_per_minute=3)
        for i in range(5):
            r.handle("PositionClosed", {"ticket": i, "symbol": "EURUSD"})
        r.handle("AccountChanged", {"reason": "autre compte"})  # passe malgré la limite
        self.clock.advance(seconds=61)
        r.handle("PositionClosed", {"ticket": 99, "symbol": "EURUSD"})
        r.flush()
        self.assertEqual(len(self.notifier.sent), 5)
        self.assertIn("CRITIQUE", self.notifier.sent[3])
        self.assertIn("2 alerte(s) écartée(s)", self.notifier.sent[3])  # signalé dans le message suivant, quel qu'il soit
        self.assertNotIn("écartée", self.notifier.sent[4])

    def test_a_broken_notifier_or_payload_never_reaches_the_agent(self) -> None:
        r = self.router(FakeNotifier(fail=True))
        r.handle("AccountChanged", {"reason": "x"})
        r.handle("AccountChanged", object())  # contenu inattendu
        r.handle("OrderFilled", {"order": "pas un dict"})
        self.assertTrue(r.flush())
        self.assertEqual(r.failures, 1)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TelegramTests(unittest.TestCase):
    def test_send_posts_to_the_bot_api(self) -> None:
        seen = {}

        def opener(req, timeout):
            seen.update(url=req.full_url, data=req.data.decode(), timeout=timeout)
            return FakeResponse(json.dumps({"ok": True, "result": {}}).encode())

        TelegramNotifier(TOKEN, "42", opener=opener).send("bonjour")
        self.assertEqual(seen["url"], f"https://api.telegram.org/bot{TOKEN}/sendMessage")
        self.assertIn("chat_id=42", seen["data"])
        self.assertIn("text=bonjour", seen["data"])
        self.assertEqual(seen["timeout"], 5.0)

    def test_the_token_never_leaks_in_errors_or_repr(self) -> None:
        def broken(req, timeout):
            raise urllib.error.URLError(f"échec sur {req.full_url}")

        n = TelegramNotifier(TOKEN, "42", opener=broken)
        with self.assertRaises(AlertError) as ctx:
            n.send("x")
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertNotIn(TOKEN, repr(n))

        def refused(req, timeout):
            return FakeResponse(json.dumps({"ok": False, "description": f"Unauthorized {TOKEN}"}).encode())

        with self.assertRaises(AlertError) as ctx:
            TelegramNotifier(TOKEN, "42", opener=refused).send("x")
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_discover_chat_id_takes_the_latest_conversation(self) -> None:
        updates = {"ok": True, "result": [{"message": {"chat": {"id": 1}}}, {"message": {"chat": {"id": 777}}}]}
        n = TelegramNotifier(TOKEN, "", opener=lambda req, timeout: FakeResponse(json.dumps(updates).encode()))
        self.assertEqual(n.discover_chat_id(), "777")
        empty = TelegramNotifier(TOKEN, "", opener=lambda req, timeout: FakeResponse(b'{"ok": true, "result": []}'))
        self.assertIsNone(empty.discover_chat_id())


class FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, key):
        return self.store.get((service, key))

    def set_password(self, service, key, value):
        self.store[(service, key)] = value


class CredentialTests(unittest.TestCase):
    def test_env_first_then_keyring_then_nothing(self) -> None:
        kr = FakeKeyring()
        self.assertIsNone(load_credentials({}, kr))
        store_credentials("tok", "9", kr)
        self.assertEqual(load_credentials({}, kr), ("tok", "9"))
        env = {"ORIONX_TELEGRAM_TOKEN": "envtok", "ORIONX_TELEGRAM_CHAT_ID": "1"}
        self.assertEqual(load_credentials(env, kr), ("envtok", "1"))

    def test_storing_without_keyring_fails_clearly(self) -> None:
        with mock.patch("orionx.alerts.telegram._keyring", return_value=None), self.assertRaises(AlertError):
            store_credentials("tok", "9")


class AgentAlertTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_change_and_gateway_outage_reach_the_phone(self) -> None:
        clock = FixedClock(T0)
        gw = SimulatedGateway({"EURUSD": EURUSD})
        gw.set_tick("EURUSD", 1.08412, 1.08420, T0)
        gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(5)])
        notifier = FakeNotifier()
        router = AlertRouter(notifier, AlertsConfig(), clock=clock)
        self.addCleanup(router.close, 1.0)
        cfg = AppConfig(symbols=("EURUSD",), autonomy="signals_only", risk=NOWEEKEND)
        agent = Agent(gw, cfg, strategy=None, audit=AuditLog(), clock=clock, alerts=router)
        await agent.start()
        gw.disconnect()
        gw.reconnect_failures = 1
        for _ in range(3):
            await agent.run_cycle()
        clock.advance(seconds=5)
        await agent.run_cycle()  # reconnexion réussie
        gw.login = 9999
        await agent.run_cycle()
        router.flush()
        text = "\n".join(notifier.sent)
        self.assertIn("liaison MT5 en erreur", text)  # CircuitBreakerTripped GATEWAY_ERRORS (bus)
        self.assertIn("Liaison MT5 perdue", text)  # GatewayReconnectFailed (journal)
        self.assertIn("Liaison MT5 rétablie", text)
        self.assertIn("Agent ARRÊTÉ", text)
        self.assertNotIn("ACCOUNT_CHANGED", text, "pas de doublon : l'arrêt pour compte changé a son propre message")


class CommandTests(unittest.TestCase):
    def test_alert_test_without_credentials_explains_what_to_do(self) -> None:
        import contextlib

        import orionx.main as m

        out = io.StringIO()
        with mock.patch("orionx.alerts.telegram.load_credentials", return_value=None), contextlib.redirect_stdout(out):
            self.assertEqual(m.alert_cmd("alert-test"), 2)
        self.assertIn("alert-setup", out.getvalue())

    def test_alert_test_sends_one_message(self) -> None:
        import contextlib

        import orionx.main as m

        sent = []
        with (mock.patch("orionx.alerts.telegram.load_credentials", return_value=(TOKEN, "42")),
              mock.patch("orionx.alerts.telegram.TelegramNotifier.send", lambda self, text: sent.append(text)),
              contextlib.redirect_stdout(io.StringIO())):  # fmt: skip
            self.assertEqual(m.alert_cmd("alert-test"), 0)
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
