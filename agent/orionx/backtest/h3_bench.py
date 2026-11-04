"""H-3 : banc d'essai (protocole §3 et §5) : rendements nets de coûts, dates aléatoires appariées, test du maximum d'une famille.

Aucune lecture du courtier ici : des lignes de bougies gelées (`h3_freeze`) en entrée, des statistiques en sortie. Rien n'est ajusté.

Conventions (figées avant toute mesure) :
- le signal est connu à la clôture de la bougie i ; l'entrée se fait à l'ouverture de la bougie i+1 ; la sortie à la clôture de la bougie
  i+horizon ;
- les bougies sont en prix bid ; un achat paie le spread de la bougie d'entrée ; une vente paie le spread de la bougie de sortie
  (rachat à l'ask) ;
- la commission (7 USD par lot et par aller-retour) est convertie en unités de prix de la paire ;
- les rendements sont exprimés en ATR(14) du signal, pour comparer des paires différentes.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from orionx.backtest.h3 import atr_series
from orionx.backtest.h3_freeze import Row
from orionx.core.models import Bar

COMMISSION_ROUND_TURN_USD = 7.0  # 3,50 USD par lot et par côté, mesuré le 4 nov. 2026 (protocole §3)
LOT_UNITS = 100_000
TERCILES = 3
HORIZONS_CANDLES = (4, 12, 24)
HORIZONS_FIGURES = (12, 24)


def point_size(symbol: str) -> float:
    """Taille du point : 0,001 pour les paires en yen, 0,00001 pour les autres (cotation à 3 et 5 décimales)."""
    return 0.001 if "JPY" in symbol else 0.00001


def commission_in_price(symbol: str, price: float) -> float:
    """Commission d'un aller-retour, exprimée en unités de prix de la paire.
    Paire cotée en USD : 7 USD / 100 000 unités ; sinon (USDJPY, USDCAD) il faut la convertir dans la devise de cotation."""
    base = COMMISSION_ROUND_TURN_USD / LOT_UNITS
    return base if symbol.endswith("USD") else base * price


@dataclass(frozen=True)
class SymbolData:
    symbol: str
    bars: list[Bar]
    times: list[int]  # heures serveur telles que livrées
    spreads: list[int]  # en points
    atr: list[float | None]
    hour: list[int]  # heure serveur (0-23) : sert à apparier les dates aléatoires
    tercile: list[int]  # classe d'ATR (0, 1, 2) mesurée sur toute la série fournie (le développement)


def build_symbol_data(symbol: str, rows: Sequence[Row]) -> SymbolData:
    bars = [Bar(datetime.fromtimestamp(r[0], UTC), r[1], r[2], r[3], r[4], float(r[5])) for r in rows]
    atr = atr_series(bars)
    values = sorted(a for a in atr if a is not None)
    cuts = [values[int(len(values) * k / TERCILES)] for k in range(1, TERCILES)] if values else []
    tercile = [0 if a is None else sum(a > c for c in cuts) for a in atr]
    return SymbolData(symbol, bars, [r[0] for r in rows], [r[6] for r in rows], atr, [b.time.hour for b in bars], tercile)


def forward_net(sd: SymbolData, i: int, direction: int, horizon: int) -> float | None:
    """Rendement net d'un trade pris sur un signal clos à la bougie i : en ATR(i), coûts déduits ; None si les données manquent."""
    j, k = i + 1, i + horizon
    atr = sd.atr[i]
    if k >= len(sd.bars) or atr is None or atr <= 0 or direction == 0:
        return None
    point = point_size(sd.symbol)
    entry, exit_ = sd.bars[j].open, sd.bars[k].close
    pnl = exit_ - entry - sd.spreads[j] * point if direction == 1 else entry - exit_ - sd.spreads[k] * point
    return (pnl - commission_in_price(sd.symbol, entry)) / atr


@dataclass(frozen=True)
class SignalRef:
    symbol: str
    index: int
    direction: int
    stratum: tuple[int, int]  # (heure, classe d'ATR) de la bougie du signal : sert à tirer des dates aléatoires comparables


