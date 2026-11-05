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


# --- tranche 2 : indicateurs, pivots (zigzag), figures, divergences, étranglement, Fibonacci ---------------------------
ATR_PERIOD = 14
ZIGZAG_ATR = 3.0  # un pivot n'est confirmé qu'après un retournement d'au moins 3 ATR
DOUBLE_TOLERANCE_ATR = 0.5  # deux sommets (ou creux) à 0,5 ATR près
VALLEY_MIN_ATR = 2.0  # creux intermédiaire d'au moins 2 ATR
SHOULDER_TOLERANCE_ATR = 1.0
HEAD_MIN_ATR = 1.0
FIGURE_LIFETIME = 60  # une figure non cassée au bout de 60 bougies expire
RSI_PERIOD = 14
BB_PERIOD = 20
BB_LOOKBACK = 250
BB_QUANTILE = 0.10
FIB_RATIOS = (0.382, 0.5, 0.618)
FIB_WITNESS_RATIOS = (0.30, 0.45, 0.55, 0.70)  # niveaux témoins : sans eux, « Fibonacci marche » ne prouve rien
FIB_LEG_ATR = 3.0
FIB_RACE_ATR = 1.0
FIB_RACE_BARS = 48
FIB_LIFETIME = 60


@dataclass(frozen=True)
class Pivot:
    index: int  # bougie du sommet ou du creux
    price: float
    kind: str  # "H" sommet, "L" creux
    confirmed_at: int  # bougie à laquelle le pivot devient CONNU (jamais avant)


def atr_series(bars: list[Bar], n: int = ATR_PERIOD) -> list[float | None]:
    """ATR simple (moyenne des n dernières amplitudes vraies) ; None tant que l'historique manque."""
    tr = [bars[0].high - bars[0].low]
    for k in range(1, len(bars)):
        prev_close = bars[k - 1].close
        tr.append(max(bars[k].high - bars[k].low, abs(bars[k].high - prev_close), abs(bars[k].low - prev_close)))
    return [sum(tr[k - n + 1 : k + 1]) / n if k + 1 >= n else None for k in range(len(bars))]


def rsi_series(closes: list[float], n: int = RSI_PERIOD) -> list[float | None]:
    """RSI de Wilder ; None tant que l'historique manque."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    avg_g = sum(max(closes[k] - closes[k - 1], 0.0) for k in range(1, n + 1)) / n
    avg_l = sum(max(closes[k - 1] - closes[k], 0.0) for k in range(1, n + 1)) / n

    def value(g: float, lo: float) -> float:
        return 100.0 if lo == 0 else 100.0 - 100.0 / (1.0 + g / lo)

    out[n] = value(avg_g, avg_l)
    for k in range(n + 1, len(closes)):
        change = closes[k] - closes[k - 1]
        avg_g = (avg_g * (n - 1) + max(change, 0.0)) / n
        avg_l = (avg_l * (n - 1) + max(-change, 0.0)) / n
        out[k] = value(avg_g, avg_l)
    return out


def pivots(bars: list[Bar], atr_mult: float = ZIGZAG_ATR) -> list[Pivot]:
    """Zigzag causal : un pivot est annoncé à la bougie où le retournement atteint `atr_mult` ATR, jamais avant."""
    atr = atr_series(bars)
    start = next((k for k, a in enumerate(atr) if a is not None), None)
    if start is None:
        return []
    out: list[Pivot] = []
    hi_i = lo_i = ext = start
    direction = 0
    for i in range(start + 1, len(bars)):
        thr = atr_mult * float(atr[i] or 0.0)
        b = bars[i]
        if direction == 0:
            if b.high > bars[hi_i].high:
                hi_i = i
            if b.low < bars[lo_i].low:
                lo_i = i
            if lo_i < i and b.high - bars[lo_i].low >= thr:
                out.append(Pivot(lo_i, bars[lo_i].low, "L", i))
                direction, ext = 1, max(range(lo_i, i + 1), key=lambda k: bars[k].high)
            elif hi_i < i and bars[hi_i].high - b.low >= thr:
                out.append(Pivot(hi_i, bars[hi_i].high, "H", i))
                direction, ext = -1, min(range(hi_i, i + 1), key=lambda k: bars[k].low)
        elif direction == 1:
            if b.high > bars[ext].high:
                ext = i
            elif bars[ext].high - b.low >= thr:
                out.append(Pivot(ext, bars[ext].high, "H", i))
                direction, ext = -1, i
        else:
            if b.low < bars[ext].low:
                ext = i
            elif b.high - bars[ext].low >= thr:
                out.append(Pivot(ext, bars[ext].low, "L", i))
                direction, ext = 1, i
    return out


def _neckline_break(bars: list[Bar], first: int, last: int, neck: float, top: bool, invalid: float) -> int | None:
    """Première clôture au-delà de la ligne de cou (sous pour une figure de sommet) ; None si la figure est invalidée avant
    (clôture au-delà du sommet/creux extrême) ou n'est pas cassée avant `last`."""
    for i in range(first, min(last, len(bars) - 1) + 1):
        close = bars[i].close
        if top:
            if close > invalid:
                return None
            if close < neck:
                return i
        else:
            if close < invalid:
                return None
            if close > neck:
                return i
    return None


