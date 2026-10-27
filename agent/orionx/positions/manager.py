"""Gestion des positions ouvertes (T-07).

Règles : breakeven puis stop suiveur à partir de +1R, sortie partielle à +1,5R, sortie temporelle optionnelle.
INVARIANT : un stop-loss n'est JAMAIS éloigné du prix, seulement rapproché.
R = distance initiale entre le prix d'entrée et le stop-loss.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from orionx.connector_mt5.gateway import Gateway
from orionx.core.bus import EventBus
from orionx.core.events import PositionAdjusted
from orionx.core.models import ORIONX_MAGIC, Position, Side, SymbolSpec, Tick, utcnow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionRules:
    breakeven_r: float = 1.0  # à +1R : stop au point d'entrée (+ spread), puis suiveur
    trail_r: float = 1.0  # le stop suit le prix à une distance de 1R
    trail_atr_mult: float | None = None  # si défini : suiveur à N × ATR (mesure du moment) au lieu de N × R
    partial_r: float = 1.5  # à +1,5R : sortie partielle
    partial_fraction: float = 0.5
    max_hold_minutes: float | None = None  # sortie temporelle (mesurée en heure LOCALE)
    weekend_flat: bool = False  # tout clôturer avant la fermeture du vendredi
    weekend_close_minutes_before: int = 30
    weekend_close_hour_utc: int = 21  # fermeture du marché des changes le vendredi (heure UTC)
    news_action: str = "tighten"  # tighten | reduce | close, quand une annonce est imminente
    manage_foreign: bool = True  # gérer aussi les positions ouvertes à la main (magic différent)
    thesis_exit_min_reasons: int | None = None  # sortie si la thèse est dégradée pour au moins N raisons
    thesis_reduce_min_reasons: int | None = None  # réduction à partir de N raisons


def choose_action(reasons: Sequence[str], rules: PositionRules) -> str:
    """Décision sur une position dont la thèse est dégradée : CLOSE, REDUCE ou TIGHTEN (resserrer le stop)."""
    n, news = len(reasons), any("annonce" in r for r in reasons)
    if rules.thesis_exit_min_reasons is not None and n >= rules.thesis_exit_min_reasons:
        return "CLOSE"
    if news and rules.news_action == "close":
        return "CLOSE"
    if (news and rules.news_action == "reduce") or (
        rules.thesis_reduce_min_reasons is not None and n >= rules.thesis_reduce_min_reasons
    ):
        return "REDUCE"
    return "TIGHTEN"


def weekend_close_time(now: datetime, rules: PositionRules) -> bool:
    """Vrai le vendredi à partir de (heure de fermeture - minutes d'avance), et pendant le week-end."""
    if now.weekday() in (5, 6):
        return now.weekday() == 5 or now.hour < 22
    return now.weekday() == 4 and now.hour * 60 + now.minute >= rules.weekend_close_hour_utc * 60 - rules.weekend_close_minutes_before


def protective_stop(pos: Position, tick: Tick, spec: SymbolSpec) -> float | None:
    """Stop plus protecteur pour l'arrêt d'urgence niveau 2. None si aucun resserrement possible."""
    buy = pos.side is Side.BUY
    price = tick.bid if buy else tick.ask
    gap = max(2 * tick.spread, 10 * spec.point)
    pip = 10 * spec.point
    if pos.sl is None:  # position sans stop : stop de secours à 30 pips
        target = price - 30 * pip if buy else price + 30 * pip
    elif (price > pos.price_open) if buy else (price < pos.price_open):
        target = pos.price_open + tick.spread if buy else pos.price_open - tick.spread  # breakeven
    else:
        target = price - (price - pos.sl) / 2 if buy else price + (pos.sl - price) / 2  # moitié de la distance
    target = round(target, spec.digits)
    if buy:
        if (pos.sl is not None and target <= pos.sl + spec.tick_size / 2) or target > price - gap:
            return None
    elif (pos.sl is not None and target >= pos.sl - spec.tick_size / 2) or target < price + gap:
        return None
    return target


class PositionManager:
    def __init__(
        self,
        gateway: Gateway,
        rules: PositionRules | None = None,
        bus: EventBus | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._gw, self._rules, self._bus, self._clock = gateway, rules or PositionRules(), bus, clock
        self._risk_dist: dict[int, float | None] = {}
        self._first_seen: dict[int, datetime] = {}
        self._partial_done: set[int] = set()
        self._warned: set[int] = set()

    async def step(
        self,
        positions: Sequence[Position],
        ticks: Mapping[str, Tick],
        specs: Mapping[str, SymbolSpec],
        atrs: Mapping[str, float] | None = None,
    ) -> list[str]:
        actions: list[str] = []
        now = self._clock()
        live = {p.ticket for p in positions}
        for store in (self._risk_dist, self._first_seen):
            for t in [t for t in store if t not in live]:
                del store[t]
        self._partial_done &= live
        self._warned &= live
        for pos in positions:
            tick, spec = ticks.get(pos.symbol), specs.get(pos.symbol)
            if tick is None or spec is None:
                continue
            actions += await self._manage(pos, tick, spec, now, (atrs or {}).get(pos.symbol))
        return actions

    @staticmethod
    def _part(pos: Position, spec: SymbolSpec, fraction: float) -> float:
        """Volume à clôturer pour une sortie partielle ; 0.0 si le reste serait inférieur au lot minimal."""
        part = round(math.floor(pos.volume * fraction / spec.volume_step + 1e-9) * spec.volume_step, 8)
        return part if part >= spec.volume_min and pos.volume - part >= spec.volume_min - 1e-9 else 0.0

    async def _note(self, pos: Position, action: str, detail: str, actions: list[str]) -> None:
        actions.append(f"{action} #{pos.ticket} {detail}")
        if self._bus:
            await self._bus.publish(PositionAdjusted(pos.ticket, action, detail))

    def _left_alone(self, pos: Position) -> bool:
        """Position ouverte à la main, laissée à l'utilisateur (selon la configuration)."""
        return not self._rules.manage_foreign and pos.magic != ORIONX_MAGIC

    async def _manage(self, pos: Position, tick: Tick, spec: SymbolSpec, now: datetime, atr: float | None = None) -> list[str]:
        actions: list[str] = []
        if self._left_alone(pos):
            return actions
        buy = pos.side is Side.BUY
        if pos.ticket not in self._first_seen:
            self._first_seen[pos.ticket] = now
            valid = pos.sl is not None and ((pos.sl < pos.price_open) if buy else (pos.sl > pos.price_open))
            self._risk_dist[pos.ticket] = abs(pos.price_open - pos.sl) if valid and pos.sl is not None else None
        if self._rules.weekend_flat and weekend_close_time(now, self._rules):  # --- fermeture avant le week-end
            res = await self._gw.close_position(pos.ticket)
            await self._note(pos, "WEEKEND_FLAT" if res.ok else "WEEKEND_FLAT_FAILED", "fermeture du marché", actions)
            return actions
        if pos.sl is None:
            if pos.ticket not in self._warned:
                self._warned.add(pos.ticket)
                await self._note(pos, "UNPROTECTED", "position sans stop-loss : aucune règle R applicable", actions)
            return actions

        price = tick.bid if buy else tick.ask
        risk = self._risk_dist.get(pos.ticket)
        r = ((price - pos.price_open) if buy else (pos.price_open - price)) / risk if risk else None
        rules, gap = self._rules, max(2 * tick.spread, 10 * spec.point)

        if risk and r is not None and r >= rules.breakeven_r:  # --- breakeven + stop suiveur
            be = pos.price_open + tick.spread if buy else pos.price_open - tick.spread
            dist = rules.trail_atr_mult * atr if rules.trail_atr_mult and atr else rules.trail_r * risk  # ATR ou R
            trail = price - dist if buy else price + dist
            target = round(max(be, trail) if buy else min(be, trail), spec.digits)
            better = target > pos.sl + spec.tick_size / 2 if buy else target < pos.sl - spec.tick_size / 2
            allowed = target <= price - gap if buy else target >= price + gap
            if better and allowed:
                res = await self._gw.modify_position(pos.ticket, target, pos.tp if pos.tp is not None else 0.0)
                await self._note(pos, "STOP_MOVED" if res.ok else "STOP_FAILED", f"{pos.sl} -> {target} ({r:.2f}R)", actions)

        if risk and r is not None and r >= rules.partial_r and pos.ticket not in self._partial_done:  # --- partielle
            part = self._part(pos, spec, rules.partial_fraction)
            if part:
                res = await self._gw.close_position(pos.ticket, part)
                self._partial_done.add(pos.ticket)
                await self._note(pos, "PARTIAL_CLOSE" if res.ok else "PARTIAL_FAILED", f"{part} lot à {r:.2f}R", actions)

        held = (now - self._first_seen[pos.ticket]).total_seconds() / 60
        if rules.max_hold_minutes is not None and held > rules.max_hold_minutes:  # --- sortie temporelle
            res = await self._gw.close_position(pos.ticket)
            await self._note(pos, "TIME_EXIT" if res.ok else "TIME_EXIT_FAILED", f"après {held:.0f} min", actions)
        return actions

    async def on_thesis_degraded(self, pos: Position, reasons: Sequence[str], tick: Tick, spec: SymbolSpec) -> list[str]:
        """La logique du trade ne tient plus : CLOSE (sortie), REDUCE (réduction + stop resserré) ou TIGHTEN (stop resserré).

        Le stop n'est jamais éloigné du prix."""
        actions: list[str] = []
        if self._left_alone(pos):
            return actions
        why = ", ".join(reasons)
        action = choose_action(reasons, self._rules)
        if action == "CLOSE":
            res = await self._gw.close_position(pos.ticket)
            await self._note(pos, "THESIS_EXIT" if res.ok else "THESIS_EXIT_FAILED", why, actions)
            return actions
        if action == "REDUCE":
            part = self._part(pos, spec, 0.5)
            if part:
                res = await self._gw.close_position(pos.ticket, part)
                await self._note(pos, "THESIS_REDUCE" if res.ok else "THESIS_REDUCE_FAILED", f"{part} lot ({why})", actions)
        new_sl = protective_stop(pos, tick, spec)
        if new_sl is not None:
            res = await self._gw.modify_position(pos.ticket, new_sl, pos.tp if pos.tp is not None else 0.0)
            await self._note(pos, "THESIS_STOP" if res.ok else "THESIS_STOP_FAILED", f"{pos.sl} -> {new_sl} ({why})", actions)
        elif not actions:
            await self._note(pos, "THESIS_WATCH", f"thèse dégradée ({why}), stop déjà serré", actions)
        return actions
