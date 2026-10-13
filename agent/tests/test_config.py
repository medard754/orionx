import tempfile
import unittest
from pathlib import Path

from orionx.core.config import AppConfig, RiskConfig, load_config
from orionx.core.errors import ConfigError


class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid(self) -> None:
        self.assertEqual(AppConfig().risk.risk_per_trade_pct, 0.5)

    def test_absolute_bounds(self) -> None:
        for field, value in [("risk_per_trade_pct", 2.0), ("max_daily_loss_pct", 9.0), ("max_open_positions", 20),
                             ("max_drawdown_pct", 50.0), ("min_rr", 0.5)]:  # fmt: skip
            with self.subTest(field=field), self.assertRaises(ConfigError):
                RiskConfig(**{field: value})

    def test_live_mode_refused(self) -> None:
        with self.assertRaises(ConfigError):
            AppConfig(mode="live")

    def test_load_example_file(self) -> None:
        path = Path(__file__).resolve().parents[1] / "config.example.yaml"
        cfg = load_config(path)
        self.assertEqual(cfg.symbols, ("EURUSD", "GBPUSD", "XAUUSD"))

    def test_unknown_key_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.yaml"
            p.write_text("mode: demo\nbogus: 1\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(p)


if __name__ == "__main__":
    unittest.main()
