import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import httpx

from orionx.agent import MAX_PENDING_TICKETS, TicketError
from orionx.api.explain import explain_signal
from orionx.api.server import create_app, new_token
from orionx.core.models import Side, utcnow
from orionx.demo_dashboard import DemoWorld, build_demo, step_market

ADVICE = ("achetez", "vendez", "nous recommandons", "il faut acheter", "il faut vendre", "garanti", "sans risque")
PIP = 1e-4


class TicketCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.world: DemoWorld = await build_demo(Path(self.tmp.name))
        self.addCleanup(self.world.agent._audit.close)  # Windows : fermer la base avant d'effacer le dossier
        self.agent, self.gw = self.world.agent, self.world.gateway
        self.agent.pending.clear()

    async def test_buy_ticket_geometry_stop_below_target_above_in_the_ratio_asked(self) -> None:
        p = self.agent.propose_manual("EURUSD", "buy", sl_pips=20, rr=2.0)
        tick = await self.gw.get_tick("EURUSD")
        self.assertEqual(p.side, Side.BUY)
        self.assertAlmostEqual(tick.ask - p.sl, 20 * PIP, places=5)
        self.assertAlmostEqual(p.tp - tick.ask, 40 * PIP, places=5)
        self.assertEqual((p.strategy, self.agent.pending[p.id]), ("ticket_manuel", p))
        self.assertGreater(p.valid_until, utcnow() + timedelta(minutes=5))

    async def test_sell_ticket_is_the_mirror_image(self) -> None:
        p = self.agent.propose_manual("EURUSD", "sell", sl_pips=15, rr=3.0)
        tick = await self.gw.get_tick("EURUSD")
        self.assertAlmostEqual(p.sl - tick.bid, 15 * PIP, places=5)
        self.assertAlmostEqual(tick.bid - p.tp, 45 * PIP, places=5)

    async def test_the_stop_is_mandatory_and_bounded(self) -> None:
        for bad in (0, -5, 4.9, 200.1, 1e9, float("nan")):
            with self.assertRaises(TicketError, msg=str(bad)):
                self.agent.propose_manual("EURUSD", "buy", sl_pips=bad, rr=2.0)
        self.agent.propose_manual("EURUSD", "buy", sl_pips=5, rr=2.0)
        self.agent.propose_manual("EURUSD", "buy", sl_pips=200, rr=2.0)

    async def test_other_inputs_are_validated_with_a_plain_message(self) -> None:
        cases = [("GBPUSD", "buy", 20, 2.0, "symbole"), ("EURUSD", "achat", 20, 2.0, "sens"), ("EURUSD", "buy", 20, 0.9, "rapport"),
                 ("EURUSD", "buy", 20, 5.1, "rapport")]  # fmt: skip
        for sym, side, pips, rr, word in cases:
            with self.assertRaises(TicketError) as ctx:
                self.agent.propose_manual(sym, side, pips, rr)
            self.assertIn(word, str(ctx.exception).lower())

    async def test_signals_only_mode_never_prepares_orders(self) -> None:
        self.agent.autonomy = "signals_only"
        with self.assertRaises(TicketError) as ctx:
            self.agent.propose_manual("EURUSD", "buy", 20, 2.0)
        self.assertIn("signaux seuls", str(ctx.exception))
        self.assertEqual(self.agent.pending, {})

    async def test_no_ticket_without_a_price_or_when_the_agent_has_stopped(self) -> None:
        self.agent.market.last.pop("EURUSD")
        with self.assertRaises(TicketError):
            self.agent.propose_manual("EURUSD", "buy", 20, 2.0)
        await step_market(self.world)  # le prix revient
        self.agent.aborted = "compte changé"
        with self.assertRaises(TicketError) as ctx:
            self.agent.propose_manual("EURUSD", "buy", 20, 2.0)
        self.assertIn("arrêté", str(ctx.exception))

    async def test_pending_tickets_are_capped_and_expired_ones_are_purged(self) -> None:
        for _ in range(MAX_PENDING_TICKETS):
            self.agent.propose_manual("EURUSD", "buy", 20, 2.0)
            await asyncio_sleep_ms()
        with self.assertRaises(TicketError) as ctx:
            self.agent.propose_manual("EURUSD", "buy", 20, 2.0)
        self.assertIn("en attente", str(ctx.exception))
        for p in self.agent.pending.values():  # tout expire : la place se libère
            self.agent.pending[p.id] = type(p)(p.id, p.symbol, p.side, p.sl, p.tp, p.confidence, p.strategy, p.rationale,
                                               p.created_at - timedelta(hours=1), utcnow() - timedelta(seconds=1))  # fmt: skip
        self.agent.propose_manual("EURUSD", "buy", 20, 2.0)
        self.assertEqual(len(self.agent.pending), 1)

    async def test_approving_a_ticket_goes_through_the_risk_manager_with_a_stop_and_a_bounded_size(self) -> None:
        before = self.gw.orders_sent
        p = self.agent.propose_manual("EURUSD", "buy", 20, 2.0)
        preview = self.agent.preview_signal(p)
        self.assertTrue(preview["would_approve"], preview)
        self.assertLessEqual(preview["risk_pct"], 0.5 + 1e-9, "jamais plus que le risque par trade configuré")
        res = await self.agent.approve(p.id, via="ticket")
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.gw.orders_sent, before + 1)
        pos = next(x for x in await self.gw.get_positions() if x.ticket == res.ticket)
        self.assertIsNotNone(pos.sl, "aucun ordre sans stop-loss")
        self.assertAlmostEqual(abs(pos.price_open - pos.sl), 20 * PIP, delta=3 * PIP)

    async def test_nothing_is_sent_when_a_ticket_is_only_prepared_or_previewed(self) -> None:
        before = self.gw.orders_sent
        p = self.agent.propose_manual("EURUSD", "sell", 25, 2.0)
        self.agent.preview_signal(p)
        self.assertEqual(self.gw.orders_sent, before)


