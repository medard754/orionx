import dataclasses
import unittest
from datetime import timedelta

from orionx.core.errors import ApprovalError
from orionx.core.models import ApprovedOrder, Side
from orionx.risk.approval import ApprovalSigner
from tests.helpers import T0, FixedClock


def order() -> ApprovedOrder:
    return ApprovedOrder("p1", "EURUSD", Side.BUY, 0.22, 1.082, 1.091, 10, T0 + timedelta(seconds=5))


class ApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FixedClock()
        self.signer = ApprovalSigner(key=b"k" * 32, clock=self.clock)

    def test_valid_signature(self) -> None:
        self.signer.verify(self.signer.sign(order()))

    def test_unsigned_rejected(self) -> None:
        with self.assertRaises(ApprovalError):
            self.signer.verify(order())

    def test_tampered_volume_rejected(self) -> None:
        bad = dataclasses.replace(self.signer.sign(order()), volume=5.0)
        with self.assertRaises(ApprovalError):
            self.signer.verify(bad)

    def test_other_key_rejected(self) -> None:
        other = ApprovalSigner(key=b"x" * 32, clock=self.clock)
        with self.assertRaises(ApprovalError):
            self.signer.verify(other.sign(order()))

    def test_expired_rejected(self) -> None:
        signed = self.signer.sign(order())
        self.clock.advance(seconds=6)
        with self.assertRaises(ApprovalError):
            self.signer.verify(signed)

    def test_order_without_stop_loss_cannot_exist(self) -> None:
        with self.assertRaises(ValueError):
            ApprovedOrder("p", "EURUSD", Side.BUY, 0.1, 0.0, 1.09, 10, T0)


if __name__ == "__main__":
    unittest.main()
