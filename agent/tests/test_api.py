import json
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from orionx.agent import Agent
from orionx.api.feed import EventFeed
from orionx.api.server import create_app, new_token, write_token
from orionx.audit.log import AuditLog
from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.config import AppConfig
from orionx.core.models import utcnow
from tests.helpers import EURUSD
from tests.test_agent import NOWEEKEND, StubStrategy, new_bar


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gw = SimulatedGateway({"EURUSD": EURUSD})
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, utcnow())
        self.gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(5)])
        self.audit, self.feed = AuditLog(), EventFeed()
        self.flag = Path(self.tmp.name) / "KILL_SWITCH.json"
        cfg = AppConfig(symbols=("EURUSD",), autonomy="semi_auto", risk=NOWEEKEND)
        self.agent = Agent(self.gw, cfg, strategy=StubStrategy(), audit=self.audit, kill_flag=self.flag, feed=self.feed)
        await self.agent.start()
        self.token = new_token()
        self.app = create_app(self.agent, self.token, self.flag, self.feed)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1")
        self.addAsyncCleanup(self.client.aclose)
        self.auth = {"Authorization": f"Bearer {self.token}"}

    async def new_signal(self) -> str:
        await self.agent.run_cycle()
        self.gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(6)])
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, utcnow())
        await self.agent.run_cycle()
        (pid,) = self.agent.pending
        return pid

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.audit.tail(200)]

    # --- sécurité ---------------------------------------------------------------------
    async def test_every_endpoint_requires_the_token(self) -> None:
        for method, url in (("GET", "/api/status"), ("GET", "/api/signals"), ("POST", "/api/signals/x/approve"),
                            ("POST", "/api/signals/x/reject"), ("POST", "/api/kill")):  # fmt: skip
            for headers in ({}, {"Authorization": "Bearer mauvais"}, {"Authorization": self.token}):
                r = await self.client.request(method, url, headers=headers, json={"level": 3})
                self.assertEqual(r.status_code, 401, (method, url, headers))
        self.assertFalse(self.flag.exists(), "aucun arrêt d'urgence sans jeton")

    async def test_foreign_host_header_is_refused(self) -> None:  # DNS rebinding
        r = await self.client.get("/api/status", headers={**self.auth, "Host": "evil.example"})
        self.assertEqual(r.status_code, 400)

    async def test_no_cors_and_no_public_docs(self) -> None:
        r = await self.client.options("/api/status", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
        self.assertNotIn("access-control-allow-origin", {k.lower() for k in r.headers})
        for url in ("/docs", "/openapi.json", "/redoc"):
            self.assertEqual((await self.client.get(url)).status_code, 404, url)

    async def test_token_file_is_written_and_tokens_are_unique(self) -> None:
        path = Path(self.tmp.name) / "api_token"
        write_token(path, self.token)
        self.assertEqual(path.read_text(encoding="utf-8"), self.token)
        self.assertNotEqual(new_token(), new_token())
        self.assertGreaterEqual(len(self.token), 40)

    # --- fonctions --------------------------------------------------------------------
    async def test_status_reflects_the_agent(self) -> None:
        await self.agent.run_cycle()
        body = (await self.client.get("/api/status", headers=self.auth)).json()
        self.assertEqual((body["autonomy"], body["is_demo"], body["pending_signals"]), ("semi_auto", True, 0))
        self.assertEqual(body["equity"], 10_000.0)

    async def test_approve_goes_through_the_agent_once_and_is_audited(self) -> None:
        pid = await self.new_signal()
        signals = (await self.client.get("/api/signals", headers=self.auth)).json()
        self.assertEqual([s["id"] for s in signals], [pid])
        self.assertFalse(signals[0]["expired"])
        r = (await self.client.post(f"/api/signals/{pid}/approve", headers=self.auth)).json()
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.gw.orders_sent, 1)
        again = (await self.client.post(f"/api/signals/{pid}/approve", headers=self.auth)).json()
        self.assertFalse(again["ok"], "une proposition ne s'exécute qu'une fois")
        self.assertEqual(self.gw.orders_sent, 1)
        approvals = [e for e in self.audit.tail(200) if e["kind"] == "SignalApproval"]
        self.assertEqual([a["payload"]["via"] for a in approvals], ["api", "api"])

    async def test_reject_removes_the_signal_without_any_order(self) -> None:
        pid = await self.new_signal()
        r = (await self.client.post(f"/api/signals/{pid}/reject", headers=self.auth)).json()
        self.assertTrue(r["ok"])
        self.assertEqual((self.agent.pending, self.gw.orders_sent), ({}, 0))
        self.assertIn("SignalRejectedByUser", self.kinds())

    async def test_approval_is_refused_once_the_account_has_changed(self) -> None:
        pid = await self.new_signal()
        self.gw.login = 4242  # autre compte dans le terminal
        r = (await self.client.post(f"/api/signals/{pid}/approve", headers=self.auth)).json()
        self.assertFalse(r["ok"])
        self.assertEqual(self.gw.orders_sent, 0)
        self.assertIsNotNone(self.agent.aborted)

    async def test_kill_writes_the_flag_that_the_agent_applies(self) -> None:
        r = await self.client.post("/api/kill", headers=self.auth, json={"level": 1, "reason": "test"})
        self.assertTrue(r.json()["ok"])
        self.assertEqual(json.loads(self.flag.read_text(encoding="utf-8"))["level"], 1)
        await self.agent.run_cycle()
        self.assertIn("KILL_SWITCH", self.agent.risk.halt_reasons)
        bad = await self.client.post("/api/kill", headers=self.auth, json={"level": 7})
        self.assertEqual(bad.status_code, 422)


class EventStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.feed, self.token = EventFeed(), new_token()
        agent = SimpleAgentStub()
        self.client = TestClient(create_app(agent, self.token, Path(tempfile.gettempdir()) / "unused_flag.json", self.feed),
                                 base_url="http://127.0.0.1")  # fmt: skip

    def test_events_are_streamed_with_the_token_only(self) -> None:
        with self.client.websocket_connect(f"ws://127.0.0.1/api/events?token={self.token}") as ws:
            self.feed.handle("GatewayReconnected", {"down_seconds": 12.0})
            msg = ws.receive_json()
        self.assertEqual((msg["kind"], msg["payload"]["down_seconds"]), ("GatewayReconnected", 12.0))
        self.assertEqual(self.feed.subscribers, 0, "abonnement retiré à la déconnexion")
        from starlette.websockets import WebSocketDisconnect

        with (self.assertRaises(WebSocketDisconnect) as ctx,
              self.client.websocket_connect("ws://127.0.0.1/api/events?token=mauvais") as ws):  # fmt: skip
            ws.receive_json()
        self.assertEqual(ctx.exception.code, 1008, "refus pour violation de politique (jeton)")


class SimpleAgentStub:
    """Le flux d'événements n'a besoin d'aucun état de l'agent."""

    autonomy, cycles, aborted, pending, last_account, last_positions = "signals_only", 0, None, {}, None, []


if __name__ == "__main__":
    unittest.main()
