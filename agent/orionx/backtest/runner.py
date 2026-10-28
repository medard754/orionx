"""Backtest événementiel sur bougies (T-12). Une seule position à la fois, avec coûts.

Hypothèses prudentes : entrée à l'OUVERTURE de la bougie suivante (+ spread à l'achat), si stop et objectif sont
atteints dans la même bougie c'est le STOP qui compte, gaps exécutés au pire prix. La stratégie ne voit jamais
une bougie future. Les résultats sont INDICATIFS : un bon backtest ne garantit rien.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from orionx.analysis.filters import EntryFilter
from orionx.analysis.state import analyze
from orionx.analysis.tzutil import server_to_utc_eet
from orionx.core.config import AnalysisConfig
from orionx.core.models import Bar, Side, SymbolSpec, Tick
from orionx.risk.sizing import position_size
from orionx.strategies.trend_pullback import Strategy


@dataclass(frozen=True)
class Trade:
    opened: str
    side: str
    entry: float
    exit: float
    volume: float
    profit: float
    reason: str


@dataclass
class BacktestResult:
    trades: int = 0
    wins: int = 0
    net_profit: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    final_equity: float = 0.0
    filtered: int = 0  # signaux écartés par le filtre d'entrée
    log: list[Trade] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0


def run_backtest(
    bars: Sequence[Bar],
    strategy: Strategy,
    spec: SymbolSpec,
    symbol: str = "EURUSD",
    *,
    balance: float = 10_000.0,
    risk_pct: float = 0.5,
    spread_points: float = 10.0,
    commission_per_lot: float = 0.0,
    min_rr: float = 1.5,
    entry_filter: EntryFilter | None = None,
    analysis: AnalysisConfig | None = None,
) -> BacktestResult:
    spread = spread_points * spec.point
    acfg = analysis or AnalysisConfig()
    state_window = acfg.vol_window + 60
    res, equity, peak, gross_win, gross_loss = BacktestResult(final_equity=balance), balance, balance, 0.0, 0.0
    i, window = strategy.min_bars, strategy.min_bars + 5  # même fenêtre qu'en production (agent.py)
    while i < len(bars) - 1:
        mid = bars[i].close
        tick = Tick(symbol, mid, mid + spread, bars[i].time)
        prop = strategy.generate(symbol, bars[max(0, i + 1 - window) : i + 1], tick, bars[i].time, set())  # jamais de bougie future
        if prop is None:
            i += 1
            continue
        if entry_filter is not None:  # état du marché calculé UNIQUEMENT avec les bougies déjà fermées
            state = analyze(symbol, bars[max(0, i + 1 - state_window) : i + 1], acfg, server_to_utc_eet)
            if not entry_filter(state, prop):
                res.filtered += 1
                i += 1
                continue
        buy = prop.side is Side.BUY
        entry = bars[i + 1].open + (spread if buy else 0.0)
        sl, tp = prop.sl, prop.tp
        ok = (sl < entry < tp) if buy else (tp < entry < sl)
        if not ok or abs(tp - entry) / abs(entry - sl) < min_rr:
            i += 1
            continue
        volume = position_size(equity, risk_pct, entry, sl, spec)
        if volume <= 0:
            i += 1
            continue
        exit_price, reason, j = bars[-1].close + (0.0 if buy else spread), "fin des données", len(bars) - 1
        for j in range(i + 1, len(bars)):  # noqa: B007
            b = bars[j]
            if buy:
                if b.low <= sl:
                    exit_price, reason = min(sl, b.open), "stop"
                    break
                if b.high >= tp:
                    exit_price, reason = tp, "objectif"
                    break
            else:
                if b.high + spread >= sl:
                    exit_price, reason = max(sl, b.open + spread), "stop"
                    break
                if b.low + spread <= tp:
                    exit_price, reason = tp, "objectif"
                    break
        move = (exit_price - entry) if buy else (entry - exit_price)
        profit = move / spec.tick_size * spec.tick_value * volume - commission_per_lot * volume
        equity += profit
        peak = max(peak, equity)
        res.max_drawdown_pct = max(res.max_drawdown_pct, (peak - equity) / peak * 100)
        res.trades += 1
        res.wins += profit > 0
        gross_win, gross_loss = gross_win + max(profit, 0.0), gross_loss + max(-profit, 0.0)
        res.log.append(Trade(bars[i].time.isoformat(), prop.side.value, entry, exit_price, volume, profit, reason))
        i = j + 1
    res.net_profit, res.final_equity = equity - balance, equity
    res.profit_factor = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    return res