def make_ref(sd: SymbolData, index: int, direction: int) -> SignalRef:
    return SignalRef(sd.symbol, index, direction, (sd.hour[index], sd.tercile[index]))


Variant = tuple[str, int]  # (nom de la règle, horizon en bougies)


@dataclass(frozen=True)
class FamilyTable:
    """Rendements nets précalculés d'un symbole : (sens, horizon) -> liste par bougie ; bougies appariables par (heure, classe d'ATR)."""

    net: dict[tuple[int, int], list[float | None]]
    strata: dict[tuple[int, int], list[int]]


def build_table(sd: SymbolData, horizons: Sequence[int]) -> FamilyTable:
    net = {(d, h): [forward_net(sd, i, d, h) for i in range(len(sd.bars))] for d in (1, -1) for h in horizons}
    longest = max(horizons)
    strata: dict[tuple[int, int], list[int]] = {}
    for i in range(len(sd.bars) - longest - 1):
        if sd.atr[i] is not None:
            strata.setdefault((sd.hour[i], sd.tercile[i]), []).append(i)
    return FamilyTable(net, strata)


@dataclass(frozen=True)
class VariantResult:
    variant: Variant
    signals: int
    mean_net: float


@dataclass(frozen=True)
class FamilyResult:
    best: VariantResult | None
    p_value: float  # part des tirages aléatoires dont le maximum atteint ou dépasse celui observé (avec correction +1)
    permutations: int
    considered: int  # variantes retenues (au moins `min_signals` signaux)


def _observed(
    variants: dict[Variant, list[SignalRef]], tables: dict[str, FamilyTable], min_signals: int
) -> dict[Variant, tuple[list[SignalRef], list[float]]]:
    kept: dict[Variant, tuple[list[SignalRef], list[float]]] = {}
    for variant, refs in variants.items():
        usable, values = [], []
        for ref in refs:
            v = tables[ref.symbol].net[(ref.direction, variant[1])][ref.index]
            if v is not None:
                usable.append(ref)
                values.append(v)
        if len(usable) >= min_signals:
            kept[variant] = (usable, values)
    return kept


def family_max_test(
    variants: dict[Variant, list[SignalRef]],
    tables: dict[str, FamilyTable],
    *,
    permutations: int = 1000,
    min_signals: int = 100,
    seed: int = 0,
) -> FamilyResult:
    """Test du maximum : le meilleur rendement net moyen de la famille est-il meilleur que celui des dates aléatoires ?
    Chaque tirage remplace chaque signal par une bougie du MÊME symbole, de la MÊME heure et de la MÊME classe d'ATR, avec le même sens,
    puis garde le maximum de la famille : la comparaison est donc équitable malgré le nombre de variantes."""
    kept = _observed(variants, tables, min_signals)
    if not kept:
        return FamilyResult(None, 1.0, permutations, 0)
    stats = {v: VariantResult(v, len(vals), sum(vals) / len(vals)) for v, (_refs, vals) in kept.items()}
    best = max(stats.values(), key=lambda s: s.mean_net)
    plan = []  # par variante : pour chaque signal, (bougies appariables, rendements du sens et de l'horizon)
    for v, (refs, _vals) in kept.items():
        plan.append([(tables[r.symbol].strata.get(r.stratum, []), tables[r.symbol].net[(r.direction, v[1])]) for r in refs])
    beaten = 0
    for k in range(permutations):
        rng = random.Random(seed * 1_000_003 + k)
        top = float("-inf")
        for variant_plan in plan:
            total, n = 0.0, 0
            for pool, net in variant_plan:
                if not pool:
                    continue
                value = net[pool[rng.randrange(len(pool))]]
                if value is not None:
                    total += value
                    n += 1
            if n:
                top = max(top, total / n)
        if top >= best.mean_net:
            beaten += 1
    return FamilyResult(best, (beaten + 1) / (permutations + 1), permutations, len(kept))
