"""Référence aléatoire : mêmes stops, objectifs et coûts que la stratégie, mais entrées au hasard.

Sert de témoin : si une stratégie ne fait pas nettement mieux que le hasard soumis aux MÊMES coûts,
elle n'a pas d'avantage démontré.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import datetime, timedelta

from orionx.core.models import Bar, Side, Tick, TradeProposal
from orionx.strategies.indicators import atr_last


class RandomEntry:
    name = "random_baseline"
    timeframe = "H1"

    def __init__(self, seed: int = 0, prob: float = 0.04, sl_atr: float = 1.5, rr: float = 2.0) -> None:
        self._rnd = random.Random(seed)
        self.prob, self.sl_atr, self.rr = prob, sl_atr, rr
        self.min_bars = 120

    def generate(
        self, symbol: str, bars: Sequence[Bar], tick: Tick, now: datetime, open_symbols: set[str]
    ) -> TradeProposal | None:
        if symbol in open_symbols or len(bars) < self.min_bars or self._rnd.random() > self.prob:
            return None
        atr = atr_last(bars, 14)
        if atr is None or atr <= 0:
            return None
        side = self._rnd.choice([Side.BUY, Side.SELL])
        buy = side is Side.BUY
        entry = tick.ask if buy else tick.bid
        dist = self.sl_atr * atr
        sl, tp = (entry - dist, entry + self.rr * dist) if buy else (entry + dist, entry - self.rr * dist)
        return TradeProposal(
            f"rnd-{bars[-1].time:%Y%m%d%H%M}", symbol, side, sl, tp, 0.5, self.name, ("hasard",),
            now, now + timedelta(seconds=90),
        )  # fmt: skip