def detect_figures(bars: list[Bar]) -> list[PatternSignal]:
    """Double sommet / creux et tête-épaules (et inversés). Le signal est la clôture au-delà de la ligne de cou,
    jamais avant la confirmation du dernier pivot de la figure."""
    atr, pv = atr_series(bars), pivots(bars)
    signals: list[PatternSignal] = []
    for k in range(len(pv) - 2):
        a, b, c = pv[k : k + 3]
        if not (a.kind == c.kind and b.kind != a.kind):
            continue
        tol = float(atr[c.confirmed_at] or 0.0)
        top = a.kind == "H"
        depth = (min(a.price, c.price) - b.price) if top else (b.price - max(a.price, c.price))
        if abs(a.price - c.price) <= DOUBLE_TOLERANCE_ATR * tol and depth >= VALLEY_MIN_ATR * tol:
            extreme = max(a.price, c.price) if top else min(a.price, c.price)
            hit = _neckline_break(bars, c.index + 1, c.index + FIGURE_LIFETIME, b.price, top, extreme)
            if hit is not None:
                signals.append(PatternSignal(max(hit, c.confirmed_at), "double_sommet" if top else "double_creux", -1 if top else 1))
    for k in range(len(pv) - 4):
        s1, l1, head, l2, s2 = pv[k : k + 5]
        if not (s1.kind == head.kind == s2.kind and l1.kind == l2.kind != s1.kind):
            continue
        tol = float(atr[s2.confirmed_at] or 0.0)
        top = s1.kind == "H"
        if top:
            head_ok = head.price - max(s1.price, s2.price) >= HEAD_MIN_ATR * tol
        else:
            head_ok = min(s1.price, s2.price) - head.price >= HEAD_MIN_ATR * tol
        if head_ok and abs(s1.price - s2.price) <= SHOULDER_TOLERANCE_ATR * tol:
            hit = _neckline_break(bars, s2.index + 1, s2.index + FIGURE_LIFETIME, (l1.price + l2.price) / 2, top, head.price)
            if hit is not None:
                signals.append(
                    PatternSignal(max(hit, s2.confirmed_at), "tete_epaules" if top else "tete_epaules_inverse", -1 if top else 1)
                )
    return sorted(signals, key=lambda s: (s.index, s.name))


