import unittest
from datetime import timedelta
from pathlib import Path

import httpx

from orionx.agent import Agent
from orionx.analysis.calendar import EconomicCalendar, EconomicEvent
from orionx.analysis.snapshot import CHART_BARS, StrategyInfo, build_snapshot
from orionx.analysis.state import MarketState, Regime, Session, analyze
from orionx.analysis.tzutil import server_to_utc_eet
from orionx.api.server import create_app, new_token
from orionx.audit.log import AuditLog
from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.config import AnalysisConfig, AppConfig
from orionx.core.models import Tick, utcnow
from tests.helpers import EURUSD, T0, synthetic_bars
from tests.test_agent import NOWEEKEND

PIP = 0.0001
ADVICE = ("achetez", "vendez", "achat recommandé", "vente recommandée", "garanti")


def state(regime: Regime = Regime.TREND_UP, session: Session = Session.LONDON, ratio: float = 1.0) -> MarketState:
    return MarketState("EURUSD", T0, T0, regime, ratio, 1.5, 0.4, 0.0009, session)


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bars = synthetic_bars(300, seed=7)
        self.tick = Tick("EURUSD", self.bars[-1].close, self.bars[-1].close + 0.00008, T0)
        self.cfg = AnalysisConfig()

    def snap(self, **kw):
        state_now = analyze("EURUSD", self.bars, self.cfg, server_to_utc_eet)
        args = {"symbol": "EURUSD", "bars": self.bars, "state": state_now, "tick": self.tick,
                "pip": PIP, "now_utc": T0, "to_utc": server_to_utc_eet, "cfg": self.cfg}  # fmt: skip
        args.update(kw)
        return build_snapshot(**args)

    def test_every_tab_has_numbers_and_a_reading(self) -> None:
        s = self.snap()
        for tab in ("trend", "momentum", "volatility", "levels", "session", "decision"):
            self.assertTrue(s[tab]["reading"], tab)
        self.assertTrue(0 <= s["momentum"]["rsi14"] <= 100)
        self.assertEqual(s["price"]["spread_pips"], 0.8)
        self.assertEqual(len(s["chart"]["closes"]), CHART_BARS)
        self.assertEqual(len(s["chart"]["ema20"]), CHART_BARS)
        self.assertGreater(s["volatility"]["atr_pips"], 0)

    def test_classic_pivot_formulas_on_the_last_24_bars(self) -> None:
        s = self.snap()["levels"]
        last = self.bars[-24:]
        hi, lo, cl = max(b.high for b in last), min(b.low for b in last), last[-1].close
        p = (hi + lo + cl) / 3
        self.assertAlmostEqual(s["pivot"], p, places=5)
        self.assertAlmostEqual(s["r1"], 2 * p - lo, places=5)
        self.assertAlmostEqual(s["s1"], 2 * p - hi, places=5)
        self.assertAlmostEqual(s["r2"], p + (hi - lo), places=5)

    def test_readings_describe_and_never_advise(self) -> None:
        for regime in Regime:
            s = self.snap(state=state(regime, ratio=2.5), blackout="USD CPI", halts=("DAILY_LOSS_LIMIT",))
            text = " ".join(s[t]["reading"] for t in ("trend", "momentum", "volatility", "levels", "session", "decision")).lower()
            for word in ADVICE:
                self.assertNotIn(word, text, f"{regime} : « {word} » ressemble à un conseil")

    def test_decision_knows_which_strategies_fit_the_regime_and_what_blocks_entries(self) -> None:
        strategies = [StrategyInfo("trend_pullback", ("trend_up", "trend_down"), True), StrategyInfo("mean_reversion", ("range",), True),
                      StrategyInfo("breakout", None, False)]  # fmt: skip
        d = self.snap(state=state(Regime.TREND_UP), strategies=strategies)["decision"]
        self.assertEqual([x["eligible"] for x in d["strategies"]], [True, False, False])
        self.assertEqual(d["strategies"][2]["why"], "désactivée")
        self.assertEqual(d["blocks"], [])
        blocked = self.snap(state=state(Regime.HIGH_VOL), strategies=strategies, blackout="EUR BCE", halts=("KILL_SWITCH",))["decision"]
        self.assertEqual(blocked["blocks"], ["KILL_SWITCH", "forte volatilité", "annonce : EUR BCE"])
        self.assertIn("bloquées", blocked["reading"])

    def test_upcoming_events_show_minutes_left(self) -> None:
        ev = [EconomicEvent(T0 + timedelta(minutes=90), "USD", "high", "CPI")]
        s = self.snap(events=ev)["session"]
        self.assertEqual((s["events"][0]["in_minutes"], s["events"][0]["title"]), (90, "CPI"))

    def test_no_bars_and_no_price_do_not_break_anything(self) -> None:
        s = build_snapshot("EURUSD", [], None, None, PIP, T0, server_to_utc_eet, self.cfg)
        self.assertEqual(s["bars"], 0)
        self.assertIn("Pas encore", s["trend"]["reading"])
        self.assertEqual(s["chart"]["closes"], [])
        self.assertIsNone(s["price"]["bid"])


class AgentAnalysisSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        bars = synthetic_bars(300, seed=7)
        self.gw = SimulatedGateway({"EURUSD": EURUSD})
        self.gw.set_tick("EURUSD", bars[-1].close, bars[-1].close + 0.00008, utcnow())
        self.gw.set_bars("EURUSD", "H1", bars)
        cal = EconomicCalendar([EconomicEvent(utcnow() + timedelta(hours=2), "EUR", "high", "BCE")])
        self.agent = Agent(self.gw, AppConfig(symbols=("EURUSD",), autonomy="semi_auto", risk=NOWEEKEND), strategy=None,
                           audit=AuditLog(), calendar=cal)  # fmt: skip
        await self.agent.start()
        await self.agent.run_cycle()

    async def test_agent_exposes_what_it_computed(self) -> None:
        (s,) = self.agent.analysis()
        self.assertEqual(s["symbol"], "EURUSD")
        self.assertEqual(s["trend"]["regime"], self.agent.states["EURUSD"].regime.value)
        self.assertEqual(s["bars"], len(self.agent.analysis_bars["EURUSD"]))
        self.assertEqual([e["title"] for e in s["session"]["events"]], ["BCE"])

    async def test_api_route_is_protected_and_returns_the_analysis(self) -> None:
        token = new_token()
        app = create_app(self.agent, token, Path("unused.json"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            self.assertEqual((await c.get("/api/analysis")).status_code, 401)
            body = (await c.get("/api/analysis", headers={"Authorization": f"Bearer {token}"})).json()
        self.assertEqual(body[0]["symbol"], "EURUSD")
        self.assertIn("reading", body[0]["decision"])


if __name__ == "__main__":
    unittest.main()
