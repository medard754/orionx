"""Événements échangés sur le bus interne."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from orionx.core.models import ApprovedOrder, Bar, OrderResult, Tick, TradeProposal


@dataclass(frozen=True)
class TradeProposed:
    proposal: TradeProposal


@dataclass(frozen=True)
class SignalContext:
    """Ce qu'il faut pour REJOUER un signal plus tard (`signals-report`) : prix d'entrée de référence et bougie d'ancrage.
    `anchor_bar` est l'heure (courtier) de la dernière bougie fermée au moment du signal : on ne la compare jamais à l'horloge locale."""

    proposal_id: str
    symbol: str
    timeframe: str
    anchor_bar: datetime
    entry: float  # achat : ask ; vente : bid, au moment du signal
    spread: float  # ask - bid au moment du signal (unités de prix)


@dataclass(frozen=True)
class TradeApproved:
    order: ApprovedOrder


@dataclass(frozen=True)
class TradeRejected:
    proposal_id: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class OrderFilled:
    order: ApprovedOrder
    result: OrderResult


@dataclass(frozen=True)
class OrderFailed:
    order: ApprovedOrder
    result: OrderResult


@dataclass(frozen=True)
class CircuitBreakerTripped:
    reason: str


@dataclass(frozen=True)
class KillSwitchActivated:
    level: int  # 1 pause, 2 protection, 3 clôture totale
    reason: str


@dataclass(frozen=True)
class TickReceived:
    tick: Tick


@dataclass(frozen=True)
class BarClosed:
    symbol: str
    timeframe: str
    bar: Bar


@dataclass(frozen=True)
class DataStale:
    symbol: str
    age_seconds: float


@dataclass(frozen=True)
class DataRecovered:
    symbol: str


@dataclass(frozen=True)
class PositionAdjusted:
    ticket: int
    action: str
    detail: str


@dataclass(frozen=True)
class MarketStateUpdated:
    symbol: str
    regime: str
    session: str
    volatility_ratio: float
    trend_strength: float
    efficiency: float
    atr: float


@dataclass(frozen=True)
class ThesisDegraded:
    ticket: int
    symbol: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionQuality:
    proposal_id: str
    symbol: str
    ok: bool
    attempts: int
    latency_ms: float
    slippage_points: float | None
    note: str


@dataclass(frozen=True)
class DecisionNote:
    symbol: str
    notes: tuple[str, ...]