def detect_divergences(bars: list[Bar]) -> list[PatternSignal]:
    """Divergences RSI(14) / prix entre deux pivots consécutifs de même type, annoncées à la confirmation du second."""
    rsi, pv = rsi_series([b.close for b in bars]), pivots(bars)
    signals: list[PatternSignal] = []
    for kind in ("L", "H"):
        same = [p for p in pv if p.kind == kind]
        for a, b in zip(same, same[1:], strict=False):
            ra, rb = rsi[a.index], rsi[b.index]
            if ra is None or rb is None:
                continue
            if kind == "L" and b.price < a.price and rb > ra:
                signals.append(PatternSignal(b.confirmed_at, "divergence_directe_haussiere", 1))
            elif kind == "L" and b.price > a.price and rb < ra:
                signals.append(PatternSignal(b.confirmed_at, "divergence_cachee_haussiere", 1))
            elif kind == "H" and b.price > a.price and rb < ra:
                signals.append(PatternSignal(b.confirmed_at, "divergence_directe_baissiere", -1))
            elif kind == "H" and b.price < a.price and rb > ra:
                signals.append(PatternSignal(b.confirmed_at, "divergence_cachee_baissiere", -1))
    return sorted(signals, key=lambda s: (s.index, s.name))


def bollinger_width(closes: list[float], i: int, n: int = BB_PERIOD) -> float | None:
    """Largeur relative des bandes (n, 2σ) : (haute - basse) / milieu = 4σ / moyenne."""
    if i + 1 < n:
        return None
    window = closes[i - n + 1 : i + 1]
    mean = sum(window) / n
    var = sum((x - mean) ** 2 for x in window) / n
    return 4.0 * var**0.5 / mean if mean else None


def detect_squeezes(bars: list[Bar]) -> list[PatternSignal]:
    """Étranglement : largeur des bandes au plus bas 10 % des 250 largeurs PRÉCÉDENTES. Non directionnel (direction 0)."""
    closes = [b.close for b in bars]
    widths = [bollinger_width(closes, i) for i in range(len(bars))]
    signals: list[PatternSignal] = []
    for i in range(len(bars)):
        w = widths[i]
        past = [x for x in widths[max(0, i - BB_LOOKBACK) : i] if x is not None]
        if w is None or len(past) < BB_LOOKBACK:
            continue
        if w <= sorted(past)[int(BB_QUANTILE * len(past))]:
            signals.append(PatternSignal(i, "etranglement", 0))
    return signals


def volatility_ratio(bars: list[Bar], i: int, horizon: int = 12) -> float | None:
    """Amplitude moyenne des `horizon` bougies qui suivent i, rapportée à celle des 250 précédentes (le passé seulement)."""
    if i + horizon >= len(bars) or i < BB_LOOKBACK:
        return None
    base = sum(b.high - b.low for b in bars[i - BB_LOOKBACK + 1 : i + 1]) / BB_LOOKBACK
    after = sum(b.high - b.low for b in bars[i + 1 : i + 1 + horizon]) / horizon
    return after / base if base > 0 else None


@dataclass(frozen=True)
class FibEntry:
    index: int  # bougie où le niveau est touché
    ratio: float
    direction: int  # sens de la jambe d'impulsion, que le trade cherche à voir reprendre
    price: float  # niveau de retracement (ordre limite supposé rempli à ce prix)
    atr: float


