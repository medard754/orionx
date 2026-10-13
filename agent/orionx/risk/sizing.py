"""Calcul de taille de position. Arrondi TOUJOURS vers le bas : le risque réel ≤ risque voulu."""

from __future__ import annotations

import math

from orionx.core.models import SymbolSpec


def risk_per_lot(spec: SymbolSpec, entry: float, sl: float) -> float:
    """Perte (devise du compte) pour 1 lot si le stop-loss est touché."""
    return abs(entry - sl) / spec.tick_size * spec.tick_value


def position_size(capital: float, risk_pct: float, entry: float, sl: float, spec: SymbolSpec) -> float:
    """Volume en lots, arrondi au pas inférieur. Retourne 0.0 si inférieur au volume minimum."""
    per_lot = risk_per_lot(spec, entry, sl)
    if capital <= 0 or risk_pct <= 0 or per_lot <= 0:
        return 0.0
    raw = capital * risk_pct / 100.0 / per_lot
    steps = math.floor(raw / spec.volume_step + 1e-9)
    volume = round(steps * spec.volume_step, 8)
    volume = min(volume, spec.volume_max)
    return volume if volume >= spec.volume_min else 0.0
