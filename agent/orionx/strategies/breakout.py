"""Stratégie 3 : cassure d'un canal (hypothèse À TESTER, non validée).

ACHAT : la dernière clôture dépasse le plus haut des 20 bougies précédentes, avec une bougie d'amplitude >= 1 ATR.
VENTE : symétrique. Stop = 1,5 ATR, objectif = 2 × la distance du stop.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from orionx.core.models import Bar, Side, Tick, TradeProposal
from orionx.strategies.indicators import atr_last


class Breakout:
    name = "breakout_v1"
    timeframe = "H1"

    def __init__(self, lookback: int = 20, sl_atr: float = 1.5, rr: float = 2.0, min_range_atr: float = 1.0,
                 valid_seconds: float = 90.0) -> None:  # fmt: skip
        self.lookback, self.sl_atr, self.rr, self.min_range_atr, self.valid_seconds = lookback, sl_atr, rr, min_range_atr, valid_seconds
        self.min_bars = lookback + 40

    def generate(self, symbol: str, bars: Sequence[Bar], tick: Tick, now: datetime, open_symbols: set[str]) -> TradeProposal | None:
        if symbol in open_symbols or len(bars) < self.min_bars:
            return None
        atr = atr_last(bars, 14)
        if atr is None or atr <= 0:
            return None
        last, prior = bars[-1], bars[-1 - self.lookback : -1]
        if (last.high - last.low) < self.min_range_atr * atr:
            return None
        if last.close > max(b.high for b in prior):
            side, entry = Side.BUY, tick.ask
        elif last.close < min(b.low for b in prior):
            side, entry = Side.SELL, tick.bid
        else:
            return None
        dist = self.sl_atr * atr
        sl, tp = (entry - dist, entry + self.rr * dist) if side is Side.BUY else (entry + dist, entry - self.rr * dist)
        if sl <= 0 or tp <= 0:
            return None
        return TradeProposal(
            f"{symbol}-bo-{last.time:%Y%m%d%H%M}-{side.value}", symbol, side, sl, tp, 0.55, self.name,
            (f"cassure du canal de {self.lookback} bougies", f"amplitude >= {self.min_range_atr} ATR"),
            now, now + timedelta(seconds=self.valid_seconds),
        )  # fmt: skip
