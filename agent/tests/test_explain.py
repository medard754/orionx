import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import httpx

from orionx.agent import Agent
from orionx.api.explain import EDGE_NOTICE, HALTS_FR, NOTES_FR, REASONS_FR, build_preview, explain_signal, reason_fr, strategy_blurb
from orionx.api.server import create_app, new_token
from orionx.audit.log import AuditLog
from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.config import AppConfig
from orionx.core.models import Side, Tick, TradeProposal, utcnow
from orionx.risk.manager import Decision
from tests.helpers import EURUSD, T0, FixedClock, account, manager, proposal, tick
from tests.test_agent import NOWEEKEND, new_bar

ADVICE = ("achetez", "vendez", "nous recommandons", "il faut acheter", "il faut vendre", "garanti", "sans risque")


class PreviewTests(unittest.TestCase):
    def decision(self, **kw):
        rm, _signer, _clock = manager()
        prop, tk, acc = proposal(), tick(), account()
        return rm.evaluate(prop, tk, acc, [], {"EURUSD": EURUSD}), prop, tk, acc

    def test_money_at_stake_matches_the_order_the_risk_manager_would_send(self) -> None:
        d, prop, tk, acc = self.decision()
        self.assertTrue(d.approved, d.reasons)
        p = build_preview(d, prop.side, tk, EURUSD, acc)
        self.assertTrue(p["would_approve"])
        self.assertEqual(p["volume"], d.order.volume)
        # perte maximale = distance au stop / tick × valeur du tick × lots
        loss = abs(tk.ask - d.order.sl) / EURUSD.tick_size * EURUSD.tick_value * d.order.volume
        self.assertAlmostEqual(p["risk_cash"], round(loss, 2), places=2)
        self.assertLessEqual(p["risk_pct"], 0.5 + 1e-9, "jamais plus que le risque par trade configuré")
        self.assertGreater(p["reward_cash"], p["risk_cash"], "objectif plus loin que le stop (rapport >= 1,5)")
        self.assertAlmostEqual(p["rr"], p["reward_cash"] / p["risk_cash"], places=1)
        self.assertEqual(p["currency"], acc.currency)

    def test_a_refused_trade_says_why_in_plain_french_and_shows_no_amount(self) -> None:
        d = Decision(False, reasons=("SPREAD_TOO_WIDE", "HALTED:KILL_SWITCH"))
        p = build_preview(d, Side.BUY, tick(), EURUSD, account())
        self.assertFalse(p["would_approve"])
        self.assertIsNone(p["risk_cash"])
        self.assertEqual(len(p["reasons_fr"]), 2)
        self.assertTrue(all(" " in r for r in p["reasons_fr"]), "des phrases, pas des codes")

    def test_every_risk_code_documented_in_risk_rules_has_a_plain_sentence(self) -> None:
        rules = (Path(__file__).resolve().parents[2] / "docs" / "risk_rules.md").read_text(encoding="utf-8")
        import re

        codes = set(re.findall(r"`([A-Z_]{4,})`", rules)) - {"HALTED"}
        known = {*REASONS_FR, *HALTS_FR, *NOTES_FR}  # refus, causes d'arrêt, notes d'information
        missing = sorted(c for c in codes if c not in known and not c.startswith("HALTED"))
        self.assertEqual(missing, [], "ajouter une phrase simple dans api/explain.py")
        self.assertIn("arrêt d'urgence", reason_fr("HALTED:KILL_SWITCH").lower())
        self.assertIn("CODE_INCONNU", reason_fr("CODE_INCONNU"), "un code inconnu reste visible au lieu d'être masqué")


