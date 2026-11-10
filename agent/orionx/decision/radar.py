# ruff: noqa: E501  (libellés affichés à l'utilisateur)
"""Radar de signaux : pour chaque stratégie, quelles conditions sont remplies et lesquelles manquent. Lecture seule.

Ce module ne décide rien et ne change aucun seuil : il relit les conditions des stratégies (mêmes constantes, mêmes indicateurs) pour
les AFFICHER. Un test vérifie que lorsqu'une stratégie propose un trade, toutes les conditions du radar sont remplies.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from typing import Any

from orionx.core.models import Bar
from orionx.strategies.breakout import Breakout
from orionx.strategies.indicators import atr_last, ema_series, rsi_last
from orionx.strategies.mean_reversion import MeanReversion
from orionx.strategies.trend_pullback import TrendPullback

Check = dict[str, Any]


def _check(label: str, ok: bool, detail: str) -> Check:
    return {"label": label, "ok": bool(ok), "detail": detail}


def _entry(strategy: str, side: str, checks: list[Check], applies: bool, why_not: str) -> dict[str, Any]:
    done = sum(1 for c in checks if c["ok"])
    return {
        "strategy": strategy, "side": side, "applies": applies, "regime_note": why_not,
        "checks": checks, "done": done, "total": len(checks), "ready": applies and done == len(checks),
    }  # fmt: skip


def trend_pullback_radar(bars: Sequence[Bar], s: TrendPullback | None = None) -> tuple[str, list[Check]]:
    s = s or TrendPullback()
    if len(bars) < s.min_bars:
        return "-", [_check("Historique suffisant", False, f"{len(bars)} bougies sur {s.min_bars}")]
    closes = [b.close for b in bars]
    ef, es = ema_series(closes, s.fast), ema_series(closes, s.slow)
    atr, rsi = atr_last(bars, s.atr_n), rsi_last(closes, s.rsi_n)
    if atr is None or rsi is None:
        return "-", [_check("Indicateurs calculables", False, "ATR ou RSI indisponible")]
    last, k = bars[-1], s.slope_bars
    prior = range(-1 - s.touch_bars, -1)
    buy = ef[-1] >= es[-1]
    if buy:
        checks = [
            _check(f"EMA{s.fast} au-dessus de EMA{s.slow}", ef[-1] > es[-1], f"{ef[-1]:.5f} vs {es[-1]:.5f}"),
            _check(f"Clôture au-dessus de EMA{s.slow}", last.close > es[-1], f"{last.close:.5f} vs {es[-1]:.5f}"),
            _check(f"EMA{s.slow} en hausse ({k} bougies)", es[-1] > es[-1 - k], f"{es[-1]:.5f} vs {es[-1 - k]:.5f}"),
            _check(f"Repli : une des {s.touch_bars} bougies précédentes a touché EMA{s.fast}", any(bars[i].low <= ef[i] for i in prior), "contact récent"),
            _check(f"Clôture repasse au-dessus de EMA{s.fast}", last.close > ef[-1], f"{last.close:.5f} vs {ef[-1]:.5f}"),
            _check("RSI entre 40 et 65", 40 <= rsi <= 65, f"RSI {rsi:.0f}"),
        ]  # fmt: skip
    else:
        checks = [
            _check(f"EMA{s.fast} sous EMA{s.slow}", ef[-1] < es[-1], f"{ef[-1]:.5f} vs {es[-1]:.5f}"),
            _check(f"Clôture sous EMA{s.slow}", last.close < es[-1], f"{last.close:.5f} vs {es[-1]:.5f}"),
            _check(f"EMA{s.slow} en baisse ({k} bougies)", es[-1] < es[-1 - k], f"{es[-1]:.5f} vs {es[-1 - k]:.5f}"),
            _check(f"Rebond : une des {s.touch_bars} bougies précédentes a touché EMA{s.fast}", any(bars[i].high >= ef[i] for i in prior), "contact récent"),
            _check(f"Clôture repasse sous EMA{s.fast}", last.close < ef[-1], f"{last.close:.5f} vs {ef[-1]:.5f}"),
            _check("RSI entre 35 et 60", 35 <= rsi <= 60, f"RSI {rsi:.0f}"),
        ]  # fmt: skip
    return "achat" if buy else "vente", checks


def mean_reversion_radar(bars: Sequence[Bar], s: MeanReversion | None = None) -> tuple[str, list[Check]]:
    s = s or MeanReversion()
    if len(bars) < s.min_bars:
        return "-", [_check("Historique suffisant", False, f"{len(bars)} bougies sur {s.min_bars}")]
    closes = [b.close for b in bars]
    window = closes[-s.period :]
    sma, sd = statistics.fmean(window), statistics.pstdev(window)
    rsi = rsi_last(closes, 14)
    if rsi is None or sd <= 0:
        return "-", [_check("Indicateurs calculables", False, "RSI ou écart-type indisponible")]
    last = bars[-1]
    buy = last.close <= sma
    if buy:
        checks = [
            _check(f"Clôture sous la bande basse ({s.k}σ)", last.close < sma - s.k * sd, f"{last.close:.5f} vs {sma - s.k * sd:.5f}"),
            _check(f"RSI sous {s.rsi_low:.0f}", rsi < s.rsi_low, f"RSI {rsi:.0f}"),
        ]  # fmt: skip
    else:
        checks = [
            _check(f"Clôture au-dessus de la bande haute ({s.k}σ)", last.close > sma + s.k * sd, f"{last.close:.5f} vs {sma + s.k * sd:.5f}"),
            _check(f"RSI au-dessus de {s.rsi_high:.0f}", rsi > s.rsi_high, f"RSI {rsi:.0f}"),
        ]  # fmt: skip
    return "achat" if buy else "vente", checks


def breakout_radar(bars: Sequence[Bar], s: Breakout | None = None) -> tuple[str, list[Check]]:
    s = s or Breakout()
    if len(bars) < s.min_bars:
        return "-", [_check("Historique suffisant", False, f"{len(bars)} bougies sur {s.min_bars}")]
    atr = atr_last(bars, 14)
    if atr is None or atr <= 0:
        return "-", [_check("ATR calculable", False, "ATR indisponible")]
    last, prior = bars[-1], bars[-1 - s.lookback : -1]
    hi, lo = max(b.high for b in prior), min(b.low for b in prior)
    buy = (hi - last.close) <= (last.close - lo)  # on regarde le côté dont la clôture est la plus proche
    wide = (last.high - last.low) >= s.min_range_atr * atr
    breaks = last.close > hi if buy else last.close < lo
    return ("achat" if buy else "vente"), [
        _check(f"Amplitude de la bougie d'au moins {s.min_range_atr} ATR", wide, f"{(last.high - last.low) / atr:.2f} ATR"),
        _check(
            f"Clôture {'au-dessus du plus haut' if buy else 'sous le plus bas'} des {s.lookback} bougies",
            breaks, f"{last.close:.5f} vs {hi if buy else lo:.5f}",
        ),
    ]  # fmt: skip


RADARS: dict[str, Callable[[Sequence[Bar]], tuple[str, list[Check]]]] = {
    "trend_pullback": trend_pullback_radar,
    "mean_reversion": mean_reversion_radar,
    "breakout": breakout_radar,
}


def build_radar(
    symbol: str, bars: Sequence[Bar], regime: str | None, entries: Sequence[tuple[str, set[str] | None]]
) -> list[dict[str, Any]]:
    """`entries` : (nom court de la stratégie, régimes d'application ou None). Retourne une ligne par stratégie activée."""
    out = []
    for name, regimes in entries:
        fn = RADARS.get(name)
        if fn is None:
            continue
        side, checks = fn(bars)
        applies = regimes is None or regime is None or regime in regimes
        note = "" if applies else f"régime actuel « {regime} » hors du champ de cette stratégie"
        row = _entry(name, side, checks, applies, note)
        row["symbol"] = symbol
        out.append(row)
    return out
