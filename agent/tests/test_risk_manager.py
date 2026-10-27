import random
import unittest
from datetime import timedelta

from orionx.core.config import RiskConfig
from orionx.core.models import Position, Side, Tick
from orionx.risk.sizing import risk_per_lot
from tests.helpers import EURUSD, SPECS, T0, account, manager, proposal, tick


def pos(vol: float = 0.1, sl: float | None = 1.0820, side: Side = Side.BUY, ticket: int = 1) -> Position:
    return Position(ticket, "EURUSD", side, vol, 1.0842, sl, 1.0910, 0.0, T0)


class RiskManagerTests(unittest.TestCase):
    def ev(self, rm, prop=None, tk=None, acc=None, positions=()):
        return rm.evaluate(prop or proposal(), tk or tick(), acc or account(), list(positions), SPECS)

    def test_valid_buy_is_approved_and_signed(self) -> None:
        rm, signer, _ = manager()
        d = self.ev(rm)
        self.assertTrue(d.approved, d.reasons)
        self.assertEqual(d.order.volume, 0.22)
        signer.verify(d.order)

    def test_valid_sell_is_approved(self) -> None:
        rm, _, _ = manager()
        d = self.ev(rm, proposal(Side.SELL, sl=1.0863, tp=1.0775))
        self.assertTrue(d.approved, d.reasons)

    def test_wrong_side_stop_rejected(self) -> None:
        rm, _, _ = manager()
        self.assertIn("INVALID_SL", self.ev(rm, proposal(sl=1.0900)).reasons)

    def test_low_rr_rejected(self) -> None:
        rm, _, _ = manager()
        self.assertIn("RR_TOO_LOW", self.ev(rm, proposal(tp=1.0850)).reasons)

    def test_wide_spread_rejected(self) -> None:
        rm, _, _ = manager()
        self.assertIn("SPREAD_TOO_WIDE", self.ev(rm, tk=tick(1.08400, 1.08460)).reasons)

    def test_stale_data_rejected(self) -> None:
        rm, _, clock = manager()
        clock.advance(seconds=10)
        d = self.ev(rm, proposal(valid_s=600))
        self.assertEqual(d.reasons, ("STALE_DATA",))

    def test_server_clock_offset_does_not_hide_stale_data(self) -> None:
        """Le courtier est à UTC+3 : son horodatage est 3 h « dans le futur ». L'âge doit venir de received_at."""
        rm, _, clock = manager()
        server_time = clock.now + timedelta(hours=3)
        stale = Tick("EURUSD", 1.08412, 1.08420, server_time, received_at=clock.now - timedelta(seconds=30))
        fresh = Tick("EURUSD", 1.08412, 1.08420, server_time, received_at=clock.now - timedelta(seconds=1))
        self.assertEqual(self.ev(rm, tk=stale).reasons, ("STALE_DATA",))
        self.assertTrue(self.ev(rm, tk=fresh).approved)

    def test_expired_proposal_rejected(self) -> None:
        rm, _, clock = manager()
        clock.advance(seconds=1)
        d = self.ev(rm, proposal(valid_s=0.5), tk=tick(at=clock.now))
        self.assertEqual(d.reasons, ("PROPOSAL_EXPIRED",))

    def test_live_account_rejected(self) -> None:
        rm, _, _ = manager()
        self.assertEqual(self.ev(rm, acc=account(demo=False)).reasons, ("LIVE_DISABLED",))

    def test_max_positions(self) -> None:
        rm, _, _ = manager()
        d = self.ev(rm, positions=[pos(0.01, ticket=i) for i in range(3)])
        self.assertIn("MAX_POSITIONS", d.reasons)

    def test_unprotected_position_blocks_everything(self) -> None:
        rm, _, _ = manager()
        self.assertIn("UNPROTECTED_POSITION", self.ev(rm, positions=[pos(sl=None)]).reasons)

    def test_low_margin_level_rejected(self) -> None:
        rm, _, _ = manager()
        self.assertIn("MARGIN_LEVEL_LOW", self.ev(rm, acc=account(margin_level=150.0)).reasons)

    def test_total_risk_reduces_volume(self) -> None:
        rm, _, _ = manager()
        d = self.ev(rm, positions=[pos(vol=0.8)])  # 0,8 lot × 220 $ = 176 $ = 1,76 % ouvert
        self.assertTrue(d.approved, d.reasons)
        self.assertEqual(d.order.volume, 0.10)
        self.assertIn("VOLUME_REDUCED", d.notes)

    def test_total_risk_exceeded(self) -> None:
        rm, _, _ = manager()
        d = self.ev(rm, positions=[pos(vol=1.0)])  # 2,2 % > 2 %
        self.assertIn("TOTAL_RISK_EXCEEDED", d.reasons)

    def test_daily_loss_halts_then_resets_next_day(self) -> None:
        rm, _, clock = manager()
        rm.on_account_update(account(10_000, equity=9_690))  # -3,1 %
        self.assertTrue(rm.halted)
        self.assertTrue(self.ev(rm, acc=account(9_690)).reasons[0].startswith("HALTED:DAILY_LOSS_LIMIT"))
        clock.advance(days=1)
        rm.on_account_update(account(9_690))
        self.assertFalse(rm.halted)

    def test_drawdown_halt_is_not_auto_released(self) -> None:
        rm, _, clock = manager()
        rm.on_account_update(account(10_000, equity=8_900))  # -11 %
        self.assertIn("MAX_DRAWDOWN", rm.halt_reasons)
        clock.advance(days=2)
        rm.on_account_update(account(8_900))
        self.assertIn("MAX_DRAWDOWN", rm.halt_reasons)
        rm.resume("MAX_DRAWDOWN")
        rm.resume("WEEKLY_LOSS_LIMIT")
        self.assertFalse(rm.halted)

    def test_consecutive_losses_halt(self) -> None:
        rm, _, clock = manager()
        for _ in range(4):
            rm.on_trade_closed(-10.0)
        self.assertIn("CONSECUTIVE_LOSSES", rm.halt_reasons)
        clock.advance(days=1)
        rm.on_account_update(account())
        self.assertNotIn("CONSECUTIVE_LOSSES", rm.halt_reasons)

    def test_manual_halt_blocks(self) -> None:
        rm, _, _ = manager()
        rm.halt("KILL_SWITCH")
        self.assertFalse(self.ev(rm).approved)

    def test_randomized_invariants(self) -> None:
        """Jamais d'ordre approuvé hors limites, quelles que soient les entrées."""
        rnd = random.Random(42)
        cfg = RiskConfig()
        rm, _, clock = manager(cfg)
        for i in range(3000):
            bid = rnd.uniform(1.05, 1.15)
            spread = rnd.uniform(0.00001, 0.0008)
            tk = tick(bid, bid + spread, at=clock.now)
            side = rnd.choice([Side.BUY, Side.SELL])
            dist = rnd.uniform(0.0001, 0.02)
            entry = tk.ask if side is Side.BUY else tk.bid
            sgn = 1 if side is Side.BUY else -1
            prop = proposal(side, sl=entry - sgn * dist, tp=entry + sgn * dist * rnd.uniform(0.3, 4), pid=f"r{i}")
            bal = rnd.uniform(200, 500_000)
            acc = account(bal)
            d = rm.evaluate(prop, tk, acc, [], SPECS)
            if not d.approved:
                continue
            o = d.order
            self.assertGreater(o.sl, 0)
            self.assertLessEqual(o.volume * risk_per_lot(EURUSD, entry, o.sl), bal * cfg.risk_per_trade_pct / 100 + 0.01)
            self.assertGreaterEqual(o.volume, EURUSD.volume_min)
            self.assertLessEqual(o.volume, EURUSD.volume_max)
            self.assertLessEqual(tk.spread / EURUSD.point, cfg.max_spread_points)


if __name__ == "__main__":
    unittest.main()
