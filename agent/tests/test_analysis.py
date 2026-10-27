import math
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orionx.analysis.calendar import EconomicCalendar, currencies_of
from orionx.analysis.filters import FILTERS, combine, no_high_vol, sessions, trend_aligned
from orionx.analysis.state import MarketState, Regime, Session, analyze, session_of
from orionx.analysis.thesis import ThesisMonitor
from orionx.analysis.tzutil import _last_sunday, eet_offset, estimate_offset, eu_dst_active, server_to_utc_eet
from orionx.backtest.runner import run_backtest
from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.config import AnalysisConfig, RiskConfig, load_config
from orionx.core.errors import ConfigError
from orionx.core.models import Bar, Position, Side
from orionx.positions.manager import PositionManager, PositionRules
from orionx.risk.approval import ApprovalSigner
from orionx.risk.manager import RiskManager
from orionx.strategies.trend_pullback import TrendPullback
from tests.helpers import EURUSD, SPECS, T0, FixedClock, account, proposal, synthetic_bars, tick

UTC = UTC
CFG = AnalysisConfig()


def mkbar(i: int, o: float, c: float, rng: float = 0.0002) -> Bar:
    return Bar(T0 + timedelta(hours=i), o, max(o, c) + rng, min(o, c) - rng, c, 100.0)


def trend_bars(n: int, step: float) -> list[Bar]:
    bars, price = [], 1.10
    for i in range(n):
        bars.append(mkbar(i, price, price + step))
        price += step
    return bars


def range_bars(n: int) -> list[Bar]:
    closes = [1.10 + 0.001 * math.sin(i * 0.7) for i in range(n)]
    return [mkbar(i, closes[i - 1] if i else closes[0], closes[i]) for i in range(n)]


def calm_then_spike(calm: int = 200, spike: int = 6) -> list[Bar]:
    bars = [mkbar(i, 1.10, 1.1001 if i % 2 else 1.0999, 0.0002) for i in range(calm)]
    for j in range(spike):
        bars.append(mkbar(calm + j, 1.10, 1.102 if j % 2 else 1.098, 0.002))
    return bars


class StateTests(unittest.TestCase):
    def regime(self, bars) -> Regime:
        return analyze("EURUSD", bars, CFG, lambda t: t).regime

    def test_uptrend(self) -> None:
        self.assertEqual(self.regime(trend_bars(200, 0.0004)), Regime.TREND_UP)

    def test_downtrend(self) -> None:
        self.assertEqual(self.regime(trend_bars(200, -0.0004)), Regime.TREND_DOWN)

    def test_range(self) -> None:
        self.assertEqual(self.regime(range_bars(220)), Regime.RANGE)

    def test_high_volatility(self) -> None:
        st = analyze("EURUSD", calm_then_spike(), CFG, lambda t: t)
        self.assertEqual(st.regime, Regime.HIGH_VOL)
        self.assertGreater(st.volatility_ratio, CFG.high_vol_ratio)

    def test_not_enough_bars_is_unknown(self) -> None:
        self.assertEqual(self.regime(trend_bars(50, 0.0004)), Regime.UNKNOWN)

    def test_sessions(self) -> None:
        d = lambda h, m=0: datetime(2026, 10, 14, h, m, tzinfo=UTC)  # noqa: E731
        self.assertEqual(
            [session_of(d(h)) for h in (3, 8, 13, 18, 22)],
            [Session.ASIA, Session.LONDON, Session.OVERLAP, Session.NEW_YORK, Session.OFF],
        )
        self.assertEqual(session_of(d(23, 30)), Session.ASIA)

    def test_state_uses_utc_conversion_for_session(self) -> None:
        st = analyze("EURUSD", trend_bars(200, 0.0004), CFG, lambda t: t + timedelta(hours=10))
        self.assertEqual(st.utc_time, st.bar_time + timedelta(hours=10))


class TimezoneTests(unittest.TestCase):
    def test_eet_offsets(self) -> None:
        self.assertEqual(eet_offset(datetime(2026, 7, 1, 12, tzinfo=UTC)), timedelta(hours=3))
        self.assertEqual(eet_offset(datetime(2026, 12, 1, 12, tzinfo=UTC)), timedelta(hours=2))
        self.assertEqual(server_to_utc_eet(datetime(2026, 7, 1, 12, tzinfo=UTC)), datetime(2026, 7, 1, 9, tzinfo=UTC))

    def test_dst_boundary_is_last_sunday_of_october(self) -> None:
        d = _last_sunday(2026, 10)
        self.assertEqual((d.day, d.weekday()), (25, 6))
        self.assertTrue(eu_dst_active(d - timedelta(minutes=1)))
        self.assertFalse(eu_dst_active(d))

    def test_estimate_offset_rounds_to_half_hour(self) -> None:
        local = datetime(2026, 10, 14, 5, 53, 57, tzinfo=UTC)
        self.assertEqual(estimate_offset(local + timedelta(hours=3, seconds=0.4), local), timedelta(hours=3))
        self.assertEqual(estimate_offset(local + timedelta(hours=2, seconds=-1.2), local), timedelta(hours=2))


