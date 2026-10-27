"""État du marché (M3 v1, règles) : régime, volatilité, séance. Sans IA : lisible, testable, explicable."""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from orionx.core.config import AnalysisConfig
from orionx.core.models import Bar
from orionx.strategies.indicators import ema_series


class Regime(str, Enum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    HIGH_VOL = "high_vol"
    UNKNOWN = "unknown"


class Session(str, Enum):
    ASIA = "asia"  # 23h-7h UTC
    LONDON = "london"  # 7h-12h
    OVERLAP = "overlap"  # 12h-16h (Londres + New York)
    NEW_YORK = "new_york"  # 16h-21h
    OFF = "off"  # 21h-23h : liquidité faible


def session_of(utc: datetime) -> Session:
    h = utc.hour + utc.minute / 60
    if 7 <= h < 12:
        return Session.LONDON
    if 12 <= h < 16:
        return Session.OVERLAP
    if 16 <= h < 21:
        return Session.NEW_YORK
    if 21 <= h < 23:
        return Session.OFF
    return Session.ASIA


@dataclass(frozen=True)
class MarketState:
    symbol: str
    bar_time: datetime  # heure du courtier de la dernière bougie fermée
    utc_time: datetime  # idem, convertie en UTC réel
    regime: Regime
    volatility_ratio: float  # ATR actuel / ATR médian
    trend_strength: float  # (EMA20 - EMA50) / ATR
    efficiency: float  # ratio d'efficacité de Kaufman, 0 (bruit) à 1 (ligne droite)
    atr: float
    session: Session


def atr_series(bars: Sequence[Bar], n: int = 14) -> list[float]:
    tr = [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(bars, bars[1:], strict=False)]
    if len(tr) < n:
        return []
    atr = sum(tr[:n]) / n
    out = [atr]
    for v in tr[n:]:
        atr = (atr * (n - 1) + v) / n
        out.append(atr)
    return out


def analyze(symbol: str, bars: Sequence[Bar], cfg: AnalysisConfig, to_utc: Callable[[datetime], datetime]) -> MarketState:
    """Analyse sur des bougies FERMÉES (la dernière est la plus récente)."""
    last = bars[-1]
    utc = to_utc(last.time)
    unknown = MarketState(symbol, last.time, utc, Regime.UNKNOWN, 0.0, 0.0, 0.0, 0.0, session_of(utc))
    if len(bars) < cfg.vol_window + 20:
        return unknown
    atrs = atr_series(bars, 14)
    if not atrs or atrs[-1] <= 0:
        return unknown
    atr = atrs[-1]
    normal = statistics.median(atrs[-cfg.vol_window :])
    vol_ratio = atr / normal if normal > 0 else 0.0
    closes = [b.close for b in bars]
    n = cfg.trend_er_period
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(len(closes) - n, len(closes)))
    er = abs(closes[-1] - closes[-1 - n]) / path if path > 0 else 0.0
    ef, es = ema_series(closes, 20), ema_series(closes, 50)
    strength, slope = (ef[-1] - es[-1]) / atr, (es[-1] - es[-6]) / atr
    if vol_ratio >= cfg.high_vol_ratio:
        regime = Regime.HIGH_VOL
    elif er >= cfg.trend_er and strength >= cfg.trend_strength_min and slope > 0:
        regime = Regime.TREND_UP
    elif er >= cfg.trend_er and strength <= -cfg.trend_strength_min and slope < 0:
        regime = Regime.TREND_DOWN
    else:
        regime = Regime.RANGE
    return MarketState(symbol, last.time, utc, regime, vol_ratio, strength, er, atr, session_of(utc))
