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
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from orionx.alerts.router import AlertRouter, attach_alerts
from orionx.analysis.calendar import EconomicCalendar
from orionx.analysis.state import MarketState, Regime, analyze, session_of
from orionx.analysis.thesis import ThesisMonitor
from orionx.analysis.tzutil import estimate_offset, server_to_utc_eet
from orionx.audit.log import AuditLog, attach
from orionx.connector_mt5.gateway import Gateway
from orionx.core.bus import EventBus
from orionx.core.config import AppConfig
from orionx.core.errors import AccountChangedError, GatewayError, InvalidQuoteError, OrionxError
from orionx.core.events import (
    CircuitBreakerTripped,
    DecisionNote,
    MarketStateUpdated,
    ThesisDegraded,
    TradeApproved,
    TradeProposed,
    TradeRejected,
)
from orionx.core.models import AccountState, OrderResult, Position, SymbolSpec, TradeProposal, utcnow
from orionx.data.engine import MarketData, MarketStore
from orionx.decision.engine import Candidate, DecisionEngine, EngineResult, StrategyEntry
from orionx.execution.executor import Executor
from orionx.monitoring.flag import read_kill_flag
from orionx.monitoring.killswitch import KillSwitch
from orionx.positions.manager import PositionManager, PositionRules
from orionx.risk.approval import ApprovalSigner
from orionx.risk.guard import RiskParamGuard
from orionx.risk.manager import RiskManager
from orionx.strategies.trend_pullback import Strategy

