"""Surveillance de la logique d'un trade ouvert (événement ThesisDegraded)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from orionx.analysis.state import MarketState, Regime
from orionx.core.models import Position, Side


class ThesisMonitor:
    """Rend les raisons pour lesquelles la thèse d'une position ne tient plus.

    Renvoie un résultat UNIQUEMENT quand l'ensemble de raisons change (une seule réaction par changement).
    """

    def __init__(self, blackout: Callable[[str, datetime], str | None] | None = None) -> None:
        self._blackout = blackout
        self._entry: dict[int, Regime] = {}
        self._last: dict[int, tuple[str, ...]] = {}

    def forget(self, live: Iterable[int]) -> None:
        keep = set(live)
        for store in (self._entry, self._last):
            for t in [t for t in store if t not in keep]:
                del store[t]

    def current(self, ticket: int) -> tuple[str, ...]:
        """Raisons de dégradation actuellement retenues pour cette position (vide : la thèse tient)."""
        return self._last.get(ticket, ())

    def check(self, pos: Position, state: MarketState | None, now_utc: datetime) -> tuple[str, ...] | None:
        reasons: list[str] = []
        buy = pos.side is Side.BUY
        if state is not None and state.regime is not Regime.UNKNOWN:
            self._entry.setdefault(pos.ticket, state.regime)
            entry, now = self._entry[pos.ticket], state.regime
            if (buy and now is Regime.TREND_DOWN) or (not buy and now is Regime.TREND_UP):
                reasons.append("régime opposé à la position")
            if now is Regime.HIGH_VOL and entry is not Regime.HIGH_VOL:
                reasons.append("volatilité anormale")
            favourable = Regime.TREND_UP if buy else Regime.TREND_DOWN
            if entry is favourable and now is Regime.RANGE:
                reasons.append("tendance perdue")
        if self._blackout is not None:
            event = self._blackout(pos.symbol, now_utc)
            if event:
                reasons.append(f"annonce imminente ({event})")
        result = tuple(sorted(reasons))
        if result == self._last.get(pos.ticket, ()):
            return None
        self._last[pos.ticket] = result
        return result or None
