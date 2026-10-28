"""Filtres d'entrée = hypothèses à TESTER par backtest (jamais à activer sans preuve)."""

from __future__ import annotations

from collections.abc import Callable

from orionx.analysis.state import MarketState, Regime, Session
from orionx.core.models import Side, TradeProposal

EntryFilter = Callable[[MarketState, TradeProposal], bool]


def no_high_vol(state: MarketState, prop: TradeProposal) -> bool:
    return state.regime is not Regime.HIGH_VOL


def trend_aligned(state: MarketState, prop: TradeProposal) -> bool:
    return state.regime is (Regime.TREND_UP if prop.side is Side.BUY else Regime.TREND_DOWN)


def sessions(*allowed: Session) -> EntryFilter:
    ok = set(allowed)
    return lambda state, prop: state.session in ok


def combine(*filters: EntryFilter) -> EntryFilter:
    return lambda state, prop: all(f(state, prop) for f in filters)


LIQUID = sessions(Session.LONDON, Session.OVERLAP, Session.NEW_YORK)
FILTERS: dict[str, EntryFilter | None] = {
    "none": None,
    "vol": no_high_vol,
    "trend": trend_aligned,
    "session": LIQUID,
    "all": combine(no_high_vol, trend_aligned, LIQUID),
}
