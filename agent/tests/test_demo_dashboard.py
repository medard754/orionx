import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx

from orionx.api.server import create_app, new_token
from orionx.core.models import utcnow
from orionx.demo_dashboard import DemoWorld, build_demo, make_bars, step_market


class DemoBarsTests(unittest.TestCase):
    def test_bars_are_valid_recent_and_reproducible(self) -> None:
        a, b = make_bars(300, seed=3), make_bars(300, seed=3)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 300)
        self.assertTrue(all(x.low <= min(x.open, x.close) and x.high >= max(x.open, x.close) for x in a))
        self.assertTrue(all(a[i].time < a[i + 1].time for i in range(len(a) - 1)))
        self.assertLess((utcnow() - a[-1].time).total_seconds(), 4 * 3600, "la dernière bougie est récente")


class DemoWorldTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.world: DemoWorld = await build_demo(Path(self.tmp.name))
        self.addCleanup(self.world.agent._audit.close)  # Windows : la base SQLite doit être fermée avant d'effacer le dossier

    async def test_everything_is_simulated_and_isolated_from_the_real_data(self) -> None:
        w = self.world
        self.assertTrue(w.agent.simulated)
        self.assertTrue(str(w.kill_flag).startswith(self.tmp.name), "le drapeau d'arrêt de la démo n'est pas celui du vrai agent")
        self.assertEqual(w.agent.autonomy, "semi_auto")
        self.assertEqual(len(w.agent.last_positions), 2)
        self.assertEqual(len(w.agent.pending), 1, "un signal à approuver est présent dès le début")

    async def test_market_moves_and_signals_are_renewed_and_approvable(self) -> None:
        w = self.world
        bids = {(await w.gateway.get_tick("EURUSD")).bid}
        for _ in range(6):
            await step_market(w)
            bids.add((await w.gateway.get_tick("EURUSD")).bid)
        self.assertGreater(len(bids), 2, "le prix bouge (un retour exact au prix de départ reste possible : on compte les valeurs)")
        self.assertLessEqual(len(w.agent.pending), 1)
        (pid,) = w.agent.pending
        res = await w.agent.approve(pid, via="demo")
        self.assertTrue(res.ok, res.message)
        self.assertEqual(w.gateway.orders_sent, 3, "2 positions de départ + 1 ordre approuvé, tous sur le courtier simulé")

    async def test_status_route_flags_the_data_as_simulated(self) -> None:
        w = self.world
        token = new_token()
        app = create_app(w.agent, token, w.kill_flag, w.feed)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            body = (await c.get("/api/status", headers={"Authorization": f"Bearer {token}"})).json()
        self.assertTrue(body["simulated"])
        self.assertEqual(body["is_demo"], True)

    async def test_analysis_tabs_have_data_in_the_demo(self) -> None:
        (s,) = self.world.agent.analysis()
        self.assertGreater(s["bars"], 100)
        self.assertTrue(s["session"]["events"], "le calendrier de démonstration affiche des annonces")
        self.assertTrue(s["decision"]["strategies"])
        await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
