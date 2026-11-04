"""H-3 : détecteurs mécaniques des règles du catalogue (`docs/validation/H-3_catalogue_regles_cours.md`, §4)
et du protocole (`H-3_protocole.md`).

Fonctions pures : des bougies fermées en entrée, des signaux en sortie. Aucune lecture du courtier, aucun ordre,
aucune dépendance aux stratégies d'ORIONX. Les seuils ci-dessous sont ceux du catalogue, écrits AVANT toute mesure ;
ils ne seront pas ajustés après avoir vu un résultat (principe n°10).

Tranche 1 : famille H-3a (chandeliers). Les familles b à f suivront dans ce même module.
"""

from __future__ import annotations

from dataclasses import dataclass

from orionx.core.models import Bar

# --- seuils figés (catalogue §4) -----------------------------------------------------------------------------------
DOJI_BODY_MAX = 0.1  # corps <= 10 % de l'amplitude
SMALL_BODY_MAX = 0.3  # « petit » corps : <= 30 % de l'amplitude
BIG_BODY_MIN = 0.6  # « grand » corps : >= 60 % de l'amplitude
MAROBOZU_BODY_MIN = 0.9
LONG_SHADOW_RATIO = 2.0  # longue ombre : >= 2 fois le corps
SHORT_SHADOW_MAX = 0.1  # ombre « absente » : <= 10 % de l'amplitude
SMA_SLOW = 50
SMA_LOOKBACK = 10  # pente de la SMA(50) mesurée sur 10 bougies


@dataclass(frozen=True)
class PatternSignal:
    index: int  # position de la DERNIÈRE bougie de la figure : le signal n'existe qu'à sa clôture
    name: str
    direction: int  # +1 : haussier annoncé, -1 : baissier annoncé


def sma(closes: list[float], n: int, i: int) -> float | None:
    """Moyenne simple des n clôtures finissant à l'indice i inclus ; None si l'historique manque."""
    if i + 1 < n:
        return None
    return sum(closes[i - n + 1 : i + 1]) / n


def trend_context(bars: list[Bar], i: int) -> int:
    """+1 contexte haussier, -1 baissier, 0 indéterminé.

    Catalogue §4 : clôture sous (ou sur) la SMA(50) ET SMA(50) en pente dans le même sens."""
    closes = [b.close for b in bars]
    now, before = sma(closes, SMA_SLOW, i), sma(closes, SMA_SLOW, i - SMA_LOOKBACK)
    if now is None or before is None:
        return 0
    if bars[i].close < now and now < before:
        return -1
    if bars[i].close > now and now > before:
        return 1
    return 0


def _range(b: Bar) -> float:
    return b.high - b.low


def _body(b: Bar) -> float:
    return abs(b.close - b.open)


def _upper(b: Bar) -> float:
    return b.high - max(b.open, b.close)


def _lower(b: Bar) -> float:
    return min(b.open, b.close) - b.low


def _green(b: Bar) -> bool:
    return b.close > b.open


def _red(b: Bar) -> bool:
    return b.close < b.open


def _single(bars: list[Bar], i: int) -> list[PatternSignal]:
    b = bars[i]
    out: list[PatternSignal] = []
    r = _range(b)
    if r <= 0:
        return out
    body, up, low = _body(b), _upper(b), _lower(b)
    ctx = trend_context(bars, i)
    if body <= DOJI_BODY_MAX * r and ctx != 0:
        out.append(PatternSignal(i, "doji", -ctx))  # l'indécision se lit comme un retournement du contexte
    if body <= SMALL_BODY_MAX * r and low >= LONG_SHADOW_RATIO * body and up <= SHORT_SHADOW_MAX * r:
        if ctx == -1:
            out.append(PatternSignal(i, "marteau", 1))
        elif ctx == 1:
            out.append(PatternSignal(i, "pendu", -1))
    if body <= SMALL_BODY_MAX * r and up >= LONG_SHADOW_RATIO * body and low <= SHORT_SHADOW_MAX * r:
        if ctx == -1:
            out.append(PatternSignal(i, "marteau_inverse", 1))
        elif ctx == 1:
            out.append(PatternSignal(i, "etoile_filante", -1))
    if body >= MAROBOZU_BODY_MIN * r and body > 0:
        out.append(PatternSignal(i, "marobozu", 1 if _green(b) else -1))  # figure de continuation
    return out


def _double(bars: list[Bar], i: int) -> list[PatternSignal]:
    prev, cur = bars[i - 1], bars[i]
    out: list[PatternSignal] = []
    if _body(prev) == 0 or _body(cur) == 0:
        return out
    if _red(prev) and _green(cur) and cur.open <= prev.close and cur.close >= prev.open:
        out.append(PatternSignal(i, "avalement", 1))
    elif _green(prev) and _red(cur) and cur.open >= prev.close and cur.close <= prev.open:
        out.append(PatternSignal(i, "avalement", -1))
    p_top, p_bot = max(prev.open, prev.close), min(prev.open, prev.close)
    c_top, c_bot = max(cur.open, cur.close), min(cur.open, cur.close)
    if _range(prev) > 0 and _body(prev) >= BIG_BODY_MIN * _range(prev) and c_top <= p_top and c_bot >= p_bot:
        if _red(prev) and _green(cur):
            out.append(PatternSignal(i, "harami", 1))
        elif _green(prev) and _red(cur):
            out.append(PatternSignal(i, "harami", -1))
    return out


def _triple(bars: list[Bar], i: int) -> list[PatternSignal]:
    a, b, c = bars[i - 2], bars[i - 1], bars[i]
    out: list[PatternSignal] = []
    if any(_range(x) <= 0 for x in (a, b, c)):
        return out
    mid_a = (a.open + a.close) / 2
    star_small = _body(b) <= SMALL_BODY_MAX * _range(b)
    if _red(a) and _body(a) >= BIG_BODY_MIN * _range(a) and star_small and max(b.open, b.close) < a.close and _green(c) and c.close > mid_a:
        out.append(PatternSignal(i, "etoile_matin", 1))
    if _green(a) and _body(a) >= BIG_BODY_MIN * _range(a) and star_small and min(b.open, b.close) > a.close and _red(c) and c.close < mid_a:
        out.append(PatternSignal(i, "etoile_soir", -1))
    trio = (a, b, c)
    if all(_body(x) >= BIG_BODY_MIN * _range(x) for x in trio):
        if all(_green(x) for x in trio) and a.close < b.close < c.close and a.low < b.open <= a.close and b.low < c.open <= b.close:
            out.append(PatternSignal(i, "trois_soldats", 1))
        if all(_red(x) for x in trio) and a.close > b.close > c.close and a.close <= b.open < a.high and b.close <= c.open < b.high:
            out.append(PatternSignal(i, "trois_corbeaux", -1))
    return out


def detect_candlesticks(bars: list[Bar]) -> list[PatternSignal]:
    """Tous les signaux de la famille H-3a, bougie par bougie, sans regarder à droite de l'indice courant."""
    signals: list[PatternSignal] = []
    for i in range(len(bars)):
        signals += _single(bars, i)
        if i >= 1:
            signals += _double(bars, i)
        if i >= 2:
            signals += _triple(bars, i)
    return signals


CANDLESTICK_NAMES = (
    "doji", "marteau", "pendu", "marteau_inverse", "etoile_filante", "marobozu", "avalement", "harami", "etoile_matin", "etoile_soir",
    "trois_soldats", "trois_corbeaux",
)  # fmt: skip