log = logging.getLogger(__name__)
GATEWAY_HALT = "GATEWAY_ERRORS"
ACCOUNT_HALT = "ACCOUNT_CHANGED"  # jamais levé automatiquement : l'agent s'arrête (docs/adr/0006)
RECONNECT_AFTER_ERRORS = 3  # erreurs de cycle de suite avant de tenter de rouvrir la liaison avec le terminal
RECONNECT_BASE_SECONDS, RECONNECT_MAX_SECONDS = 5.0, 60.0  # attente entre tentatives : 5, 10, 20, 40, 60, 60...
DATA_HALT = "DATA_STALE"


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
        engine: DecisionEngine | None = None,
        risk_guard: RiskParamGuard | None = None,
        audit: AuditLog | None = None,
        store: MarketStore | None = None,
        calendar: EconomicCalendar | None = None,
        rules: PositionRules | None = None,
        autonomy: str | None = None,
        allow_auto: bool = False,
        kill_flag: str | Path | None = None,
        alerts: AlertRouter | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.gw, self.cfg, self._audit, self._flag = gateway, cfg, audit, kill_flag
        self.engine = engine or (DecisionEngine([StrategyEntry(strategy)], cfg.decision, cfg.analysis) if strategy else None)
        self.guard = risk_guard
        risk_cfg = risk_guard.resolve_startup(cfg.risk) if risk_guard else cfg.risk  # jamais plus risqué sans le délai
        self._known_halts: set[str] = set()
        self.bus = EventBus()
        if audit:
            attach(self.bus, audit)
        self._alerts = alerts
        if alerts:
            attach_alerts(self.bus, alerts)
        self._clock, self.calendar = clock, calendar
        self.states: dict[str, MarketState] = {}
        self._state_bar: dict[str, datetime] = {}
        self.server_offset: timedelta | None = None
        self.signer = ApprovalSigner(clock=clock)
        self.risk = RiskManager(risk_cfg, self.signer, max_data_age_seconds=cfg.max_data_age_seconds, clock=clock,
                                extra_checks=(self._news_check, self._regime_check))  # fmt: skip
        self.executor = Executor(gateway, self.signer, self.bus)
        self.kill = KillSwitch(gateway, self.executor, self.risk, self.bus)
        rules = rules or cfg.positions.to_rules()
        self.positions = PositionManager(gateway, rules, self.bus, clock)
        self.thesis = ThesisMonitor(self._blackout_title)
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
        self.aborted: str | None = None  # raison de l'arrêt définitif (compte changé) ; None tant que l'agent peut tourner
        self._login: int | None = None  # compte du premier démarrage : référence pour toute la vie de l'agent
        self._reconnect_attempts = 0
        self._next_reconnect: datetime | None = None
        self._down_since: datetime | None = None

    # --- analyse de marché (M3 v1) ---------------------------------------------------
    def _to_utc(self, server_time: datetime) -> datetime:
        return server_time - self.server_offset if self.server_offset is not None else server_to_utc_eet(server_time)

    def _now_utc(self) -> datetime:
        return self._clock()

    def _blackout_title(self, symbol: str, now: datetime) -> str | None:
        if self.calendar is None:
            return None
        a = self.cfg.analysis
        event = self.calendar.blackout(symbol, now, a.news_before_min, a.news_after_min, a.news_min_impact)
        return f"{event.currency} {event.title}" if event else None

    def _news_check(self, proposal: TradeProposal, tick, now: datetime) -> str | None:  # type: ignore[no-untyped-def]
        return "NEWS_BLACKOUT" if self._blackout_title(proposal.symbol, now) else None

    def _regime_check(self, proposal: TradeProposal, tick, now: datetime) -> str | None:  # type: ignore[no-untyped-def]
        a, state = self.cfg.analysis, self.states.get(proposal.symbol)
        if a.block_high_vol and state is not None and state.regime is Regime.HIGH_VOL:
            return "HIGH_VOLATILITY"
        if session_of(now).value in a.blocked_sessions:
            return "SESSION_BLOCKED"
        return None

    def _update_offset(self) -> None:
        """Décalage horaire du courtier, mesuré sur un prix FRAIS (recalculé en continu : gère le changement d'heure)."""
        now = self._clock()
        for tick in self.market.last.values():
            if tick.received_at is not None and (now - tick.received_at) < timedelta(seconds=2):
                self.server_offset = estimate_offset(tick.time, tick.received_at)
                return

    async def _update_states(self) -> None:
        a = self.cfg.analysis
        for sym in self.cfg.symbols:
            recent = await self.gw.get_bars(sym, "H1", 2)
            if not recent or self._state_bar.get(sym) == recent[-1].time:
                continue
            bars = await self.gw.get_bars(sym, "H1", a.vol_window + 60)
            if not bars:
                continue
            state = analyze(sym, bars, a, self._to_utc)
            self._state_bar[sym] = recent[-1].time  # seulement après succès : sinon le régime resterait figé une heure
            previous = self.states.get(sym)
            self.states[sym] = state
            if previous is None or (previous.regime, previous.session) != (state.regime, state.session):
                await self.bus.publish(
                    MarketStateUpdated(sym, state.regime.value, state.session.value, round(state.volatility_ratio, 3),
                                       round(state.trend_strength, 3), round(state.efficiency, 3), state.atr)
                )  # fmt: skip

    async def _check_theses(self, positions: list[Position], rep: CycleReport) -> None:
        self.thesis.forget(p.ticket for p in positions)
        now = self._now_utc()
        for pos in positions:
            reasons = self.thesis.check(pos, self.states.get(pos.symbol), now)
            if not reasons:
                continue
            await self.bus.publish(ThesisDegraded(pos.ticket, pos.symbol, reasons))
            tick, spec = self.market.last.get(pos.symbol), self.specs.get(pos.symbol)
            if tick is not None and spec is not None:
                rep.actions += await self.positions.on_thesis_degraded(pos, reasons, tick, spec)

    # --- journal ----------------------------------------------------------------------
    def _log(self, kind: str, payload: object = None) -> None:
        if self._audit:
            self._audit.append(kind, payload)
        if self._alerts:  # ne lève jamais, ne bloque jamais (envoi dans un fil dédié)
            self._alerts.handle(kind, payload)

    # --- cycle de vie -----------------------------------------------------------------
    async def start(self) -> None:
        acc = await self.gw.get_account()
        if not acc.is_demo:
            raise OrionxError("Compte non démo : ORIONX v0 refuse de démarrer.")
        if self._login is None:  # le premier compte vu reste la référence (même après un redémarrage de la boucle)
            self._login = acc.login
        for s in self.cfg.symbols:
            self.specs[s] = await self.gw.get_symbol_spec(s)
        self.risk.start(acc)
        self._prev = {p.ticket: p for p in await self.gw.get_positions()}
        await self._ensure_symbols(list(self._prev.values()))
        await self._update_states()
        if self.engine:  # on ignore la bougie déjà fermée au démarrage (signal trop ancien)
            for s in self.cfg.symbols:
                bars = await self.gw.get_bars(s, self.engine.timeframe, 2)
                if bars:
                    self._last_bar[s] = bars[-1].time
        self._log("AgentStarted", {"autonomy": self.autonomy, "symbols": self.cfg.symbols, "equity": acc.equity,
                                   "strategies": [e.strategy.name for e in self.engine.entries if e.enabled] if self.engine else [],
                                   "open_positions": len(self._prev),
                                   "risk_startup": self.guard.startup_note if self.guard else None})  # fmt: skip

    async def shutdown(self) -> None:
        self._log("AgentStopped", {"cycles": self.cycles, "note": "positions ouvertes protégées par leur stop-loss serveur"})

    async def run(self, stop: asyncio.Event, cycle_seconds: float = 1.0, max_cycles: int | None = None) -> None:
        await self.start()
        try:
            while not stop.is_set():
                t0 = time.monotonic()
                await self.run_cycle()
                if self.aborted or (max_cycles is not None and self.cycles >= max_cycles):
                    break
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=max(0.0, cycle_seconds - (time.monotonic() - t0)))
        finally:
            await self.shutdown()

    # --- un cycle ---------------------------------------------------------------------
    async def run_cycle(self) -> CycleReport:
        rep = CycleReport()
        self.cycles += 1
        if self.aborted:  # arrêt définitif : on ne touche plus à rien
            rep.error = self.aborted
            return rep
        try:
            await self._check_kill_flag(rep)
            self._apply_due_risk_change()
            acc = await self.gw.get_account()
            self._check_account(acc)  # avant toute lecture ou action sur les positions
            self.risk.on_account_update(acc)
            positions = await self.gw.get_positions()
            self._detect_closed(positions)
            await self._ensure_symbols(positions)
            await self.market.poll()
            self._update_offset()
            await self._update_states()
            managed = await self.positions.step(positions, self.market.last, self.specs,
                                                {k: v.atr for k, v in self.states.items() if v.atr > 0})  # fmt: skip
            rep.actions += managed
            if managed:  # stop ou volume modifiés : la thèse doit réagir sur l'état réel, sinon elle peut éloigner un stop
                positions = await self.gw.get_positions()
            await self._check_theses(positions, rep)
            await self._check_data_health()
            if self.engine:
                await self._decide(acc, positions, rep)
            await self._success()
        except AccountChangedError as exc:
            self._abort(str(exc), rep)
        except Exception as exc:  # noqa: BLE001 - la boucle ne doit jamais mourir sur une erreur ponctuelle
            rep.error = f"{type(exc).__name__}: {exc}"
            await self._failure(rep.error, exc)
        await self._publish_new_halts()
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

    async def _ensure_symbols(self, positions: list[Position]) -> None:
        """Une position sur un symbole non configuré doit quand même être comprise (spécification) et suivie (prix).
        Sans spécification, le gestionnaire de risques la traiterait comme « non protégée » et bloquerait tout."""
        for p in positions:
            if p.symbol in self.specs:
                continue
            try:
                self.specs[p.symbol] = await self.gw.get_symbol_spec(p.symbol)
                self.market.watch(p.symbol)
            except Exception as exc:  # noqa: BLE001
                log.error("Spécification introuvable pour %s : %s", p.symbol, exc)

    async def _engine_eval(self, sym: str, positions: list[Position]) -> EngineResult | None:
        """Une évaluation par bougie fermée et par symbole (M4)."""
        assert self.engine is not None
        tick = self.market.last.get(sym)
        recent = await self.gw.get_bars(sym, self.engine.timeframe, 2)
        if tick is None or not recent or self._last_bar.get(sym) == recent[-1].time:
            return None
        self._last_bar[sym] = recent[-1].time
        bars = await self.gw.get_bars(sym, self.engine.timeframe, self.engine.min_bars + 5)
        res = self.engine.evaluate(sym, bars, tick, self._clock(), positions, self.states.get(sym))
        if res.notes:
            await self.bus.publish(DecisionNote(sym, res.notes))
        return res

    async def _decide(self, acc, positions: list[Position], rep: CycleReport) -> None:  # type: ignore[no-untyped-def]
        assert self.engine is not None
        results = [r for sym in self.cfg.symbols if (r := await self._engine_eval(sym, positions)) is not None]
        kept, dropped = self.engine.rank(results)  # F4.5 : classement et diversification par devise
        if dropped:
            await self.bus.publish(DecisionNote("*", tuple(f"CURRENCY_DUPLICATE:{c.proposal.symbol}" for c in dropped)))
        for cand in kept:
            await self._handle(cand, acc, positions, rep)
            positions = await self.gw.get_positions()  # l'exposition change après chaque ordre

    async def _handle(self, cand: Candidate, acc, positions: list[Position], rep: CycleReport) -> None:  # type: ignore[no-untyped-def]
        proposal, tick = cand.proposal, self.market.last.get(cand.proposal.symbol)
        if tick is None:
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
            self._log("SignalPending", {"proposal": proposal, "score": cand.score, "rr": cand.rr,
                                        "strategies": cand.strategies, "notes": decision.notes})  # fmt: skip
        else:
            self._log("SignalOnly", {"proposal": proposal, "score": cand.score, "rr": cand.rr,
                                     "strategies": cand.strategies, "notes": decision.notes})  # fmt: skip

    async def approve(self, proposal_id: str) -> OrderResult:
        """Approbation humaine (semi_auto) : le risque est RE-ÉVALUÉ avec les prix du moment."""
        proposal = self.pending.pop(proposal_id, None)
        if proposal is None:
            return OrderResult.fail("Proposition inconnue ou déjà traitée")
        try:
            tick = await self.gw.get_tick(proposal.symbol)
        except InvalidQuoteError as exc:
            self.pending[proposal_id] = proposal  # non consommée : l'utilisateur peut réessayer
            return OrderResult.fail(f"{exc} : réessayez dans un instant")
        acc, positions = await self.gw.get_account(), await self.gw.get_positions()
        decision = self.risk.evaluate(proposal, tick, acc, positions, self.specs)
        if not decision.approved or decision.order is None:
            await self.bus.publish(TradeRejected(proposal.id, decision.reasons))
            return OrderResult.fail(f"Refusée par le risque : {decision.reasons}")
        await self.bus.publish(TradeApproved(decision.order))
        return await self.executor.execute(decision.order)

    # --- robustesse -------------------------------------------------------------------
    def _check_account(self, acc: AccountState) -> None:
        """Le terminal peut être rattaché à un autre compte (reconnexion, changement manuel) : on ne gère jamais ses positions."""
        if not acc.is_demo:
            raise AccountChangedError("compte non démo détecté : l'agent s'arrête")
        if self._login is not None and acc.login != self._login:
            raise AccountChangedError(f"compte différent (démarré sur {self._login}, vu {acc.login}) : l'agent s'arrête")

    def _abort(self, reason: str, rep: CycleReport) -> None:
        self.aborted = rep.error = reason
        self.risk.halt(ACCOUNT_HALT)
        self._log("AccountChanged", {"reason": reason, "expected_login": self._login})
        log.critical("%s", reason)

    async def _success(self) -> None:
        self._errors = 0
        self._reconnect_attempts, self._next_reconnect, self._down_since = 0, None, None
        self._ok_streak += 1
        if GATEWAY_HALT in self.risk.halt_reasons and self._ok_streak >= 30:
            self.risk.resume(GATEWAY_HALT)
            self._log("GatewayRecovered", {"after_ok_cycles": self._ok_streak})

    async def _failure(self, message: str, exc: BaseException | None = None) -> None:
        self._errors += 1
        self._ok_streak = 0
        log.error("Cycle en erreur (%s) : %s", self._errors, message)
        if self._errors in (1, 10) or self._errors % 100 == 0:
            self._log("CycleError", {"consecutive": self._errors, "error": message})
        if self._errors >= 3 and GATEWAY_HALT not in self.risk.halt_reasons:
            self.risk.halt(GATEWAY_HALT)  # l'événement est publié par _publish_new_halts
        # une erreur de logique (bug) ne justifie pas de rouvrir MT5 : seules les erreurs de liaison déclenchent la reconnexion
        if self._errors >= RECONNECT_AFTER_ERRORS and isinstance(exc, GatewayError | OSError):
            await self._try_reconnect()

    async def _try_reconnect(self) -> None:
        """Rouvre la liaison avec le terminal, avec une attente croissante. Le mode sûr reste actif jusqu'à 30 cycles réussis."""
        now = self._clock()
        self._down_since = self._down_since or now
        if self._next_reconnect is not None and now < self._next_reconnect:
            return
        self._reconnect_attempts += 1
        n = self._reconnect_attempts
        try:
            await self.gw.reconnect()
        except Exception as exc:  # noqa: BLE001 - un échec de reconnexion ne doit jamais tuer la boucle
            delay = min(RECONNECT_MAX_SECONDS, RECONNECT_BASE_SECONDS * 2 ** min(n - 1, 10))
            self._next_reconnect = now + timedelta(seconds=delay)
            log.warning("Reconnexion MT5 (tentative %s) échouée : %s ; prochaine dans %.0f s", n, exc, delay)
            if n == 1 or n % 10 == 0:
                self._log("GatewayReconnectFailed", {"attempt": n, "retry_in_seconds": delay, "error": str(exc)})
            return
        down = round((now - self._down_since).total_seconds(), 1)
        self._log("GatewayReconnected", {"attempts": n, "down_seconds": down})
        self._reconnect_attempts, self._next_reconnect, self._down_since = 0, None, None

    async def _check_data_health(self) -> None:
        """Mode sûr automatique (M5 F5.9) : prix figé trop longtemps => plus aucune entrée, jusqu'au retour des prix."""
        now, limit = self._clock(), self.cfg.stale_halt_seconds
        frozen = [s for s in self.market.confirmed
                  if (t := self.market.last.get(s)) is not None and (now - t.observed_at).total_seconds() > limit]  # fmt: skip
        if frozen and DATA_HALT not in self.risk.halt_reasons:
            self.risk.halt(DATA_HALT)
            self._log("DataFrozen", {"symbols": frozen, "limit_seconds": limit})
        elif not frozen and DATA_HALT in self.risk.halt_reasons:
            self.risk.resume(DATA_HALT)
            self._log("DataRestored", {})

    async def _publish_new_halts(self) -> None:
        """Chaque nouvel arrêt automatique du gestionnaire de risques est publié (donc journalisé)."""
        current = set(self.risk.halt_reasons)
        for reason in sorted(current - self._known_halts - {"KILL_SWITCH"}):  # l'arrêt d'urgence a son propre événement
            await self.bus.publish(CircuitBreakerTripped(reason))
        self._known_halts = current

    def _apply_due_risk_change(self) -> None:
        """Applique une modification des paramètres de risque dont le délai de sécurité est écoulé (M5 F5.7)."""
        if self.guard is None:
            return
        new = self.guard.due()
        if new is not None:
            self.risk.apply_config(new)
            self._log("RiskConfigChanged", new)
