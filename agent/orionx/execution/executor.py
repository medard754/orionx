"""Exécution (M6). N'accepte QUE des ordres signés par le gestionnaire de risques."""

from __future__ import annotations

import logging

from orionx.connector_mt5.gateway import Gateway
from orionx.core.bus import EventBus
from orionx.core.events import OrderFailed, OrderFilled
from orionx.core.models import ApprovedOrder, OrderResult
from orionx.risk.approval import ApprovalSigner

log = logging.getLogger(__name__)


class Executor:
    def __init__(self, gateway: Gateway, signer: ApprovalSigner, bus: EventBus | None = None) -> None:
        self._gw, self._signer, self._bus = gateway, signer, bus
        self._results: dict[str, OrderResult] = {}  # idempotence : 1 proposition = 1 tentative
        self._blocked: str | None = None

    @property
    def blocked(self) -> str | None:
        return self._blocked

    def block(self, reason: str) -> None:
        self._blocked = reason

    def unblock(self) -> None:
        self._blocked = None

    async def execute(self, order: ApprovedOrder) -> OrderResult:
        self._signer.verify(order)  # lève ApprovalError : ordre non signé / modifié / expiré
        previous = self._results.get(order.proposal_id)
        if previous is not None:
            return previous  # jamais de doublon, même si l'appelant réessaie
        if self._blocked:
            return OrderResult.fail(f"BLOCKED:{self._blocked}")

        result = await self._gw.send_market_order(
            order.symbol, order.side, order.volume, order.sl, order.tp,
            order.max_slippage_points, f"orionx:{order.proposal_id}"[:31],
        )  # fmt: skip
        if result.ok:
            result = await self._ensure_protected(order, result)
        self._results[order.proposal_id] = result
        if self._bus:
            await self._bus.publish(OrderFilled(order, result) if result.ok else OrderFailed(order, result))
        return result

    async def _ensure_protected(self, order: ApprovedOrder, result: OrderResult) -> OrderResult:
        """Règle : jamais de position sans stop-loss. Sinon on corrige, ou on ferme."""
        positions = await self._gw.get_positions()
        pos = next((p for p in positions if p.ticket == result.ticket), None)
        if pos is None:
            self.block("PROTECTION_UNVERIFIED")
            return OrderResult.fail("POSITION_NOT_FOUND_AFTER_FILL", result.retcode)
        if pos.sl is not None and pos.tp is not None:
            return result
        log.warning("Position %s sans SL/TP : correction", pos.ticket)
        fixed = await self._gw.modify_position(pos.ticket, order.sl, order.tp)
        if fixed.ok:
            return result
        await self._gw.close_position(pos.ticket)
        self.block("PROTECTION_FAILED")
        return OrderResult.fail("PROTECTION_FAILED_POSITION_CLOSED", fixed.retcode)
