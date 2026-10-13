"""Objets du domaine. Immuables, validés à la construction.

Choix v0 : dataclasses standard (aucune dépendance). Pydantic sera introduit
pour l'API (M9) et les sorties d'IA (M3), voir docs/adr/0002.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def _aware(dt: datetime, name: str) -> None:
    _require(dt.tzinfo is not None, f"{name} doit contenir un fuseau horaire (UTC)")


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class SymbolSpec:
    name: str
    digits: int
    point: float
    tick_size: float
    tick_value: float  # gain/perte d'un tick pour 1 lot, en devise du compte
    volume_min: float
    volume_max: float
    volume_step: float

    def __post_init__(self) -> None:
        _require(self.point > 0 and self.tick_size > 0, "point/tick_size doivent être > 0")
        _require(self.tick_value > 0, "tick_value doit être > 0")
        _require(0 < self.volume_min <= self.volume_max, "volumes min/max incohérents")
        _require(self.volume_step > 0, "volume_step doit être > 0")


@dataclass(frozen=True)
class Tick:
    symbol: str
    bid: float
    ask: float
    time: datetime

    def __post_init__(self) -> None:
        _require(0 < self.bid <= self.ask, "bid/ask incohérents")
        _aware(self.time, "Tick.time")

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True)
class AccountState:
    balance: float
    equity: float
    margin_free: float
    margin_level: float | None  # None s'il n'y a aucune marge utilisée
    currency: str
    is_demo: bool


@dataclass(frozen=True)
class Position:
    ticket: int
    symbol: str
    side: Side
    volume: float
    price_open: float
    sl: float | None
    tp: float | None
    profit: float
    opened_at: datetime


@dataclass(frozen=True)
class TradeProposal:
    """Proposition produite par la couche de décision (jamais un ordre)."""

    id: str
    symbol: str
    side: Side
    sl: float
    tp: float
    confidence: float
    strategy: str
    rationale: tuple[str, ...]
    created_at: datetime
    valid_until: datetime

    def __post_init__(self) -> None:
        _require(self.sl > 0, "Le stop-loss est obligatoire")
        _require(self.tp > 0, "Le take-profit doit être > 0")
        _require(0.0 <= self.confidence <= 1.0, "confidence doit être dans [0, 1]")
        _aware(self.created_at, "created_at")
        _aware(self.valid_until, "valid_until")
        _require(self.valid_until > self.created_at, "valid_until doit suivre created_at")


@dataclass(frozen=True)
class ApprovedOrder:
    """Ordre validé par le gestionnaire de risques. Seul type accepté par M6."""

    proposal_id: str
    symbol: str
    side: Side
    volume: float
    sl: float
    tp: float
    max_slippage_points: int
    expires_at: datetime
    signature: str = ""

    def __post_init__(self) -> None:
        _require(self.volume > 0, "volume doit être > 0")
        _require(self.sl > 0, "Le stop-loss est obligatoire")
        _aware(self.expires_at, "expires_at")

    def canonical(self) -> bytes:
        payload = {
            "proposal_id": self.proposal_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "volume": self.volume,
            "sl": self.sl,
            "tp": self.tp,
            "max_slippage_points": self.max_slippage_points,
            "expires_at": self.expires_at.isoformat(),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    ticket: int | None = None
    price: float | None = None
    retcode: int | None = None
    message: str = ""

    @classmethod
    def fail(cls, message: str, retcode: int | None = None) -> OrderResult:
        return cls(ok=False, retcode=retcode, message=message)
