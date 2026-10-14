"""Découpage en périodes successives et comparaison au hasard (T-12 : validation honnête)."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from orionx.backtest.runner import BacktestResult, run_backtest
from orionx.core.models import Bar, SymbolSpec
from orionx.strategies.baseline import RandomEntry


def split_folds(bars: Sequence[Bar], n: int) -> list[list[Bar]]:
    """n blocs consécutifs, sans chevauchement. Le dernier absorbe le reste."""
    if n < 1:
        raise ValueError("n doit être >= 1")
    size = len(bars) // n
    folds = [list(bars[i * size : (i + 1) * size]) for i in range(n)]
    folds[-1] += list(bars[n * size :])
    return folds


@dataclass(frozen=True)
class RandomReference:
    runs: int
    mean_net: float
    std_net: float
    mean_pf: float
    share_worse: float  # part des tirages aléatoires qui font PIRE que la stratégie


def random_reference(bars: Sequence[Bar], spec: SymbolSpec, symbol: str, strategy_net: float, runs: int = 20,
                     **bt_kwargs: float) -> RandomReference:  # fmt: skip
    results: list[BacktestResult] = [
        run_backtest(bars, RandomEntry(seed=s), spec, symbol, **bt_kwargs) for s in range(runs)
    ]
    nets = [r.net_profit for r in results]
    pfs = [r.profit_factor for r in results if r.profit_factor != float("inf")]
    return RandomReference(
        runs=runs,
        mean_net=statistics.fmean(nets),
        std_net=statistics.pstdev(nets),
        mean_pf=statistics.fmean(pfs) if pfs else 0.0,
        share_worse=sum(n < strategy_net for n in nets) / runs,
    )