class ExplainTests(unittest.TestCase):
    PREVIEW = {"would_approve": True, "volume": 0.25, "risk_cash": 47.5, "risk_pct": 0.05, "reward_cash": 95.0, "reward_pct": 0.1,
               "rr": 2.0, "currency": "USD", "reasons_fr": [], "notes_fr": []}  # fmt: skip

    def text(self, **kw) -> str:
        args = {"side": "buy", "symbol": "EURUSD", "strategy": "trend_pullback_v1", "rationale": ["tendance haussière", "repli sur EMA20"],
                "regime_label": "Tendance haussière", "session_label": "Londres", "sl": 1.082, "tp": 1.09,
                "preview": self.PREVIEW}  # fmt: skip
        args.update(kw)
        return "\n".join(explain_signal(**args))

    def test_money_direction_and_honesty_are_all_there(self) -> None:
        t = self.text()
        self.assertIn("47,50 USD", t)
        self.assertIn("95,00 USD", t)
        self.assertIn("0,05 % du capital", t)
        self.assertIn("0,25 lot", t)
        self.assertNotIn("0.05", t)
        self.assertIn("hausse", t.lower())
        self.assertIn("EURUSD", t)
        self.assertIn(EDGE_NOTICE, t, "le rappel « aucun avantage démontré » accompagne toujours l'explication")
        self.assertIn("repli sur EMA20", t, "la raison technique du signal reste visible")

    def test_sell_is_explained_as_a_bet_on_a_fall(self) -> None:
        self.assertIn("baisse", self.text(side="sell").lower())

    def test_explanations_describe_and_never_advise(self) -> None:
        for strat in ("trend_pullback_v1", "mean_reversion_v1", "breakout_v1", "inconnue"):
            for prev in (self.PREVIEW, {"would_approve": False, "reasons_fr": ["Trop de positions ouvertes."], "currency": "USD"}):
                low = (self.text(strategy=strat, preview=prev) + strategy_blurb(strat)).lower()
                for word in ADVICE:
                    self.assertNotIn(word, low, f"{strat} : « {word} »")

    def test_refusal_reasons_are_listed_instead_of_amounts(self) -> None:
        t = self.text(preview={"would_approve": False, "reasons_fr": ["Trop de positions ouvertes."], "currency": "USD"})
        self.assertIn("refuserait", t)
        self.assertIn("Trop de positions ouvertes.", t)
        self.assertNotIn("47,50", t)

    def test_missing_context_does_not_break_the_text(self) -> None:
        waiting = {"available": False, "reasons_fr": ["Pas encore de prix."]}
        t = self.text(regime_label=None, session_label=None, rationale=[], preview=waiting)
        self.assertIn("Pas encore de prix.", t)


class SignalsApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_returns_the_preview_and_the_explanation_for_each_signal(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        gw = SimulatedGateway({"EURUSD": EURUSD})
        gw.set_tick("EURUSD", 1.08412, 1.08420, utcnow())
        gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(5)])
        agent = Agent(gw, AppConfig(symbols=("EURUSD",), autonomy="semi_auto", risk=NOWEEKEND), strategy=None, audit=AuditLog())
        await agent.start()
        await agent.run_cycle()
        now = utcnow()
        agent.pending["s1"] = TradeProposal("s1", "EURUSD", Side.BUY, 1.0822, 1.0902, 0.6, "trend_pullback_v1", ("repli sur EMA20",), now,
                                            now + timedelta(minutes=10))  # fmt: skip
        token = new_token()
        app = create_app(agent, token, Path(tmp.name) / "f.json")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            (s,) = (await c.get("/api/signals", headers={"Authorization": f"Bearer {token}"})).json()
        self.assertTrue(s["preview"]["would_approve"])
        self.assertGreater(s["preview"]["risk_cash"], 0)
        self.assertLessEqual(s["preview"]["risk_pct"], 0.5 + 1e-9)
        self.assertTrue(any("EURUSD" in line for line in s["explain"]))
        self.assertEqual(gw.orders_sent, 0, "l'aperçu n'envoie jamais d'ordre")
        agent._audit.close()  # type: ignore[union-attr]  # Windows : fermer la base avant le nettoyage


_unused = (FixedClock, T0, Tick)

if __name__ == "__main__":
    unittest.main()
