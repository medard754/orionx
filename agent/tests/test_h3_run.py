"""H-3 : l'exécution du développement refuse des données modifiées, ne s'exécute qu'une fois, et ne lit jamais la réserve."""

import random
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from orionx.backtest import h3_run
from orionx.backtest.h3_freeze import Row, freeze, snapshot_name

SYMBOLS = ("GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURUSD")


def synthetic(symbol: str, tf: str, year: int) -> list[Row]:
    step = 3600 if tf == "H1" else 86400
    n = (24 * 100 if tf == "H1" else 250) if year < 2013 else 24
    rng = random.Random(f"{symbol}{tf}{year}")
    price, rows = 1.1, []
    t0 = int(datetime(year, 1, 3, tzinfo=UTC).timestamp())
    for k in range(n):
        o = price
        c = o + rng.gauss(0, 0.0008 if tf == "H1" else 0.004)
        hi, lo = max(o, c) + abs(rng.gauss(0, 0.0003)), min(o, c) - abs(rng.gauss(0, 0.0003))
        rows.append((t0 + k * step, o, hi, lo, c, 100, 3))
        price = c
    return rows


def make_frozen(tmp: str) -> dict:
    return freeze(synthetic, Path(tmp), symbols=SYMBOLS, timeframes=("H1", "D1"), first_year=2010, last_year=2013, server="Faux-Demo",
                  commission_per_lot_per_side=3.5, clock=lambda: datetime(2014, 1, 1, tzinfo=UTC), log=lambda _m: None)  # fmt: skip


class RunTests(unittest.TestCase):
    def test_runs_once_on_intact_data_and_writes_the_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = make_frozen(tmp)
            lines, any_passed = h3_run.run_development(Path(tmp), manifest, permutations=20, plan_draws=10, log=lambda _m: None)
            text = "\n".join(lines)
            for family in ("chandeliers", "figures", "divergences", "Fibonacci", "étranglement", "plan de trading"):
                self.assertIn(family, text)
            self.assertIn("réserve non lue", text)
            self.assertTrue((Path(tmp) / h3_run.MARKER).exists())
            self.assertFalse(any_passed, "un hasard pur ne passe aucune famille")
            self.assertIn("NO-GO", text)

    def test_a_second_run_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = make_frozen(tmp)
            h3_run.run_development(Path(tmp), manifest, permutations=10, plan_draws=5, log=lambda _m: None)
            with self.assertRaises(h3_run.H3Refused):
                h3_run.run_development(Path(tmp), manifest, permutations=10, plan_draws=5, log=lambda _m: None)

    def test_modified_data_is_refused_before_any_measure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = make_frozen(tmp)
            path = Path(tmp) / snapshot_name("GBPUSD", "H1")
            path.write_text(path.read_text(encoding="utf-8").replace("1.1", "1.2", 1), encoding="utf-8")
            with self.assertRaises(h3_run.H3Refused):
                h3_run.run_development(Path(tmp), manifest, permutations=10, plan_draws=5, log=lambda _m: None)
            self.assertFalse((Path(tmp) / h3_run.MARKER).exists(), "un refus n'épuise pas l'unique exécution")


if __name__ == "__main__":
    unittest.main()
