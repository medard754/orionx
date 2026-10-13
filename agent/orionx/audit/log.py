"""Journal d'audit (T-03) : SQLite, ajout seul, chaîné par hachage.

Chaque ligne contient le hachage de la précédente : modifier, supprimer ou réordonner une ligne
casse la chaîne et `verify()` le détecte. Les triggers SQL interdisent UPDATE/DELETE ; la vérification
reste la vraie protection (un attaquant avec accès au fichier pourrait supprimer les triggers).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from orionx.core.bus import EventBus
from orionx.core.models import utcnow

GENESIS = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit: ajout seul'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit: ajout seul'); END;
"""


def to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in obj]
    return obj


def _digest(prev: str, ts: str, kind: str, payload: str) -> str:
    return hashlib.sha256(f"{prev}|{ts}|{kind}|{payload}".encode()).hexdigest()


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    first_bad_id: int | None = None
    reason: str = ""


class AuditLog:
    def __init__(self, path: str | Path = ":memory:", clock: Callable[[], datetime] = utcnow) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()
        self._clock = clock

    def append(self, kind: str, payload: Any = None) -> int:
        body = json.dumps(to_jsonable(payload or {}), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            row = self._db.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS
            ts = self._clock().isoformat()
            cur = self._db.execute(
                "INSERT INTO audit (ts, kind, payload, prev_hash, hash) VALUES (?, ?, ?, ?, ?)",
                (ts, kind, body, prev, _digest(prev, ts, kind, body)),
            )
            self._db.commit()
            return int(cur.lastrowid or 0)

    def verify(self) -> VerifyResult:
        prev, n, last_id = GENESIS, 0, 0
        with self._lock:
            rows = self._db.execute("SELECT id, ts, kind, payload, prev_hash, hash FROM audit ORDER BY id").fetchall()
        for rid, ts, kind, payload, prev_hash, h in rows:
            if prev_hash != prev:
                return VerifyResult(False, n, rid, "chaîne rompue (ligne supprimée ou réordonnée)")
            if _digest(prev, ts, kind, payload) != h:
                return VerifyResult(False, n, rid, "contenu modifié")
            prev, n, last_id = h, n + 1, rid
        del last_id
        return VerifyResult(True, n)

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT id, ts, kind, payload FROM audit ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [{"id": i, "ts": ts, "kind": k, "payload": json.loads(p)} for i, ts, k, p in reversed(rows)]

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM audit").fetchone()[0])

    def close(self) -> None:
        self._db.close()


def attach(bus: EventBus, audit: AuditLog) -> None:
    """Journalise les événements importants du bus (les ticks sont volontairement exclus)."""
    from orionx.core import events as ev

    async def record(event: Any) -> None:
        audit.append(type(event).__name__, event)

    for cls in (
        ev.TradeProposed, ev.TradeApproved, ev.TradeRejected, ev.OrderFilled, ev.OrderFailed,
        ev.CircuitBreakerTripped, ev.KillSwitchActivated, ev.DataStale, ev.DataRecovered, ev.PositionAdjusted,
    ):  # fmt: skip
        bus.subscribe(cls, record)
