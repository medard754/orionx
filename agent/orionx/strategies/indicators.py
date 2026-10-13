"""Indicateurs techniques en Python pur (sans dépendance)."""

from __future__ import annotations

from collections.abc import Sequence

from orionx.core.models import Bar


def ema_series(values: Sequence[float], n: int) -> list[float]:
    k = 2.0 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi_last(closes: Sequence[float], n: int = 14) -> float | None:
    """RSI de Wilder, dernière valeur. None s'il manque des données."""
    if len(closes) <= n:
        return None
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_g, avg_l = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, lo in zip(gains[n:], losses[n:], strict=True):
        avg_g, avg_l = (avg_g * (n - 1) + g) / n, (avg_l * (n - 1) + lo) / n
    if avg_l == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_g / avg_l)


def atr_last(bars: Sequence[Bar], n: int = 14) -> float | None:
    """ATR de Wilder, dernière valeur."""
    if len(bars) <= n:
        return None
    tr = [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(bars, bars[1:], strict=False)]
    atr = sum(tr[:n]) / n
    for v in tr[n:]:
        atr = (atr * (n - 1) + v) / n
    return atr
