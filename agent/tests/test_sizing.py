import random
import unittest

from orionx.risk.sizing import position_size, risk_per_lot
from tests.helpers import EURUSD


class SizingTests(unittest.TestCase):
    def test_half_percent_of_10000(self) -> None:
        # SL à 220 ticks => 220 $/lot ; 0,5 % de 10 000 = 50 $ => 0,227 => 0,22 lot
        self.assertAlmostEqual(position_size(10_000, 0.5, 1.0842, 1.0820, EURUSD), 0.22)

    def test_never_exceeds_target_risk(self) -> None:
        rnd = random.Random(1)
        for _ in range(2000):
            cap = rnd.uniform(500, 200_000)
            entry = rnd.uniform(1.0, 1.3)
            sl = entry - rnd.uniform(0.0003, 0.02)
            vol = position_size(cap, 0.5, entry, sl, EURUSD)
            self.assertLessEqual(vol * risk_per_lot(EURUSD, entry, sl), cap * 0.005 + 1e-6)

    def test_zero_when_below_minimum(self) -> None:
        self.assertEqual(position_size(100, 0.5, 1.0842, 1.0620, EURUSD), 0.0)

    def test_capped_at_volume_max(self) -> None:
        self.assertEqual(position_size(1e12, 1.0, 1.0842, 1.0841, EURUSD), 100.0)

    def test_invalid_inputs(self) -> None:
        self.assertEqual(position_size(-5, 0.5, 1.0842, 1.0820, EURUSD), 0.0)
        self.assertEqual(position_size(10_000, 0.5, 1.0842, 1.0842, EURUSD), 0.0)


if __name__ == "__main__":
    unittest.main()
