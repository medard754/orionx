"""Exécution (M6). N'accepte QUE des ordres signés par le gestionnaire de risques.

Fiabilité : 1 proposition = 1 position, même en cas de réponse ambiguë du courtier.
 - rejet « sûr » (requote, prix changé, pas de cotation) : nouvelle tentative limitée, approbation re-vérifiée ;
 - réponse ambiguë (délai dépassé, pas de connexion) : on NE renvoie PAS l'ordre, on cherche d'abord si la position
   existe déjà (marquée par son commentaire) ;
 - après exécution : contrôle du glissement, puis du stop-loss / take-profit ; sinon correction, sinon clôture.
Qualité mesurée à chaque ordre (latence, glissement, tentatives).
"""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass

from orionx.connector_mt5.gateway import Gateway
from orionx.core.bus import EventBus
from orionx.core.errors import ApprovalError
from orionx.core.events import ExecutionQuality, OrderFailed, OrderFilled
from orionx.core.models import ApprovedOrder, OrderResult, Side
from orionx.risk.approval import ApprovalSigner

log = logging.getLogger(__name__)
SAFE_RETRY = {10004, 10020, 10021}  # requote, prix modifié, pas de cotation : l'ordre n'a PAS été exécuté
AMBIGUOUS = {10012, 10031}  # délai dépassé, pas de connexion : l'ordre a peut-être été exécuté


def order_comment(order: ApprovedOrder) -> str:
    return f"orionx:{order.proposal_id}"[:31]


@dataclass(frozen=True)
class ExecutionReport:
    proposal_id: str
    symbol: str
    ok: bool
    attempts: int
    latency_ms: float
    slippage_points: float | None
    note: str = ""


class Executor:
    def __init__(self, gateway: Gateway, signer: ApprovalSigner, bus: EventBus | None = None, *,
                 max_retries: int = 2, on_slippage: str = "close") -> None:  # fmt: skip
        self._gw, self._signer, self._bus = gateway, signer, bus
        self._results: dict[str, OrderResult] = {}  # idempotence : 1 proposition = 1 tentative
        self._blocked: str | None = None
        self._max_retries = max_retries
        self._on_slippage = on_slippage  # "close" : on ferme aussitôt ; "keep" : on garde et on signale
        self.reports: list[ExecutionReport] = []

    @property
    def blocked(self) -> str | None:
        return self._blocked

    def block(self, reason: str) -> None:
        self._blocked = reason

    def unblock(self) -> None:
        self._blocked = None

    def stats(self) -> dict[str, float]:
        """Qualité d'exécution cumulée (latence, glissement, taux de réussite)."""
        r = self.reports
        if not r:
            return {"orders": 0}
        slips = [x.slippage_points for x in r if x.slippage_points is not None]
        return {
            "orders": len(r),
            "success_rate": sum(x.ok for x in r) / len(r),
            "mean_latency_ms": statistics.fmean(x.latency_ms for x in r),
            "max_latency_ms": max(x.latency_ms for x in r),
            "mean_slippage_points": statistics.fmean(slips) if slips else 0.0,
            "retries": sum(x.attempts - 1 for x in r),
        }

    async def execute(self, order: ApprovedOrder) -> OrderResult:
        self._signer.verify(order)  # lève ApprovalError : ordre non signé / modifié / expiré
        previous = self._results.get(order.proposal_id)
        if previous is not None:
            return previous  # jamais de doublon, même si l'appelant réessaie
        if self._blocked:
            return OrderResult.fail(f"BLOCKED:{self._blocked}")

        started, attempts, note, slippage = time.monotonic(), 0, "", None
        result = OrderResult.fail("aucune tentative")
        while True:
            attempts += 1
            result = await self._gw.send_market_order(
                order.symbol, order.side, order.volume, order.sl, order.tp, order.max_slippage_points, order_comment(order)
            )
            if result.ok:
                break
            if result.retcode in SAFE_RETRY and attempts <= self._max_retries:
                try:
                    self._signer.verify(order)  # l'approbation a-t-elle expiré entre-temps ?
                except ApprovalError:
                    note = "APPROBATION_EXPIREE_PENDANT_LES_TENTATIVES"
                    break
                continue
            if result.retcode in AMBIGUOUS or result.retcode is None:
                found = await self._find_executed(order)
                if found is not None:
                    result = OrderResult(True, found[0], found[1], 10009, "retrouvé après réponse ambiguë")
                    note = "RETROUVE_APRES_REPONSE_AMBIGUE"
            break
        if result.ok:
            result, slippage, extra = await self._after_fill(order, result)
            note = extra or note
        self._record(order, result, attempts, (time.monotonic() - started) * 1000, slippage, note)
        self._results[order.proposal_id] = result
        if self._bus:
            await self._bus.publish(OrderFilled(order, result) if result.ok else OrderFailed(order, result))
            await self._bus.publish(ExecutionQuality(order.proposal_id, order.symbol, result.ok, attempts,
                                                     round(self.reports[-1].latency_ms, 1), slippage, note))  # fmt: skip
        return result

    def _record(self, order: ApprovedOrder, result: OrderResult, attempts: int, ms: float, slip: float | None, note: str) -> None:
        self.reports.append(ExecutionReport(order.proposal_id, order.symbol, result.ok, attempts, ms, slip, note))
        del self.reports[:-500]

    async def _find_executed(self, order: ApprovedOrder) -> tuple[int, float] | None:
        wanted = order_comment(order)
        for p in await self._gw.get_positions():
            if p.symbol == order.symbol and p.comment.startswith(wanted):
                return p.ticket, p.price_open
        return None

    async def _after_fill(self, order: ApprovedOrder, result: OrderResult) -> tuple[OrderResult, float | None, str]:
        slippage: float | None = None
        if order.ref_price > 0 and result.price:
            spec = await self._gw.get_symbol_spec(order.symbol)
            adverse = (result.price - order.ref_price) if order.side is Side.BUY else (order.ref_price - result.price)
            slippage = adverse / spec.point
            if slippage > order.max_slippage_points and self._on_slippage == "close" and result.ticket is not None:
                closed = await self._gw.close_position(result.ticket)
                if not closed.ok:  # la position reste ouverte : au minimum protégée, puis plus aucun ordre
                    await self._ensure_protected(order, result)
                    self.block("SLIPPAGE_CLOSE_FAILED")
                    return OrderResult.fail(f"SLIPPAGE_CLOSE_FAILED:{slippage:.1f}pts", closed.retcode), slippage, "SLIPPAGE_CLOSE_FAILED"
                return OrderResult.fail(f"SLIPPAGE_EXCEEDED:{slippage:.1f}pts", result.retcode), slippage, "SLIPPAGE_EXCEEDED"
        result = await self._ensure_protected(order, result)
        return result, slippage, "" if result.ok else result.message

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
        closed = await self._gw.close_position(pos.ticket)
        self.block("PROTECTION_FAILED")
        return OrderResult.fail("PROTECTION_FAILED_POSITION_CLOSED" if closed.ok else "PROTECTION_FAILED_POSITION_OPEN", fixed.retcode)