async def asyncio_sleep_ms() -> None:
    import asyncio

    await asyncio.sleep(0.002)  # des identifiants de ticket distincts (horodatage en ms)


class TicketExplanationTests(unittest.TestCase):
    PREVIEW = {"would_approve": True, "volume": 2.5, "risk_cash": 500.0, "risk_pct": 0.5, "reward_cash": 1000.0, "reward_pct": 1.0,
               "rr": 2.0, "currency": "USD", "reasons_fr": [], "notes_fr": []}  # fmt: skip

    def text(self, side="buy", **kw) -> str:
        args = {"side": side, "symbol": "EURUSD", "strategy": "ticket_manuel", "rationale": ["trade décidé par vous"],
                "regime_label": "Tendance haussière", "session_label": "Londres", "sl": 1.082, "tp": 1.09,
                "preview": self.PREVIEW, "manual": True}  # fmt: skip
        args.update(kw)
        return "\n".join(explain_signal(**args))

    def test_the_wording_says_the_idea_is_yours_and_orionx_only_sizes_the_risk(self) -> None:
        t = self.text()
        self.assertIn("vous pariez", t.lower())
        self.assertNotIn("l'agent parie", t.lower())
        self.assertIn("500,00 USD", t)
        self.assertIn("la taille", t.lower())
        self.assertIn("ne juge pas", t.lower(), "ORIONX ne prétend pas évaluer la qualité de l'idée")
        self.assertIn("votre ticket", t.lower())
        self.assertNotIn("repéré", t.lower(), "ce n'est pas l'agent qui a repéré quoi que ce soit")

    def test_it_describes_and_never_advises(self) -> None:
        for side in ("buy", "sell"):
            low = self.text(side=side).lower()
            for word in ADVICE:
                self.assertNotIn(word, low)


class TicketApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.world = await build_demo(Path(self.tmp.name))
        self.addCleanup(self.world.agent._audit.close)
        self.agent = self.world.agent
        self.agent.pending.clear()
        self.token = new_token()
        app = create_app(self.agent, self.token, self.world.kill_flag, self.world.feed)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")
        self.addAsyncCleanup(self.client.aclose)
        self.auth = {"Authorization": f"Bearer {self.token}"}
        self.body = {"symbol": "EURUSD", "side": "buy", "sl_pips": 20, "rr": 2.0}

    async def test_both_routes_need_the_token(self) -> None:
        for url in ("/api/tickets", "/api/tickets/preview"):
            r = await self.client.post(url, json=self.body)
            self.assertEqual(r.status_code, 401, url)
        self.assertEqual(self.agent.pending, {})

    async def test_preview_computes_the_money_without_storing_anything(self) -> None:
        sent = self.world.gateway.orders_sent
        r = await self.client.post("/api/tickets/preview", json=self.body, headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertTrue(out["preview"]["would_approve"])
        self.assertGreater(out["preview"]["risk_cash"], 0)
        self.assertLessEqual(out["preview"]["risk_pct"], 0.5 + 1e-9)
        self.assertTrue(any("vous pariez" in line.lower() for line in out["explain"]))
        self.assertEqual((self.agent.pending, self.world.gateway.orders_sent), ({}, sent), "l'aperçu ne stocke et n'envoie rien")

    async def test_preparing_a_ticket_adds_it_to_the_pending_signals(self) -> None:
        r = await self.client.post("/api/tickets", json=self.body, headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text)
        (s,) = (await self.client.get("/api/signals", headers=self.auth)).json()
        self.assertEqual((s["id"], s["strategy"]), (r.json()["id"], "ticket_manuel"))
        self.assertTrue(s["preview"]["would_approve"])
        approved = (await self.client.post(f"/api/signals/{s['id']}/approve", headers=self.auth)).json()
        self.assertTrue(approved["ok"], approved)

    async def test_bad_requests_get_a_plain_french_message_and_store_nothing(self) -> None:
        for bad, word in (({**self.body, "sl_pips": 0}, "stop"), ({**self.body, "symbol": "XAUUSD"}, "symbole"),
                          ({**self.body, "side": "achat"}, "sens")):  # fmt: skip
            r = await self.client.post("/api/tickets", json=bad, headers=self.auth)
            self.assertIn(r.status_code, (409, 422), bad)
            self.assertIn(word, r.text.lower())
        self.assertEqual(self.agent.pending, {})

    async def test_signals_only_agent_answers_conflict(self) -> None:
        self.agent.autonomy = "signals_only"
        r = await self.client.post("/api/tickets", json=self.body, headers=self.auth)
        self.assertEqual(r.status_code, 409)
        self.assertIn("signaux seuls", r.text)

    async def test_a_dashboard_session_needs_the_anti_csrf_header(self) -> None:
        from orionx.api.server import COOKIE, Sessions

        sessions = Sessions()
        app = create_app(self.agent, self.token, self.world.kill_flag, self.world.feed, sessions=sessions)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            c.cookies.set(COOKIE, sessions.redeem(sessions.issue_code()))
            self.assertEqual((await c.post("/api/tickets", json=self.body)).status_code, 403, "formulaire d'un autre site refusé")
            ok = await c.post("/api/tickets", json=self.body, headers={"X-Requested-By": "orionx-dashboard"})
            self.assertEqual(ok.status_code, 200, ok.text)


if __name__ == "__main__":
    unittest.main()
