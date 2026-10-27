import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.config import PROFILES, RiskConfig, load_config, risk_from_profile
from orionx.core.errors import ConfigError
from orionx.core.models import Position, Side, SymbolSpec, Tick, TradeProposal
from orionx.positions.manager import PositionManager, PositionRules, choose_action
from orionx.risk.approval import ApprovalSigner
from orionx.risk.guard import RiskParamGuard, is_riskier
from orionx.risk.manager import RiskManager
from tests.helpers import EURUSD, SPECS, T0, FixedClock, account, manager, proposal, tick

UTC = timezone.utc
GBPUSD = SymbolSpec("GBPUSD", 5, 1e-5, 1e-5, 1.0, 0.01, 100.0, 0.01)
SPECS2 = {"EURUSD": EURUSD, "GBPUSD": GBPUSD}


def mgr(cfg: RiskConfig, now: datetime = T0):
    clock = FixedClock(now)
    rm = RiskManager(cfg, ApprovalSigner(key=b"k" * 32, clock=clock), max_data_age_seconds=3.0, clock=clock)
    rm.start(account())
    return rm, clock


def prop_at(now: datetime, side: Side = Side.BUY) -> TradeProposal:
    sl, tp = (1.0820, 1.0910) if side is Side.BUY else (1.0863, 1.0775)
    return TradeProposal("p", "EURUSD", side, sl, tp, 0.7, "t", ("t",), now, now + timedelta(seconds=120))


def gbp_long(vol: float) -> Position:
    return Position(7, "GBPUSD", Side.BUY, vol, 1.2650, 1.2628, 1.2700, 0.0, T0)  # risque : 220 ticks × vol


class ProfileTests(unittest.TestCase):
    def test_profiles_are_valid_and_ordered(self) -> None:
        for name in PROFILES:
            risk_from_profile(name)  # toutes dans les bornes absolues
        self.assertLess(risk_from_profile("prudent").risk_per_trade_pct, risk_from_profile("dynamic").risk_per_trade_pct)

    def test_unknown_profile_and_out_of_bounds_override(self) -> None:
        with self.assertRaises(ConfigError):
            risk_from_profile("fou")
        with self.assertRaises(ConfigError):
            risk_from_profile("prudent", risk_per_trade_pct=2.0)

    def test_yaml_profile_with_override(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.yaml"
            p.write_text("risk:\n  profile: moderate\n  risk_per_trade_pct: 0.6\n", encoding="utf-8")
            cfg = load_config(p)
            self.assertEqual((cfg.risk.risk_per_trade_pct, cfg.risk.max_open_positions), (0.6, 4))


class RiskRulesTests(unittest.TestCase):
    def test_weekend_guard(self) -> None:
        fri_late, fri_early = datetime(2026, 10, 16, 18, 30, tzinfo=UTC), datetime(2026, 10, 16, 17, 59, tzinfo=UTC)
        rm, _ = mgr(RiskConfig(), fri_late)
        d = rm.evaluate(prop_at(fri_late), tick(at=fri_late), account(), [], SPECS)
        self.assertIn("WEEKEND_GUARD", d.reasons)
        rm, _ = mgr(RiskConfig(), fri_early)
        self.assertTrue(rm.evaluate(prop_at(fri_early), tick(at=fri_early), account(), [], SPECS).approved)
        sat = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)
        rm, _ = mgr(RiskConfig(weekend_guard=False), sat)
        self.assertTrue(rm.evaluate(prop_at(sat), tick(at=sat), account(), [], SPECS).approved)

    def test_spread_limit_per_symbol(self) -> None:
        rm, clock = mgr(RiskConfig(max_spread_points_by_symbol={"EURUSD": 5}))
        wide = tick(1.08412, 1.08420, T0)  # 8 points
        self.assertIn("SPREAD_TOO_WIDE", rm.evaluate(proposal(), wide, account(), [], SPECS).reasons)
        rm2, _ = mgr(RiskConfig())
        self.assertTrue(rm2.evaluate(proposal(), wide, account(), [], SPECS).approved)

    def test_currency_exposure_reduces_volume(self) -> None:
        cfg = RiskConfig(max_total_open_risk_pct=4.0, max_currency_exposure_pct=1.0)
        rm, _ = mgr(cfg)
        d = rm.evaluate(proposal(), tick(), account(), [gbp_long(0.3)], SPECS2)  # déjà short USD de 0,66 %
        self.assertTrue(d.approved, d.reasons)
        self.assertIn("VOLUME_REDUCED", d.notes)
        self.assertEqual(d.order.volume, 0.15)

    def test_currency_exposure_netting_allows_offsetting_trade(self) -> None:
        cfg = RiskConfig(max_total_open_risk_pct=4.0, max_currency_exposure_pct=1.0)
        rm, _ = mgr(cfg)
        sell = proposal(Side.SELL, 1.0863, 1.0775)  # vendre EURUSD = long USD : réduit l'exposition short USD
        d = rm.evaluate(sell, tick(), account(), [gbp_long(0.3)], SPECS2)
        self.assertTrue(d.approved, d.reasons)
        self.assertNotIn("VOLUME_REDUCED", d.notes)

    def test_currency_exposure_rejects_when_full(self) -> None:
        cfg = RiskConfig(max_total_open_risk_pct=4.0, max_currency_exposure_pct=1.0)
        rm, _ = mgr(cfg)
        d = rm.evaluate(proposal(), tick(), account(), [gbp_long(0.5)], SPECS2)  # 1,1 % déjà short USD
        self.assertIn("CURRENCY_EXPOSURE", d.reasons)

    def test_apply_config_changes_behaviour(self) -> None:
        rm, _ = mgr(RiskConfig())
        big = rm.evaluate(proposal(), tick(), account(), [], SPECS).order.volume
        rm.apply_config(RiskConfig(risk_per_trade_pct=0.25))
        small = rm.evaluate(proposal(pid="p2"), tick(), account(), [], SPECS).order.volume
        self.assertLess(small, big)


