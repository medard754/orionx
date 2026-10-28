"""Photographie de l'analyse de marché d'un symbole, pour le dashboard (onglets « Analyse du marché »).

Calcul pur : aucun appel au courtier, aucune décision. Tout vient de ce que l'agent a déjà calculé (régime, séance, volatilité)
ou d'indicateurs classiques recalculés sur les mêmes bougies H1. Les « lectures » DÉCRIVENT l'état du marché ; elles ne
disent jamais d'acheter ou de vendre (ce n'est pas un conseil en investissement).
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from orionx.analysis.calendar import EconomicEvent
from orionx.analysis.state import MarketState, Regime
from orionx.core.config import AnalysisConfig
from orionx.core.models import Bar, Tick
from orionx.strategies.indicators import atr_last, ema_series, rsi_last

REGIME_LABELS = {"trend_up": "Tendance haussière", "trend_down": "Tendance baissière", "range": "Range (sans tendance)",
                 "high_vol": "Forte volatilité", "unknown": "Indéterminé"}  # fmt: skip
SESSION_LABELS = {"asia": "Asie", "london": "Londres", "overlap": "Londres + New York", "new_york": "New York", "off": "Hors séance"}
CHART_BARS = 120


@dataclass(frozen=True)
class StrategyInfo:
    name: str
    regimes: tuple[str, ...] | None  # None : la stratégie s'applique à tous les régimes
    enabled: bool


def _r(x: float | None, nd: int = 5) -> float | None:
    return None if x is None else round(x, nd)


def _pct_b(closes: Sequence[float], n: int = 20, k: float = 2.0) -> tuple[float | None, float | None]:
    """%B de Bollinger (0 = bande basse, 1 = bande haute) et largeur des bandes."""
    if len(closes) < n:
        return None, None
    window = closes[-n:]
    mid, sd = statistics.fmean(window), statistics.pstdev(window)
    width = 2 * k * sd
    return ((closes[-1] - (mid - k * sd)) / width if width else 0.5), width


def build_snapshot(
    symbol: str,
    bars: Sequence[Bar],
    state: MarketState | None,
    tick: Tick | None,
    pip: float,
    now_utc: datetime,
    to_utc: Callable[[datetime], datetime],
    cfg: AnalysisConfig,
    *,
    events: Sequence[EconomicEvent] = (),
    blackout: str | None = None,
    strategies: Sequence[StrategyInfo] = (),
    halts: Sequence[str] = (),
    notes: Sequence[str] = (),
    notes_at: datetime | None = None,
    theses: Sequence[tuple[int, Sequence[str]]] = (),
) -> dict[str, Any]:
    closes = [b.close for b in bars]
    price = (tick.bid + tick.ask) / 2 if tick else (closes[-1] if closes else None)
    snap: dict[str, Any] = {"symbol": symbol, "updated_at": now_utc.isoformat(), "bars": len(bars),
                            "price": {"bid": tick.bid if tick else None, "ask": tick.ask if tick else None,
                                      "spread_pips": _r((tick.ask - tick.bid) / pip, 1) if tick else None}}  # fmt: skip
    regime = state.regime.value if state else "unknown"

    # --- tendance --------------------------------------------------------------------------------
    ema20 = ema_series(closes, 20) if len(closes) >= 20 else []
    ema50 = ema_series(closes, 50) if len(closes) >= 50 else []
    e20, e50 = (ema20[-1] if ema20 else None), (ema50[-1] if ema50 else None)
    slope = (ema20[-1] - ema20[-6]) / 5 / pip if len(ema20) >= 6 else None
    if price is not None and e20 is not None and e50 is not None:
        side = "au-dessus" if price > max(e20, e50) else "en dessous" if price < min(e20, e50) else "entre"
        trend_txt = f"{REGIME_LABELS[regime]} : le prix est {side} des moyennes mobiles 20 et 50"
        trend_txt += f", EMA20 {'au-dessus' if e20 > e50 else 'en dessous'} de l'EMA50."
    else:
        trend_txt = "Pas encore assez de bougies pour lire la tendance."
    snap["trend"] = {"regime": regime, "regime_label": REGIME_LABELS[regime], "ema20": _r(e20), "ema50": _r(e50),
                     "slope_pips_per_bar": _r(slope, 2), "trend_strength": _r(state.trend_strength, 2) if state else None,
                     "efficiency": _r(state.efficiency, 2) if state else None, "reading": trend_txt}  # fmt: skip

    # --- momentum --------------------------------------------------------------------------------
    rsi = rsi_last(closes, 14)
    zone = None if rsi is None else "suracheté" if rsi >= 70 else "survendu" if rsi <= 30 else "neutre"
    change = (closes[-1] - closes[-25]) if len(closes) >= 25 else None
    mom_txt = "Pas encore assez de bougies." if rsi is None else (
        f"RSI 14 à {rsi:.0f} : zone {zone}." + (f" Variation sur 24 h : {change / pip:+.1f} pips." if change is not None else ""))
    snap["momentum"] = {
        "rsi14": _r(rsi, 1), "rsi_zone": zone, "reading": mom_txt,
        "change_24h_pips": _r(change / pip, 1) if change is not None else None,
        "change_24h_pct": _r(change / closes[-25] * 100, 3) if change is not None else None,
    }

    # --- volatilité --------------------------------------------------------------------------------
    atr = atr_last(bars, 14)
    pb, width = _pct_b(closes)
    ratio = state.volatility_ratio if state else None
    if ratio is None:
        vol_txt = "Volatilité pas encore mesurée."
    else:
        level = ("anormalement forte" if ratio >= cfg.high_vol_ratio else "élevée" if ratio >= 1.2
                 else "faible" if ratio < 0.8 else "normale")
        vol_txt = f"Volatilité {level} : ATR à {ratio:.2f} fois sa médiane (seuil de blocage : {cfg.high_vol_ratio:.1f})."
    snap["volatility"] = {
        "atr_pips": _r(atr / pip, 1) if atr else None, "volatility_ratio": _r(ratio, 2), "high_vol_ratio": cfg.high_vol_ratio,
        "bb_width_pips": _r(width / pip, 1) if width else None, "percent_b": _r(pb, 2), "reading": vol_txt,
    }

    # --- niveaux (24 dernières bougies H1) -------------------------------------------------------
    last24 = list(bars[-24:])
    if last24:
        hi, lo, cl = max(b.high for b in last24), min(b.low for b in last24), last24[-1].close
        piv = (hi + lo + cl) / 3
        pos = (price - lo) / (hi - lo) * 100 if price is not None and hi > lo else None
        snap["levels"] = {"high_24h": _r(hi), "low_24h": _r(lo), "pivot": _r(piv), "r1": _r(2 * piv - lo), "s1": _r(2 * piv - hi),
                          "r2": _r(piv + (hi - lo)), "s2": _r(piv - (hi - lo)), "position_in_range_pct": _r(pos, 0),
                          "reading": f"Prix à {pos:.0f} % de la plage des 24 dernières heures ({(hi - lo) / pip:.0f} pips)."
                          if pos is not None else "Plage indisponible."}  # fmt: skip
    else:
        snap["levels"] = {"reading": "Plage indisponible."}

    # --- séance et annonces --------------------------------------------------------------------
    session = state.session.value if state else None
    blocked = session in cfg.blocked_sessions if session else False
    ev = [{"time": e.time.isoformat(), "currency": e.currency, "impact": e.impact, "title": e.title,
           "in_minutes": round((e.time - now_utc).total_seconds() / 60)} for e in events]  # fmt: skip
    sess_txt = f"Séance {SESSION_LABELS.get(session or '', '?')}" + (" (bloquée par la configuration)" if blocked else "") + ". "
    sess_txt += f"Fenêtre d'annonce en cours : {blackout} : aucune entrée." if blackout else (
        f"{len(ev)} annonce(s) dans les 24 h." if ev else "Aucune annonce importante dans les 24 h (calendrier configuré).")
    snap["session"] = {"session": session, "session_label": SESSION_LABELS.get(session or "", "?"), "blocked": blocked,
                       "blackout": blackout, "events": ev, "reading": sess_txt}  # fmt: skip

    # --- décision de l'agent ---------------------------------------------------------------------
    strat = []
    for s in strategies:
        eligible = s.enabled and (s.regimes is None or regime in s.regimes)
        why = "désactivée" if not s.enabled else "tous régimes" if s.regimes is None else (
            "régime compatible" if eligible else f"attend : {', '.join(REGIME_LABELS.get(r, r) for r in s.regimes)}")
        strat.append({"name": s.name, "eligible": eligible, "why": why})
    blocks = list(halts)
    if state is not None and state.regime is Regime.HIGH_VOL and cfg.block_high_vol:
        blocks.append("forte volatilité")
    if blocked:
        blocks.append("séance bloquée")
    if blackout:
        blocks.append(f"annonce : {blackout}")
    n_ok = sum(1 for s in strat if s["eligible"])
    dec_txt = (f"Entrées bloquées : {', '.join(blocks)}." if blocks else
               f"{n_ok} stratégie(s) applicable(s) dans ce régime ; l'agent évalue à chaque clôture de bougie H1.")  # fmt: skip
    snap["decision"] = {
        "strategies": strat, "blocks": blocks, "last_notes": list(notes), "reading": dec_txt,
        "notes_at": notes_at.isoformat() if notes_at else None,
        "positions": [{"ticket": t, "thesis": list(r)} for t, r in theses],
    }

    # --- graphique (dernières bougies, en UTC) ---------------------------------------------------
    tail = list(bars[-CHART_BARS:])
    k = len(tail)
    snap["chart"] = {
        "times": [to_utc(b.time).isoformat() for b in tail], "closes": [_r(b.close) for b in tail],
        "ema20": [_r(v) for v in ema20[-k:]] if ema20 else [], "ema50": [_r(v) for v in ema50[-k:]] if ema50 else [],
    }
    return snap