class CalendarTests(unittest.TestCase):
    def cal(self, text: str) -> EconomicCalendar:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.csv"
            p.write_text(text, encoding="utf-8")
            return EconomicCalendar.from_csv(p)

    def test_blackout_window_is_inclusive(self) -> None:
        cal = self.cal("# commentaire\ndatetime_utc,currency,impact,title\n2026-10-15T12:30:00Z,USD,high,CPI\n")
        ev = datetime(2026, 10, 15, 12, 30, tzinfo=UTC)
        self.assertIsNotNone(cal.blackout("EURUSD", ev - timedelta(minutes=30), 30, 30))
        self.assertIsNone(cal.blackout("EURUSD", ev - timedelta(minutes=31), 30, 30))
        self.assertIsNotNone(cal.blackout("EURUSD", ev + timedelta(minutes=30), 30, 30))
        self.assertIsNone(cal.blackout("EURUSD", ev + timedelta(minutes=31), 30, 30))

    def test_currency_mapping_and_impact(self) -> None:
        cal = self.cal("2026-10-15T12:30:00Z,USD,high,CPI\n2026-10-15T09:00:00Z,EUR,medium,PMI\n")
        self.assertEqual(currencies_of("EURUSD"), {"EUR", "USD"})
        now = datetime(2026, 10, 15, 12, 30, tzinfo=UTC)
        self.assertIsNotNone(cal.blackout("XAUUSD", now, 5, 5))  # l'or réagit au dollar
        self.assertIsNone(cal.blackout("GBPJPY", now, 5, 5))
        at9 = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
        self.assertIsNone(cal.blackout("EURUSD", at9, 5, 5, "high"))
        self.assertIsNotNone(cal.blackout("EURUSD", at9, 5, 5, "medium"))
        self.assertEqual(len(cal.upcoming("EURUSD", at9 - timedelta(hours=1), 24, "medium")), 2)

    def test_bad_line_reports_line_number(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            self.cal("2026-10-15T12:30:00Z,USD,high,CPI\nn'importe quoi,USD,high,x\n")
        self.assertIn("ligne 2", str(ctx.exception))

    def test_unknown_impact_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            self.cal("2026-10-15T12:30:00Z,USD,enorme,CPI\n")


def state(regime: Regime, session: Session = Session.LONDON) -> MarketState:
    return MarketState("EURUSD", T0, T0, regime, 1.0, 0.0, 0.5, 0.001, session)


def position(side: Side = Side.BUY, ticket: int = 1) -> Position:
    return Position(ticket, "EURUSD", side, 0.1, 1.0842, 1.0820, 1.0950, 0.0, T0)


class ThesisTests(unittest.TestCase):
    def test_reasons_reported_once_per_change(self) -> None:
        m, p = ThesisMonitor(), position()
        self.assertIsNone(m.check(p, state(Regime.TREND_UP), T0))
        self.assertEqual(m.check(p, state(Regime.TREND_DOWN), T0), ("régime opposé à la position",))
        self.assertIsNone(m.check(p, state(Regime.TREND_DOWN), T0))  # pas de répétition
        self.assertEqual(m.check(p, state(Regime.RANGE), T0), ("tendance perdue",))
        self.assertEqual(m.check(p, state(Regime.HIGH_VOL), T0), ("volatilité anormale",))

    def test_sell_is_symmetric(self) -> None:
        m, p = ThesisMonitor(), position(Side.SELL)
        m.check(p, state(Regime.TREND_DOWN), T0)
        self.assertEqual(m.check(p, state(Regime.TREND_UP), T0), ("régime opposé à la position",))

    def test_news_reason(self) -> None:
        m = ThesisMonitor(lambda sym, now: "USD CPI")
        out = m.check(position(), state(Regime.TREND_UP), T0)
        self.assertEqual(out, ("annonce imminente (USD CPI)",))

    def test_unknown_state_only_checks_news(self) -> None:
        self.assertIsNone(ThesisMonitor().check(position(), state(Regime.UNKNOWN), T0))
        self.assertIsNone(ThesisMonitor().check(position(), None, T0))


class RiskExtraChecksTests(unittest.TestCase):
    def test_extra_check_rejects_with_its_code(self) -> None:
        clock = FixedClock()
        rm = RiskManager(RiskConfig(), ApprovalSigner(key=b"k" * 32, clock=clock), clock=clock,
                         extra_checks=(lambda p, t, n: "NEWS_BLACKOUT",))  # fmt: skip
        rm.start(account())
        d = rm.evaluate(proposal(), tick(), account(), [], SPECS)
        self.assertFalse(d.approved)
        self.assertIn("NEWS_BLACKOUT", d.reasons)

    def test_extra_check_none_means_pass(self) -> None:
        clock = FixedClock()
        rm = RiskManager(RiskConfig(), ApprovalSigner(key=b"k" * 32, clock=clock), clock=clock,
                         extra_checks=(lambda p, t, n: None,))  # fmt: skip
        rm.start(account())
        self.assertTrue(rm.evaluate(proposal(), tick(), account(), [], SPECS).approved)


class ThesisReactionTests(unittest.IsolatedAsyncioTestCase):
    async def setup(self, rules: PositionRules):
        gw = SimulatedGateway({"EURUSD": EURUSD})
        gw.set_tick("EURUSD", 1.08412, 1.08420, T0)
        await gw.send_market_order("EURUSD", Side.BUY, 0.20, 1.0820, 1.0990, 10, "t")
        gw.set_tick("EURUSD", 1.0870, 1.0871, T0)
        (pos,) = await gw.get_positions()
        return gw, PositionManager(gw, rules), pos, await gw.get_tick("EURUSD")

    async def test_degraded_thesis_tightens_stop(self) -> None:
        gw, pm, pos, tk = await self.setup(PositionRules())
        acts = await pm.on_thesis_degraded(pos, ("régime opposé à la position",), tk, EURUSD)
        self.assertTrue(acts[0].startswith("THESIS_STOP"), acts)
        (p,) = await gw.get_positions()
        self.assertGreater(p.sl, 1.0840)

    async def test_exit_when_enough_reasons(self) -> None:
        gw, pm, pos, tk = await self.setup(PositionRules(thesis_exit_min_reasons=2))
        one = await pm.on_thesis_degraded(pos, ("a",), tk, EURUSD)
        self.assertTrue(one[0].startswith("THESIS_STOP"))
        (pos,) = await gw.get_positions()
        two = await pm.on_thesis_degraded(pos, ("a", "b"), tk, EURUSD)
        self.assertTrue(two[0].startswith("THESIS_EXIT"))
        self.assertEqual(await gw.get_positions(), [])


class FilterAndConfigTests(unittest.TestCase):
    def test_filters(self) -> None:
        buy, sell = proposal(Side.BUY), proposal(Side.SELL, 1.0863, 1.0775)
        self.assertTrue(trend_aligned(state(Regime.TREND_UP), buy))
        self.assertFalse(trend_aligned(state(Regime.TREND_UP), sell))
        self.assertFalse(no_high_vol(state(Regime.HIGH_VOL), buy))
        self.assertTrue(sessions(Session.LONDON)(state(Regime.RANGE, Session.LONDON), buy))
        self.assertFalse(combine(no_high_vol, trend_aligned)(state(Regime.RANGE), buy))
        self.assertIsNone(FILTERS["none"])

    def test_backtest_filter_blocks_and_counts(self) -> None:
        bars = synthetic_bars(3000, seed=5)
        base = run_backtest(bars, TrendPullback(), EURUSD)
        blocked = run_backtest(bars, TrendPullback(), EURUSD, entry_filter=lambda s, p: False)
        self.assertEqual(blocked.trades, 0)
        self.assertGreaterEqual(blocked.filtered, base.trades)
        permissive = run_backtest(bars, TrendPullback(), EURUSD, entry_filter=lambda s, p: True)
        self.assertEqual((permissive.trades, permissive.net_profit), (base.trades, base.net_profit))
        self.assertEqual(permissive.filtered, 0)

    def test_analysis_config_validation_and_loading(self) -> None:
        with self.assertRaises(ConfigError):
            AnalysisConfig(news_min_impact="enorme")
        with self.assertRaises(ConfigError):
            AnalysisConfig(high_vol_ratio=0.5)
        cfg = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
        self.assertEqual(cfg.analysis.news_min_impact, "high")


if __name__ == "__main__":
    unittest.main()


class ConfigErrorMessageTests(unittest.TestCase):
    def load(self, text: str):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.yaml"
            p.write_text(text, encoding="utf-8")
            return load_config(p)

    def test_duplicated_section_gives_a_clear_message(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            self.load("risk:\n  profile: prudent\n  risk:\n    risk_per_trade_pct: 0.5\n")
        msg = str(ctx.exception)
        self.assertIn("Section « risk »", msg)
        self.assertIn("'risk'", msg)
        self.assertIn("deux fois", msg)

    def test_unknown_key_names_the_section(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            self.load("positions:\n  trail_rr: 2\n")
        self.assertIn("Section « positions »", str(ctx.exception))

    def test_tab_indentation_is_reported(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            self.load("risk:\n\tprofile: prudent\n")
        self.assertIn("illisible", str(ctx.exception))

    def test_example_config_has_exposure_line_and_loads(self) -> None:
        cfg = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
        self.assertEqual(cfg.risk.max_currency_exposure_pct, 2.0)
