import tempfile
import unittest
from pathlib import Path
import sqlite3

from orionx.audit.log import AuditLog, attach
from orionx.core.bus import EventBus
from orionx.core.events import KillSwitchActivated, OrderFilled, TradeRejected
from orionx.core.models import ApprovedOrder, OrderResult, Side
from tests.helpers import T0


class AuditTests(unittest.IsolatedAsyncioTestCase):
    def filled(self, n: int = 3) -> AuditLog:
        log = AuditLog()
        for i in range(n):
            log.append("evt", {"i": i})
        return log

    def test_chain_is_valid(self) -> None:
        r = self.filled(5).verify()
        self.assertTrue(r.ok)
        self.assertEqual(r.checked, 5)

    def test_empty_log_is_valid(self) -> None:
        self.assertTrue(AuditLog().verify().ok)

    def test_update_blocked_by_trigger(self) -> None:
        log = self.filled()
        with self.assertRaises(sqlite3.DatabaseError):
            log._db.execute("UPDATE audit SET payload='{}' WHERE id=1")

    def test_delete_blocked_by_trigger(self) -> None:
        log = self.filled()
        with self.assertRaises(sqlite3.DatabaseError):
            log._db.execute("DELETE FROM audit WHERE id=1")

    def test_tampering_detected_even_without_triggers(self) -> None:
        log = self.filled()
        log._db.execute("DROP TRIGGER audit_no_update")
        log._db.execute("UPDATE audit SET payload='{\"i\":99}' WHERE id=2")
        r = log.verify()
        self.assertFalse(r.ok)
        self.assertEqual(r.first_bad_id, 2)

    def test_deleted_row_detected(self) -> None:
        log = self.filled()
        log._db.execute("DROP TRIGGER audit_no_delete")
        log._db.execute("DELETE FROM audit WHERE id=2")
        r = log.verify()
        self.assertFalse(r.ok)
        self.assertEqual(r.first_bad_id, 3)

    def test_persists_across_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "audit.db"
            a = AuditLog(path)
            a.append("one")
            a.close()
            b = AuditLog(path)
            b.append("two")
            self.assertEqual((b.verify().ok, b.count()), (True, 2))
            b.close()

    def test_two_writers_on_same_file_keep_one_chain(self) -> None:
        """L'agent et la commande `kill` écrivent dans le même fichier : la chaîne ne doit jamais se scinder."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "audit.db"
            a, b = AuditLog(path), AuditLog(path)
            for i in range(20):
                (a if i % 2 else b).append("evt", {"i": i})
            r = a.verify()
            self.assertEqual((r.ok, r.checked), (True, 20))
            self.assertEqual(b.verify().checked, 20)
            a.close()
            b.close()

    async def test_attach_records_bus_events(self) -> None:
        bus, log = EventBus(), AuditLog()
        attach(bus, log)
        order = ApprovedOrder("p1", "EURUSD", Side.BUY, 0.1, 1.082, 1.09, 10, T0)
        await bus.publish(TradeRejected("p9", ("RR_TOO_LOW",)))
        await bus.publish(OrderFilled(order, OrderResult(True, 1, 1.084)))
        await bus.publish(KillSwitchActivated(3, "test"))
        kinds = [e["kind"] for e in log.tail()]
        self.assertEqual(kinds, ["TradeRejected", "OrderFilled", "KillSwitchActivated"])
        self.assertEqual(log.tail()[1]["payload"]["order"]["side"], "buy")
        self.assertTrue(log.verify().ok)


if __name__ == "__main__":
    unittest.main()
