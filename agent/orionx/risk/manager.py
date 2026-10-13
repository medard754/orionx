"""Gestionnaire de risques (M5). Code DÉTERMINISTE : aucun appel à l'IA, aucun accès au courtier.

Il reçoit des données, rend une décision. Droit de veto absolu.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from orionx.core.config import RiskConfig
from orionx.core.models import (
    AccountState,
    ApprovedOrder,
    Position,
    Side,
    SymbolSpec,
    Tick,
    TradeProposal,
    utcnow,
)
from orionx.risk.approval import ApprovalSigner
from orionx.risk.sizing import position_size, risk_per_lot


@dataclass(frozen=True)
class Decision:
    approved: bool
    order: ApprovedOrder | None = None
    reasons: tuple[str, ...] = ()  # codes de rejet
    notes: tuple[str, ...] = ()  # informations (ex. VOLUME_REDUCED)


@dataclass
class RiskState:
    day: tuple[int, int, int] = (0, 0, 0)
    week: tuple[int, int] = (0, 0)
    day_start_equity: float = 0.0
    week_start_equity: float = 0.0
    peak_equity: float = 0.0
    consecutive_losses: int = 0
    halts: set[str] = field(default_factory=set)


class RiskManager:
    def __init__(
        self,
        cfg: RiskConfig,
        signer: ApprovalSigner,
        *,
        max_data_age_seconds: float = 3.0,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._cfg, self._signer, self._clock = cfg, signer, clock
        self._max_age = max_data_age_seconds
        self.state = RiskState()

    # --- état du compte -----------------------------------------------------------
    @property
    def halted(self) -> bool:
        return bool(self.state.halts)

    @property
    def halt_reasons(self) -> tuple[str, ...]:
        return tuple(sorted(self.state.halts))

    def start(self, account: AccountState) -> None:
        now = self._clock()
        s = self.state
        s.day, s.week = now.timetuple()[:3], now.isocalendar()[:2]
        s.day_start_equity = s.week_start_equity = s.peak_equity = account.equity

    def on_account_update(self, account: AccountState) -> None:
        now, s, c = self._clock(), self.state, self._cfg
        day, week = now.timetuple()[:3], now.isocalendar()[:2]
        if day != s.day:  # nouveau jour : on lève les arrêts journaliers
            s.day, s.day_start_equity, s.consecutive_losses = day, account.equity, 0
            s.halts -= {"DAILY_LOSS_LIMIT", "CONSECUTIVE_LOSSES"}
        if week != s.week:
            s.week, s.week_start_equity = week, account.equity
            s.halts.discard("WEEKLY_LOSS_LIMIT")
        s.peak_equity = max(s.peak_equity, account.equity)
        if _loss_pct(s.day_start_equity, account.equity) >= c.max_daily_loss_pct:
            s.halts.add("DAILY_LOSS_LIMIT")
        if _loss_pct(s.week_start_equity, account.equity) >= c.max_weekly_loss_pct:
            s.halts.add("WEEKLY_LOSS_LIMIT")
        if _loss_pct(s.peak_equity, account.equity) >= c.max_drawdown_pct:
            s.halts.add("MAX_DRAWDOWN")  # ne se lève PAS automatiquement

    def on_trade_closed(self, profit: float) -> None:
        s = self.state
        s.consecutive_losses = s.consecutive_losses + 1 if profit < 0 else 0
        if s.consecutive_losses >= self._cfg.max_consecutive_losses:
            s.halts.add("CONSECUTIVE_LOSSES")

    def halt(self, reason: str) -> None:
        self.state.halts.add(reason)

    def resume(self, reason: str) -> None:
        """Levée MANUELLE d'un arrêt (ex. MAX_DRAWDOWN, KILL_SWITCH) par l'utilisateur."""
        self.state.halts.discard(reason)

    # --- décision -----------------------------------------------------------------
    def evaluate(
        self,
        proposal: TradeProposal,
        tick: Tick,
        account: AccountState,
        positions: Sequence[Position],
        specs: Mapping[str, SymbolSpec],
    ) -> Decision:
        c, now = self._cfg, self._clock()
        if self.halted:
            return Decision(False, reasons=tuple(f"HALTED:{r}" for r in self.halt_reasons))
        if not account.is_demo:
            return Decision(False, reasons=("LIVE_DISABLED",))
        spec = specs.get(proposal.symbol)
        if spec is None or tick.symbol != proposal.symbol:
            return Decision(False, reasons=("UNKNOWN_SYMBOL",))
        if now > proposal.valid_until:
            return Decision(False, reasons=("PROPOSAL_EXPIRED",))
        if (now - tick.time) > timedelta(seconds=self._max_age):
            return Decision(False, reasons=("STALE_DATA",))

        reasons: list[str] = []
        notes: list[str] = []
        buy = proposal.side is Side.BUY
        entry = tick.ask if buy else tick.bid
        # On raisonne sur les prix ARRONDIS aux décimales du symbole : ce sont ceux qui seront envoyés.
        sl, tp = round(proposal.sl, spec.digits), round(proposal.tp, spec.digits)
        sl_ok = sl < entry if buy else sl > entry
        tp_ok = tp > entry if buy else tp < entry
        if not sl_ok:
            reasons.append("INVALID_SL")
        if not tp_ok:
            reasons.append("INVALID_TP")
        if tick.spread / spec.point > c.max_spread_points:
            reasons.append("SPREAD_TOO_WIDE")
        if len(positions) >= c.max_open_positions:
            reasons.append("MAX_POSITIONS")
        if account.margin_level is not None and account.margin_level < c.min_margin_level_pct:
            reasons.append("MARGIN_LEVEL_LOW")
        if sl_ok and tp_ok and abs(tp - entry) / abs(entry - sl) < c.min_rr:
            reasons.append("RR_TOO_LOW")

        capital = min(account.balance, account.equity)
        open_pct = 0.0
        for p in positions:
            pspec = specs.get(p.symbol)
            if p.sl is None or pspec is None:
                reasons.append("UNPROTECTED_POSITION")  # une position sans SL bloque tout
                break
            distance = (p.price_open - p.sl) if p.side is Side.BUY else (p.sl - p.price_open)
            cash = max(0.0, distance) / pspec.tick_size * pspec.tick_value * p.volume
            open_pct += cash / capital * 100 if capital > 0 else 100.0

        volume = 0.0
        if not reasons:
            allowed_pct = min(c.risk_per_trade_pct, c.max_total_open_risk_pct - open_pct)
            if allowed_pct <= 0:
                reasons.append("TOTAL_RISK_EXCEEDED")
            else:
                volume = position_size(capital, allowed_pct, entry, sl, spec)
                if volume <= 0:
                    reasons.append("VOLUME_BELOW_MIN")
                elif allowed_pct < c.risk_per_trade_pct:
                    notes.append("VOLUME_REDUCED")
        if reasons:
            return Decision(False, reasons=tuple(reasons), notes=tuple(notes))

        order = ApprovedOrder(
            proposal_id=proposal.id,
            symbol=proposal.symbol,
            side=proposal.side,
            volume=volume,
            sl=sl,
            tp=tp,
            max_slippage_points=c.max_slippage_points,
            expires_at=now + timedelta(seconds=c.approval_ttl_seconds),
        )
        return Decision(True, order=self._signer.sign(order), notes=tuple(notes))


def _loss_pct(reference: float, equity: float) -> float:
    return (reference - equity) / reference * 100 if reference > 0 else 0.0


__all__ = ["Decision", "RiskManager", "RiskState", "risk_per_lot"]
