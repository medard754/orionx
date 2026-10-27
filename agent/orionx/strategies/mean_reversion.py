"""Stratégie 2 : retour à la moyenne en marché sans tendance (hypothèse À TESTER, non validée).

ACHAT : clôture sous la bande de Bollinger basse (20, 2σ) et RSI < 30 ; objectif = moyenne ; stop = 1,5 ATR.
VENTE : symétrique. Utile surtout en régime `range` (voir decision/registry.py).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime, timedelta

from orionx.core.models import Bar, Side, Tick, TradeProposal
from orionx.strategies.indicators import atr_last, rsi_last


class MeanReversion:
    name = "mean_reversion_v1"
    timeframe = "H1"

    def __init__(self, period: int = 20, k: float = 2.0, rsi_low: float = 30.0, rsi_high: float = 70.0,
                 sl_atr: float = 1.5, min_rr: float = 1.0, valid_seconds: float = 90.0) -> None:  # fmt: skip
        self.period, self.k, self.rsi_low, self.rsi_high = period, k, rsi_low, rsi_high
        self.sl_atr, self.min_rr, self.valid_seconds = sl_atr, min_rr, valid_seconds
        self.min_bars = period + 40

    def generate(self, symbol: str, bars: Sequence[Bar], tick: Tick, now: datetime, open_symbols: set[str]) -> TradeProposal | None:
        if symbol in open_symbols or len(bars) < self.min_bars:
            return None
        closes = [b.close for b in bars]
        window = closes[-self.period :]
        sma, sd = statistics.fmean(window), statistics.pstdev(window)
        atr, rsi = atr_last(bars, 14), rsi_last(closes, 14)
        if atr is None or atr <= 0 or rsi is None or sd <= 0:
            return None
        last = bars[-1]
        if last.close < sma - self.k * sd and rsi < self.rsi_low:
            side, entry = Side.BUY, tick.ask
            sl, tp = entry - self.sl_atr * atr, sma
            ok = tp > entry
        elif last.close > sma + self.k * sd and rsi > self.rsi_high:
            side, entry = Side.SELL, tick.bid
            sl, tp = entry + self.sl_atr * atr, sma
            ok = tp < entry
        else:
            return None
        if not ok or sl <= 0 or abs(tp - entry) / abs(entry - sl) < self.min_rr:
            return None
        return TradeProposal(
            f"{symbol}-mr-{last.time:%Y%m%d%H%M}-{side.value}", symbol, side, sl, tp, 0.5 + min(0.2, (70 - rsi if side is Side.BUY else rsi - 30) / 100),
            self.name, (f"clôture hors bande de Bollinger ({self.k}σ)", f"RSI {rsi:.0f}", "objectif : retour à la moyenne"),
            now, now + timedelta(seconds=self.valid_seconds),
        )  # fmt: skip
