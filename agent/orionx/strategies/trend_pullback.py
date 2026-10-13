"""Stratégie v1 (T-12) : tendance H1 + repli sur la moyenne rapide.

ACHAT  : EMA20 > EMA50, clôture > EMA50, EMA50 en hausse ; l'une des 3 bougies précédentes a touché l'EMA20 ;
         la dernière clôture repasse au-dessus de l'EMA20 ; RSI entre 40 et 65.
VENTE  : symétrique (RSI entre 35 et 60).
Stop = 1,5 ATR, objectif = 2 × la distance du stop. La stratégie ne fait QUE proposer : le gestionnaire de
risques décide. Aucune garantie de rentabilité : à valider par backtest puis en démo.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from orionx.core.models import Bar, Side, Tick, TradeProposal
from orionx.strategies.indicators import atr_last, ema_series, rsi_last


class Strategy(Protocol):
    name: str
    timeframe: str
    min_bars: int

    def generate(
        self, symbol: str, bars: Sequence[Bar], tick: Tick, now: datetime, open_symbols: set[str]
    ) -> TradeProposal | None: ...


class TrendPullback:
    name = "trend_pullback_v1"
    timeframe = "H1"

    def __init__(self, fast: int = 20, slow: int = 50, atr_n: int = 14, rsi_n: int = 14, sl_atr: float = 1.5,
                 rr: float = 2.0, slope_bars: int = 5, touch_bars: int = 3, valid_seconds: float = 90.0) -> None:  # fmt: skip
        self.fast, self.slow, self.atr_n, self.rsi_n = fast, slow, atr_n, rsi_n
        self.sl_atr, self.rr, self.slope_bars, self.touch_bars, self.valid_seconds = sl_atr, rr, slope_bars, touch_bars, valid_seconds
        self.min_bars = slow * 2 + 20

    def generate(
        self, symbol: str, bars: Sequence[Bar], tick: Tick, now: datetime, open_symbols: set[str]
    ) -> TradeProposal | None:
        if symbol in open_symbols or len(bars) < self.min_bars:
            return None
        closes = [b.close for b in bars]
        ef, es = ema_series(closes, self.fast), ema_series(closes, self.slow)
        atr, rsi = atr_last(bars, self.atr_n), rsi_last(closes, self.rsi_n)
        if atr is None or atr <= 0 or rsi is None:
            return None
        last, k = bars[-1], self.slope_bars
        prior = range(-1 - self.touch_bars, -1)
        side: Side | None = None
        if ef[-1] > es[-1] and last.close > es[-1] and es[-1] > es[-1 - k]:
            if any(bars[i].low <= ef[i] for i in prior) and last.close > ef[-1] and 40 <= rsi <= 65:
                side = Side.BUY
        elif ef[-1] < es[-1] and last.close < es[-1] and es[-1] < es[-1 - k]:  # noqa: SIM102
            if any(bars[i].high >= ef[i] for i in prior) and last.close < ef[-1] and 35 <= rsi <= 60:
                side = Side.SELL
        if side is None:
            return None
        buy = side is Side.BUY
        entry = tick.ask if buy else tick.bid
        dist = self.sl_atr * atr
        sl, tp = (entry - dist, entry + self.rr * dist) if buy else (entry + dist, entry - self.rr * dist)
        if sl <= 0 or tp <= 0:
            return None
        strength = abs(ef[-1] - es[-1]) / atr
        why = (
            f"tendance {'haussière' if buy else 'baissière'} (EMA{self.fast} vs EMA{self.slow})",
            f"repli sur l'EMA{self.fast}",
            f"RSI {rsi:.0f}",
            f"ATR {atr:.5f}",
        )
        return TradeProposal(
            id=f"{symbol}-{last.time:%Y%m%d%H%M}-{side.value}",
            symbol=symbol, side=side, sl=sl, tp=tp,
            confidence=min(0.9, 0.5 + min(0.3, strength * 0.1)),
            strategy=self.name, rationale=why, created_at=now, valid_until=now + timedelta(seconds=self.valid_seconds),
        )  # fmt: skip
