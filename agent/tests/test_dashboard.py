import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from orionx.agent import Agent
from orionx.api.feed import EventFeed
from orionx.api.server import COOKIE, STATIC, Sessions, create_app, new_token
from orionx.audit.log import AuditLog
from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.config import AppConfig
from orionx.core.models import utcnow
from tests.helpers import EURUSD
from tests.test_agent import NOWEEKEND, StubStrategy, new_bar

CSRF = {"X-Requested-By": "orionx-dashboard"}


class SessionTests(unittest.TestCase):
    def test_login_codes_are_single_use_and_expire(self) -> None:
        now = [0.0]
        s = Sessions(code_ttl=60, clock=lambda: now[0])
        code = s.issue_code()
        session = s.redeem(code)
        self.assertTrue(s.valid(session))
        self.assertIsNone(s.redeem(code), "usage unique")
        late = s.issue_code()
        now[0] = 61
        self.assertIsNone(s.redeem(late), "expiré après 60 s")
        self.assertFalse(s.valid(None))
        self.assertFalse(s.valid("invente"))


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.gw = SimulatedGateway({"EURUSD": EURUSD})
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, utcnow())
        self.gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(5)])
        cfg = AppConfig(symbols=("EURUSD",), autonomy="semi_auto", risk=NOWEEKEND)
        self.agent = Agent(self.gw, cfg, strategy=StubStrategy(), audit=AuditLog(), kill_flag=Path(tmp.name) / "flag.json")
        await self.agent.start()
        self.token, self.sessions = new_token(), Sessions()
        self.app = create_app(self.agent, self.token, Path(tmp.name) / "flag.json", EventFeed(), port=8765, sessions=self.sessions)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1:8765")
        self.addAsyncCleanup(self.client.aclose)

    async def login(self) -> None:
        r = await self.client.get(f"/login?code={self.sessions.issue_code()}")
        self.assertEqual(r.status_code, 303)
        cookie = r.headers["set-cookie"].lower()
        for attr in ("httponly", "samesite=strict", "path=/"):
            self.assertIn(attr, cookie)

    async def new_signal(self) -> str:
        await self.agent.run_cycle()
        self.gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(6)])
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, utcnow())
        await self.agent.run_cycle()
        (pid,) = self.agent.pending
        return pid

    async def test_bad_code_is_refused(self) -> None:
        r = await self.client.get("/login?code=faux")
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("set-cookie", r.headers)

    async def test_session_reads_but_every_action_needs_the_anti_csrf_header(self) -> None:
        await self.login()
        self.assertEqual((await self.client.get("/api/status")).status_code, 200)
        pid = await self.new_signal()
        forged = await self.client.post(f"/api/signals/{pid}/approve")  # ce qu'enverrait un formulaire d'un autre site
        self.assertEqual(forged.status_code, 403)
        self.assertEqual(self.gw.orders_sent, 0)
        self.assertEqual((await self.client.post("/api/kill", json={"level": 3})).status_code, 403)
        ok = (await self.client.post(f"/api/signals/{pid}/approve", headers=CSRF)).json()
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(self.gw.orders_sent, 1)

    async def test_a_session_cannot_mint_new_login_codes(self) -> None:
        await self.login()
        self.assertEqual((await self.client.post("/api/session/code", headers=CSRF)).status_code, 401, "jeton requis")
        r = await self.client.post("/api/session/code", headers={"Authorization": f"Bearer {self.token}"})
        self.assertIn("code", r.json())

    async def test_without_session_nothing_is_readable(self) -> None:
        self.assertEqual((await self.client.get("/api/status")).status_code, 401)
        self.client.cookies.set(COOKIE, "invente")
        self.assertEqual((await self.client.get("/api/status")).status_code, 401)

    async def test_page_and_security_headers(self) -> None:
        r = await self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('<script src="/app.js"></script>', r.text)
        csp = r.headers["content-security-policy"]
        for rule in ("default-src 'none'", "script-src 'self'", "frame-ancestors 'none'", "form-action 'none'"):
            self.assertIn(rule, csp)
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        for path in ("/app.js", "/style.css"):
            self.assertEqual((await self.client.get(path)).status_code, 200, path)

    def test_dashboard_script_never_injects_html(self) -> None:
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(forbidden, js, f"{forbidden} : risque d'injection de code")
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", html, "aucun script en ligne (CSP)")
        self.assertNotIn("style=", html, "aucun style en ligne (CSP)")


class DashboardEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests.test_api import SimpleAgentStub

        self.feed, self.sessions = EventFeed(), Sessions()
        app = create_app(SimpleAgentStub(), new_token(), Path(tempfile.gettempdir()) / "unused.json", self.feed, port=8765,
                         sessions=self.sessions)  # fmt: skip
        self.client = TestClient(app, base_url="http://127.0.0.1:8765")
        self.client.cookies.set(COOKIE, self.sessions.redeem(self.sessions.issue_code()))

    def test_session_websocket_requires_our_own_origin(self) -> None:
        with self.client.websocket_connect("ws://127.0.0.1:8765/api/events", headers={"origin": "http://127.0.0.1:8765"}) as ws:
            self.feed.handle("GatewayRecovered", {})
            self.assertEqual(ws.receive_json()["kind"], "GatewayRecovered")
        with (self.assertRaises(WebSocketDisconnect) as ctx,
              self.client.websocket_connect("ws://127.0.0.1:8765/api/events",
                                            headers={"origin": "https://evil.example"}) as ws):  # fmt: skip
            ws.receive_json()
        self.assertEqual(ctx.exception.code, 1008, "détournement de WebSocket par un autre site refusé")


class RealServerWebSocketTests(unittest.IsolatedAsyncioTestCase):
    """Le client de test gère le WebSocket tout seul : seul un VRAI serveur uvicorn révèle une bibliothèque manquante
    (constaté le 28 oct. : « No supported WebSocket library detected », flux en direct muet)."""

    async def test_events_stream_through_the_real_uvicorn_server(self) -> None:
        import asyncio
        import json
        import socket

        import websockets

        from orionx.api.server import serve
        from tests.test_api import SimpleAgentStub

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        feed, token, stop = EventFeed(), new_token(), asyncio.Event()
        app = create_app(SimpleAgentStub(), token, Path(tempfile.gettempdir()) / "unused.json", feed, port=port)
        task = asyncio.create_task(serve(app, port, stop))
        try:
            ws = None
            for _ in range(50):  # le temps que le serveur écoute
                try:
                    ws = await websockets.connect(f"ws://127.0.0.1:{port}/api/events?token={token}")
                    break
                except OSError:
                    await asyncio.sleep(0.1)
            self.assertIsNotNone(ws, "serveur injoignable")
            assert ws is not None
            await asyncio.sleep(0.1)  # abonnement côté serveur
            feed.handle("GatewayRecovered", {})
            msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
            self.assertEqual(msg["kind"], "GatewayRecovered")
            stop.set()  # navigateur toujours connecté : l'arrêt ne doit pas l'attendre
            await asyncio.wait_for(task, 5)
            with self.assertRaises(websockets.ConnectionClosed) as ctx:
                await asyncio.wait_for(ws.recv(), 5)
            self.assertEqual(ctx.exception.rcvd.code if ctx.exception.rcvd else None, 1001)
        finally:
            stop.set()
            await asyncio.wait_for(task, 5)


if __name__ == "__main__":
    unittest.main()
