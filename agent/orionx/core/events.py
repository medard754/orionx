"""Événements échangés sur le bus interne."""

from __future__ import annotations

from dataclasses import dataclass

from orionx.core.models import ApprovedOrder, OrderResult, TradeProposal


@dataclass(frozen=True)
class TradeProposed:
    proposal: TradeProposal


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