class GuardTests(unittest.TestCase):
    def guard(self, clock: FixedClock, d: str) -> RiskParamGuard:
        return RiskParamGuard(d, 24, clock)

    def test_riskier_change_is_delayed_24h(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            clock = FixedClock()
            g = self.guard(clock, d)
            base = risk_from_profile("prudent")
            self.assertEqual(g.request(base).status, "unchanged")
            ch = g.request(risk_from_profile("dynamic"))
            self.assertEqual((ch.status, ch.effective_at), ("delayed", T0 + timedelta(hours=24)))
            self.assertIsNone(g.due())
            clock.advance(hours=23, minutes=59)
            self.assertIsNone(g.due())
            clock.advance(minutes=2)
            self.assertEqual(g.due(), risk_from_profile("dynamic"))
            self.assertEqual(g.active(), risk_from_profile("dynamic"))
            self.assertIsNone(g.pending())

    def test_safer_change_is_immediate_and_cancels_pending_riskier(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            g = self.guard(FixedClock(), d)
            g.request(risk_from_profile("moderate"))
            g.request(risk_from_profile("dynamic"))  # en attente
            ch = g.request(risk_from_profile("prudent"))
            self.assertEqual(ch.status, "immediate")
            self.assertEqual(g.due(), risk_from_profile("prudent"))

    def test_startup_cannot_bypass_the_delay(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            clock = FixedClock()
            g = self.guard(clock, d)
            prudent, dynamic = risk_from_profile("prudent"), risk_from_profile("dynamic")
            self.assertEqual(g.resolve_startup(prudent), prudent)  # premier démarrage
            self.assertEqual(g.resolve_startup(dynamic), prudent)  # le fichier demande plus : on garde l'ancien
            self.assertIsNotNone(g.pending())
            self.assertEqual(g.resolve_startup(risk_from_profile("moderate")), prudent)
            safer = RiskConfig(risk_per_trade_pct=0.3)
            self.assertEqual(g.resolve_startup(safer), safer)  # plus prudent : appliqué
            self.assertEqual(g.active(), safer)

    def test_corrupted_active_file_refuses_startup_instead_of_accepting_config(self) -> None:
        from pathlib import Path

        from orionx.core.errors import ConfigError

        with tempfile.TemporaryDirectory() as d:
            g = self.guard(FixedClock(), d)
            g.resolve_startup(risk_from_profile("prudent"))
            Path(d, "risk_active.json").write_text('{"risk_per_trade_pct": 0.', encoding="utf-8")  # écriture interrompue
            with self.assertRaises(ConfigError):
                g.resolve_startup(risk_from_profile("dynamic"))
            Path(d, "risk_pending.json").write_text("{", encoding="utf-8")
            self.assertIsNone(g.pending())  # demande illisible : jamais appliquée

    def test_is_riskier(self) -> None:
        base = RiskConfig()
        self.assertFalse(is_riskier(base, base))
        self.assertTrue(is_riskier(base, RiskConfig(risk_per_trade_pct=0.8)))
        self.assertFalse(is_riskier(base, RiskConfig(risk_per_trade_pct=0.3)))
        self.assertTrue(is_riskier(base, RiskConfig(min_rr=1.2)))
        self.assertTrue(is_riskier(base, RiskConfig(weekend_guard=False)))
        self.assertTrue(is_riskier(base, RiskConfig(max_spread_points_by_symbol={"EURUSD": 80})))
        self.assertFalse(is_riskier(base, RiskConfig(max_spread_points_by_symbol={"EURUSD": 10})))

    def test_is_riskier_catches_every_loosening(self) -> None:
        base = RiskConfig(max_spread_points_by_symbol={"XAUUSD": 20})
        self.assertTrue(is_riskier(base, RiskConfig(max_spread_points_by_symbol={"XAUUSD": 20}, weekend_no_entry_from_utc=23)))
        self.assertTrue(is_riskier(base, RiskConfig(max_spread_points_by_symbol={"XAUUSD": 20}, approval_ttl_seconds=60)))
        self.assertTrue(is_riskier(base, RiskConfig()))  # XAUUSD retiré : son plafond remonte de 20 à 30
        self.assertFalse(is_riskier(base, RiskConfig(max_spread_points_by_symbol={"XAUUSD": 20}, weekend_no_entry_from_utc=16)))

    def test_every_risk_field_is_classified(self) -> None:
        from dataclasses import fields

        from orionx.risk.guard import HIGHER_IS_RISKIER, LOWER_IS_RISKIER, SPECIAL

        known = {*HIGHER_IS_RISKIER, *LOWER_IS_RISKIER, *SPECIAL}
        self.assertEqual({f.name for f in fields(RiskConfig)} - known, set(), "classer le nouveau champ dans guard.py")


class M7Tests(unittest.IsolatedAsyncioTestCase):
    async def build(self, rules: PositionRules, now: datetime = T0, **gwkw):
        self.clock = FixedClock(now)
        self.gw = SimulatedGateway({"EURUSD": EURUSD}, **gwkw)
        self.gw.set_tick("EURUSD", 1.08412, 1.08420, T0)
        self.pm = PositionManager(self.gw, rules, clock=self.clock)

    async def open_buy(self, vol: float = 0.20):
        await self.gw.send_market_order("EURUSD", Side.BUY, vol, 1.0820, 1.0990, 10, "t")

    async def step(self, bid: float, ask: float, atrs=None):
        self.gw.set_tick("EURUSD", bid, ask, T0)
        return await self.pm.step(await self.gw.get_positions(), {"EURUSD": Tick("EURUSD", bid, ask, T0)}, SPECS, atrs)

    async def test_atr_trailing_uses_atr_distance(self) -> None:
        await self.build(PositionRules(trail_atr_mult=1.0))
        await self.open_buy()
        await self.step(1.08412, 1.08420)
        await self.step(1.0870, 1.0871, {"EURUSD": 0.0010})  # +1,27R
        (p,) = await self.gw.get_positions()
        self.assertAlmostEqual(p.sl, 1.0860, places=5)  # prix - 1 ATR (et non prix - 1R = 1.0848)

    async def test_weekend_flat_closes_before_friday_close(self) -> None:
        await self.build(PositionRules(weekend_flat=True), datetime(2026, 10, 16, 20, 0, tzinfo=UTC))
        await self.open_buy()
        self.assertEqual(await self.step(1.08412, 1.08420), [])  # vendredi 20h00 UTC : trop tôt
        self.clock.now = datetime(2026, 10, 16, 20, 45, tzinfo=UTC)
        acts = await self.step(1.08412, 1.08420)
        self.assertTrue(acts[0].startswith("WEEKEND_FLAT"), acts)
        self.assertEqual(await self.gw.get_positions(), [])

    async def test_weekend_flat_disabled_by_default(self) -> None:
        await self.build(PositionRules(), datetime(2026, 10, 16, 20, 45, tzinfo=UTC))
        await self.open_buy()
        await self.step(1.08412, 1.08420)
        self.assertEqual(len(await self.gw.get_positions()), 1)

    async def test_foreign_positions_can_be_left_alone(self) -> None:
        await self.build(PositionRules(manage_foreign=False))
        self.gw.next_magic = 999  # position ouverte à la main
        await self.open_buy()
        await self.step(1.08412, 1.08420)
        acts = await self.step(1.0870, 1.0871)
        self.assertEqual(acts, [])
        (p,) = await self.gw.get_positions()
        self.assertEqual(p.sl, 1.0820)

    async def test_foreign_positions_are_managed_by_default(self) -> None:
        await self.build(PositionRules())
        self.gw.next_magic = 999
        await self.open_buy()
        await self.step(1.08412, 1.08420)
        acts = await self.step(1.0870, 1.0871)
        self.assertTrue(any(a.startswith("STOP_MOVED") for a in acts), acts)

    def test_choose_action(self) -> None:
        news, other = "annonce imminente (USD CPI)", "volatilité anormale"
        self.assertEqual(choose_action([other], PositionRules()), "TIGHTEN")
        self.assertEqual(choose_action([news], PositionRules(news_action="reduce")), "REDUCE")
        self.assertEqual(choose_action([news], PositionRules(news_action="close")), "CLOSE")
        self.assertEqual(choose_action([other, news], PositionRules(thesis_reduce_min_reasons=2)), "REDUCE")
        self.assertEqual(choose_action([other, news], PositionRules(thesis_exit_min_reasons=2, thesis_reduce_min_reasons=1)), "CLOSE")

    async def test_news_reduce_halves_position_and_tightens(self) -> None:
        await self.build(PositionRules(news_action="reduce"))
        await self.open_buy()
        self.gw.set_tick("EURUSD", 1.0870, 1.0871, T0)
        (pos,) = await self.gw.get_positions()
        acts = await self.pm.on_thesis_degraded(pos, ("annonce imminente (USD CPI)",), await self.gw.get_tick("EURUSD"), EURUSD)
        self.assertTrue(any(a.startswith("THESIS_REDUCE") for a in acts), acts)
        self.assertTrue(any(a.startswith("THESIS_STOP") for a in acts), acts)
        (p,) = await self.gw.get_positions()
        self.assertEqual(p.volume, 0.10)
        self.assertGreater(p.sl, 1.0840)

    async def test_news_close_closes_position(self) -> None:
        await self.build(PositionRules(news_action="close"))
        await self.open_buy()
        (pos,) = await self.gw.get_positions()
        acts = await self.pm.on_thesis_degraded(pos, ("annonce imminente (USD CPI)",), await self.gw.get_tick("EURUSD"), EURUSD)
        self.assertTrue(acts[0].startswith("THESIS_EXIT"))
        self.assertEqual(await self.gw.get_positions(), [])


if __name__ == "__main__":
    unittest.main()
