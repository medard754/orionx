import unittest

from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.config import AppConfig, RiskConfig
from orionx.core.models import Side, utcnow
from orionx.riskcheck import check_proposal
from tests.helpers import EURUSD
from tests.test_agent import GBPUSD_SPEC


async def no_sleep(_: float) -> None:
    return None


class RiskCheckTests(unittest.IsolatedAsyncioTestCase):
    async def run_check(self, side: Side, **risk):
        gw = SimulatedGateway({"EURUSD": EURUSD, "GBPUSD": GBPUSD_SPEC}, balance=100_000.0)
        for s in ("EURUSD", "GBPUSD"):
            gw.set_tick(s, 1.10000, 1.10010, utcnow())
        await gw.send_market_order("GBPUSD", Side.BUY, 6.0, 1.0980, 1.1100, 10, "manuel")  # 6 lots, stop 20 pips : ~1,2 %
        cfg = AppConfig(risk=RiskConfig(weekend_guard=False, max_total_open_risk_pct=4.0, **risk))
        lines: list[str] = []
        code = await check_proposal(gw, cfg, "EURUSD", side, out=lines.append, sleep=no_sleep)
        return code, "\n".join(lines), gw

    async def test_exposure_refuses_a_second_short_usd(self) -> None:
        code, text, gw = await self.run_check(Side.BUY, max_currency_exposure_pct=1.0)
        self.assertEqual(code, 5)
        self.assertIn("CURRENCY_EXPOSURE", text)
        self.assertIn("GBPUSD buy 6.0 lot", text)
        self.assertEqual(gw.orders_sent, 1)  # seul l'ordre « manuel » de préparation : la simulation n'envoie rien

    async def test_opposite_side_is_accepted(self) -> None:
        code, text, _ = await self.run_check(Side.SELL, max_currency_exposure_pct=1.0)
        self.assertEqual(code, 0, text)
        self.assertIn("APPROUVÉ", text)

    async def test_uses_the_active_risk_config_not_config_yaml(self) -> None:
        gw = SimulatedGateway({"EURUSD": EURUSD}, balance=100_000.0)
        gw.set_tick("EURUSD", 1.10000, 1.10010, utcnow())
        yaml_cfg = AppConfig(risk=RiskConfig(weekend_guard=False, risk_per_trade_pct=1.0, max_total_open_risk_pct=4.0))
        active = RiskConfig(weekend_guard=False, risk_per_trade_pct=0.5)
        lines: list[str] = []
        code = await check_proposal(gw, yaml_cfg, "EURUSD", Side.BUY, out=lines.append, sleep=no_sleep, risk_cfg=active)
        self.assertEqual(code, 0, lines)
        self.assertIn("2.5 lot (risque cible 0.5 %", "\n".join(lines))  # 0,5 % de 100 000 sur 20 pips, pas 1 %

    async def test_positions_on_other_symbols_are_understood(self) -> None:
        code, text, _ = await self.run_check(Side.BUY)  # plafond par défaut (2 %) : lot réduit, jamais « non protégée »
        self.assertNotIn("UNPROTECTED_POSITION", text)
        self.assertEqual(code, 0, text)


if __name__ == "__main__":
    unittest.main()