def fib_entries(bars: list[Bar], ratios: tuple[float, ...]) -> list[FibEntry]:
    """Après une jambe d'impulsion (>= 3 ATR entre deux pivots), premier contact de chaque niveau de retracement.
    Invalidé si le prix dépasse l'extrémité de la jambe ou efface 100 % de la jambe ; expire au bout de 60 bougies."""
    atr, pv = atr_series(bars), pivots(bars)
    entries: list[FibEntry] = []
    for a, b in zip(pv, pv[1:], strict=False):
        leg, tol = abs(b.price - a.price), float(atr[b.confirmed_at] or 0.0)
        if a.kind == b.kind or tol <= 0 or leg < FIB_LEG_ATR * tol:
            continue
        direction = 1 if b.kind == "H" else -1
        # Le pivot n'est CONNU qu'à sa confirmation : un niveau déjà traversé d'ici là n'aurait pas pu être pris (écarté, sinon
        # on supposerait un remplissage au prix du niveau alors que le prix est déjà passé : regard vers le futur).
        seen = bars[b.index + 1 : b.confirmed_at + 1]
        if direction == 1:
            reached = min((x.low for x in seen), default=b.price)
            pending = {r for r in ratios if b.price - r * leg < reached}
        else:
            reached = max((x.high for x in seen), default=b.price)
            pending = {r for r in ratios if b.price + r * leg > reached}
        for i in range(b.confirmed_at + 1, min(b.confirmed_at + FIB_LIFETIME, len(bars) - 1) + 1):
            bar = bars[i]
            extreme_broken = bar.high > b.price if direction == 1 else bar.low < b.price
            wiped = bar.low <= a.price if direction == 1 else bar.high >= a.price
            if extreme_broken or wiped:
                break
            for r in sorted(pending):
                level = b.price - direction * r * leg
                if (bar.low <= level) if direction == 1 else (bar.high >= level):
                    entries.append(FibEntry(i, r, direction, level, tol))
                    pending.discard(r)
    return sorted(entries, key=lambda e: (e.index, e.ratio))


def race_outcome(bars: list[Bar], entry: FibEntry, max_bars: int = FIB_RACE_BARS) -> int:
    """+1 : le prix reprend le sens de la jambe de 1 ATR avant de reculer de 1 ATR ; -1 : l'inverse ; 0 : ni l'un ni l'autre.
    Les deux barrières dans la même bougie comptent comme un échec (pessimiste). Évalué à partir de la bougie SUIVANT le contact."""
    up = entry.price + entry.direction * FIB_RACE_ATR * entry.atr
    down = entry.price - entry.direction * FIB_RACE_ATR * entry.atr
    for bar in bars[entry.index + 1 : entry.index + 1 + max_bars]:
        hit_up = bar.high >= up if entry.direction == 1 else bar.low <= up
        hit_down = bar.low <= down if entry.direction == 1 else bar.high >= down
        if hit_down:
            return -1
        if hit_up:
            return 1
    return 0


# --- famille f : parabolique SAR (Wilder, pas 0,02, maximum 0,20) ---------------------------------------------------------
SAR_STEP = 0.02
SAR_MAX = 0.20


@dataclass(frozen=True)
class SarState:
    trend: int  # +1 haussier, -1 baissier (tendance en vigueur à la fin de la bougie)
    stop: float  # niveau du SAR en vigueur PENDANT la bougie (avant une éventuelle inversion)
    flipped: bool  # la tendance s'est inversée sur cette bougie


def sar_series(bars: list[Bar]) -> list[SarState | None]:
    """Parabolique SAR classique. Causal : l'état de la bougie i ne dépend que des bougies ≤ i. None pour les deux premières bougies."""
    out: list[SarState | None] = [None] * len(bars)
    if len(bars) < 3:
        return out
    up = bars[1].close >= bars[0].close
    sar = bars[0].low if up else bars[0].high
    ep = bars[1].high if up else bars[1].low
    af = SAR_STEP
    for i in range(2, len(bars)):
        b = bars[i]
        level = sar + af * (ep - sar)
        level = min(level, bars[i - 1].low, bars[i - 2].low) if up else max(level, bars[i - 1].high, bars[i - 2].high)
        if up and b.low < level:
            up, sar, ep, af, flipped = False, ep, b.low, SAR_STEP, True
        elif not up and b.high > level:
            up, sar, ep, af, flipped = True, ep, b.high, SAR_STEP, True
        else:
            flipped = False
            sar = level
            if (up and b.high > ep) or (not up and b.low < ep):
                ep = b.high if up else b.low
                af = min(af + SAR_STEP, SAR_MAX)
        out[i] = SarState(1 if up else -1, level, flipped)
    return out
