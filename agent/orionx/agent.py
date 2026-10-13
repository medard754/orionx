"""Boucle principale de l'agent (T-06).

Un cycle : arrêt d'urgence ? -> compte et risques -> positions fermées ? -> prix -> gestion des positions
-> nouvelle bougie ? -> stratégie -> gestionnaire de risques -> (selon l'autonomie) exécution.

Autonomie :
  signals_only : on journalise les signaux, aucun ordre.
  semi_auto    : les signaux validés par le risque attendent une approbation humaine (`approve`).
  auto         : exécution automatique. Exige `allow_auto=True`, sinon repli sur semi_auto.
Les positions ouvertes ont toujours un stop-loss serveur : arrêter l'agent ne les laisse pas sans protection.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from orionx.audit.log import AuditLog, attach
from orionx.connector_mt5.gateway import Gateway
from orionx.core.bus import EventBus
from orionx.core.config import AppConfig
from orionx.core.errors import OrionxError
from orionx.core.events import CircuitBreakerTripped, TradeApproved, TradeProposed, TradeRejected
from orionx.core.models import OrderResult, Position, SymbolSpec, TradeProposal, utcnow
from orionx.data.engine import MarketData, MarketStore
from orionx.execution.executor import Executor
from orionx.monitoring.flag import read_kill_flag
from orionx.monitoring.killswitch import KillSwitch
from orionx.positions.manager import PositionManager, PositionRules
from orionx.risk.approval import ApprovalSigner
from orionx.risk.manager import RiskManager
from orionx.strategies.trend_pullback import Strategy

log = logging.getLogger(__name__)
GATEWAY_HALT = "GATEWAY_ERRORS"


@dataclass
class CycleReport:
    signals: int = 0
    orders: int = 0
    actions: list[str] = field(default_factory=list)
    error: str | None = None


class Agent:
    def __init__(
        self,
        gateway: Gateway,
        cfg: AppConfig,
        *,
        strategy: Strategy | None = None,
        audit: AuditLog | None = None,
        store: MarketStore | None = None,
        rules: PositionRules | None = None,
        autonomy: str | None = None,
        allow_auto: bool = False,
        kill_flag: str | Path | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.gw, self.cfg, self.strategy, self._audit, self._flag = gateway, cfg, strategy, audit, kill_flag
        self.bus = EventBus()
        if audit:
            attach(self.bus, audit)
        self.signer = ApprovalSigner(clock=clock)
        self.risk = RiskManager(cfg.risk, self.signer, max_data_age_seconds=cfg.max_data_age_seconds, clock=clock)
        self.executor = Executor(gateway, self.signer, self.bus)
        self.kill = KillSwitch(gateway, self.executor, self.risk, self.bus)
        self.positions = PositionManager(gateway, rules, self.bus, clock)
        self.market = MarketData(gateway, cfg.symbols, self.bus, stale_after=cfg.max_data_age_seconds, store=store, clock=clock)
        self.autonomy = autonomy or cfg.autonomy
        if self.autonomy == "auto" and not allow_auto:
            log.warning("autonomy=auto sans allow_auto : repli sur semi_auto")
            self.autonomy = "semi_auto"
        self.specs: dict[str, SymbolSpec] = {}
        self.pending: dict[str, TradeProposal] = {}
        self._prev: dict[int, Position] = {}
        self._last_bar: dict[str, datetime] = {}
        self._errors = 0
        self._ok_streak = 0
        self.cycles = 0

    # --- journal ----------------------------------------------------------------------
    def _log(self, kind: str, payload: object = None) -> None:
        if self._audit:
            self._audit.append(kind, payload)

    # --- cycle de vie -----------------------------------------------------------------
    async def start(self) -> None:
        acc = await self.gw.get_account()
        if not acc.is_demo:
            raise OrionxError("Compte non démo : ORIONX v0 refuse de démarrer.")
        for s in self.cfg.symbols:
            self.specs[s] = await self.gw.get_symbol_spec(s)
        self.risk.start(acc)
        self._prev = {p.ticket: p for p in await self.gw.get_positions()}
        if self.strategy:  # on ignore la bougie déjà fermée au démarrage (signal trop ancien)
            for s in self.cfg.symbols:
                bars = await self.gw.get_bars(s, self.strategy.timeframe, 2)
                if bars:
                    self._last_bar[s] = bars[-1].time
        self._log("AgentStarted", {"autonomy": self.autonomy, "symbols": self.cfg.symbols, "equity": acc.equity,
                                   "strategy": getattr(self.strategy, "name", None), "open_positions": len(self._prev)})  # fmt: skip

    async def shutdown(self) -> None:
        self._log("AgentStopped", {"cycles": self.cycles, "note": "positions ouvertes protégées par leur stop-loss serveur"})

    async def run(self, stop: asyncio.Event, cycle_seconds: float = 1.0, max_cycles: int | None = None) -> None:
        await self.start()
        try:
            while not stop.is_set():
                t0 = time.monotonic()
                await self.run_cycle()
                if max_cycles is not None and self.cycles >= max_cycles:
                    break
                try:
                    await asyncio.wait_for(stop.wait(), timeout=max(0.0, cycle_seconds - (time.monotonic() - t0)))
                except TimeoutError:
                    pass
        finally:
            await self.shutdown()

    # --- un cycle ---------------------------------------------------------------------
    async def run_cycle(self) -> CycleReport:
        rep = CycleReport()
        self.cycles += 1
        try:
            await self._check_kill_flag(rep)
            acc = await self.gw.get_account()
            self.risk.on_account_update(acc)
            positions = await self.gw.get_positions()
            self._detect_closed(positions)
            await self.market.poll()
            rep.actions += await self.positions.step(positions, self.market.last, self.specs)
            if self.strategy:
                for sym in self.cfg.symbols:
                    await self._evaluate(sym, acc, positions, rep)
            await self._success()
        except Exception as exc:  # noqa: BLE001 - la boucle ne doit jamais mourir sur une erreur ponctuelle
            rep.error = f"{type(exc).__name__}: {exc}"
            await self._failure(rep.error)
        return rep

    async def _check_kill_flag(self, rep: CycleReport) -> None:
        if self._flag is None:
            return
        flag = read_kill_flag(self._flag)
        if flag and int(flag["level"]) > self.kill.level:
            report = await self.kill.activate(int(flag["level"]), f"drapeau : {flag.get('reason', '')}")
            rep.actions.append(f"ARRÊT D'URGENCE niveau {report.level} : {report.actions} échecs={report.failures}")

    def _detect_closed(self, positions: list[Position]) -> None:
        now_ids = {p.ticket for p in positions}
        for ticket, last in self._prev.items():
            if ticket not in now_ids:  # profit = dernier instantané connu (approximation)
                self.risk.on_trade_closed(last.profit)
                self._log("PositionClosed", {"ticket": ticket, "symbol": last.symbol, "last_known_profit": last.profit})
        self._prev = {p.ticket: p for p in positions}

    async def _evaluate(self, sym: str, acc, positions: list[Position], rep: CycleReport) -> None:  # type: ignore[no-untyped-def]
        assert self.strategy is not None
        tick = self.market.last.get(sym)
        recent = await self.gw.get_bars(sym, self.strategy.timeframe, 2)
        if tick is None or not recent or self._last_bar.get(sym) == recent[-1].time:
            return
        self._last_bar[sym] = recent[-1].time  # une seule évaluation par bougie fermée
        bars = await self.gw.get_bars(sym, self.strategy.timeframe, self.strategy.min_bars + 5)
        proposal = self.strategy.generate(sym, bars, tick, self.market._clock(), {p.symbol for p in positions})
        if proposal is None:
            return
        rep.signals += 1
        await self.bus.publish(TradeProposed(proposal))
        decision = self.risk.evaluate(proposal, tick, acc, positions, self.specs)
        if not decision.approved or decision.order is None:
            await self.bus.publish(TradeRejected(proposal.id, decision.reasons))
            return
        if self.autonomy == "auto":
            await self.bus.publish(TradeApproved(decision.order))
            result = await self.executor.execute(decision.order)
            rep.orders += int(result.ok)
        elif self.autonomy == "semi_auto":
            self.pending[proposal.id] = proposal
            self._log("SignalPending", {"proposal": proposal, "notes": decision.notes})
        else:
            self._log("SignalOnly", {"proposal": proposal, "notes": decision.notes})

    async def approve(self, proposal_id: str) -> OrderResult:
        """Approbation humaine (semi_auto) : le risque est RE-ÉVALUÉ avec les prix du moment."""
        proposal = self.pending.pop(proposal_id, None)
        if proposal is None:
            return OrderResult.fail("Proposition inconnue ou déjà traitée")
        tick = await self.gw.get_tick(proposal.symbol)
        acc, positions = await self.gw.get_account(), await self.gw.get_positions()
        decision = self.risk.evaluate(proposal, tick, acc, positions, self.specs)
        if not decision.approved or decision.order is None:
            await self.bus.publish(TradeRejected(proposal.id, decision.reasons))
            return OrderResult.fail(f"Refusée par le risque : {decision.reasons}")
        await self.bus.publish(TradeApproved(decision.order))
        return await self.executor.execute(decision.order)

    # --- robustesse -------------------------------------------------------------------
    async def _success(self) -> None:
        self._errors = 0
        self._ok_streak += 1
        if GATEWAY_HALT in self.risk.halt_reasons and self._ok_streak >= 30:
            self.risk.resume(GATEWAY_HALT)
            self._log("GatewayRecovered", {"after_ok_cycles": self._ok_streak})

    async def _failure(self, message: str) -> None:
        self._errors += 1
        self._ok_streak = 0
        log.error("Cycle en erreur (%s) : %s", self._errors, message)
        if self._errors in (1, 10) or self._errors % 100 == 0:
            self._log("CycleError", {"consecutive": self._errors, "error": message})
        if self._errors >= 3 and GATEWAY_HALT not in self.risk.halt_reasons:
            self.risk.halt(GATEWAY_HALT)
            await self.bus.publish(CircuitBreakerTripped(GATEWAY_HALT))
