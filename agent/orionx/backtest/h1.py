"""Protocole H-1/V-3 (docs/validation/H-1_protocole.md) appliqué mécaniquement.

Le verdict n'est pas une opinion : il vient des critères figés du protocole. La réserve (183 derniers jours) n'est
jamais touchée par `run_dev`, et ne peut être ouverte qu'une fois (`open_holdout_once`).
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from orionx.analysis.filters import FILTERS
from orionx.analysis.state import session_of
from orionx.analysis.tzutil import server_to_utc_eet
from orionx.audit.log import AuditLog
from orionx.backtest.runner import BacktestParams, BacktestResult, Trade, run_backtest
from orionx.backtest.walkforward import RandomReference, random_reference, split_folds
from orionx.core.models import Bar, SymbolSpec
from orionx.strategies.trend_pullback import Strategy

HOLDOUT_DAYS = 183
N_BLOCKS = 4
RANDOM_RUNS = 40
COSTS: BacktestParams = {"spread_points": 10.0, "commission_per_lot": 7.0}
NAMES = ("none", "vol", "session", "trend", "all")  # « none » est la référence, pas une hypothèse
MIN_DEV_TRADES, MIN_HOLDOUT_TRADES = 100, 20
PF_MIN, DD_MAX, BEAT_RANDOM_MIN, BLOCKS_REQUIRED = 1.2, 10.0, 0.95, 3


# --- découpage et instantané ------------------------------------------------------------------
def split_holdout(bars: Sequence[Bar], days: int = HOLDOUT_DAYS) -> tuple[list[Bar], list[Bar]]:
    """(développement, réserve) : la réserve couvre les `days` derniers jours avant la dernière bougie."""
    cutoff = bars[-1].time - timedelta(days=days)
    return [b for b in bars if b.time < cutoff], [b for b in bars if b.time >= cutoff]


def save_snapshot(path: Path, bars: Sequence[Bar]) -> None:
    """Instantané des données : sans lui, la fenêtre glisse d'une bougie par heure et les résultats ne se reproduisent pas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b.time.isoformat(), repr(b.open), repr(b.high), repr(b.low), repr(b.close), repr(b.volume)])


def load_snapshot(path: Path) -> list[Bar]:
    with open(path, newline="", encoding="utf-8") as f:
        return [Bar(datetime.fromisoformat(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                    float(r["volume"])) for r in csv.DictReader(f)]  # fmt: skip


# --- critères -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class Criterion:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Verdict:
    criteria: tuple[Criterion, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.criteria)


@dataclass(frozen=True)
class FilterReport:
    name: str
    total: BacktestResult
    folds: list[BacktestResult]
    ref: RandomReference
    verdict: Verdict


def judge_dev(total: BacktestResult, folds: Sequence[BacktestResult], ref: RandomReference) -> Verdict:
    """§5 du protocole : TOUS les critères sont requis, aucune tolérance."""
    good = sum(f.profit_factor > PF_MIN for f in folds)
    return Verdict((
        Criterion("trades", total.trades >= MIN_DEV_TRADES, f"{total.trades} trades (>= {MIN_DEV_TRADES})"),
        Criterion("blocs", len(folds) == N_BLOCKS and good >= BLOCKS_REQUIRED,
                  f"PF > {PF_MIN} dans {good} bloc(s) sur {len(folds)} (>= {BLOCKS_REQUIRED} sur {N_BLOCKS})"),
        Criterion("drawdown", total.max_drawdown_pct < DD_MAX, f"{total.max_drawdown_pct:.1f} % (< {DD_MAX} %)"),
        Criterion("hasard", ref.runs >= RANDOM_RUNS and ref.share_worse >= BEAT_RANDOM_MIN,
                  f"meilleur que {ref.share_worse:.0%} de {ref.runs} tirages (>= {BEAT_RANDOM_MIN:.0%} de {RANDOM_RUNS})"),
    ))  # fmt: skip


def judge_holdout(r: BacktestResult) -> Verdict:
    """§6 : moins de 20 trades = non concluant = pas de validation."""
    return Verdict((
        Criterion("trades", r.trades >= MIN_HOLDOUT_TRADES, f"{r.trades} trades (>= {MIN_HOLDOUT_TRADES}, sinon non concluant)"),
        Criterion("pf", r.profit_factor > PF_MIN, f"PF {r.profit_factor:.2f} (> {PF_MIN})"),
        Criterion("net", r.net_profit > 0, f"net {r.net_profit:+.0f}"),
        Criterion("drawdown", r.max_drawdown_pct < DD_MAX, f"{r.max_drawdown_pct:.1f} % (< {DD_MAX} %)"),
    ))  # fmt: skip


def pick_candidate(reports: Sequence[FilterReport]) -> FilterReport | None:
    """§6 : parmi les hypothèses qui passent, celle qui bat le plus de tirages (puis le plus haut PF). `none` est exclu."""
    ok = [r for r in reports if r.name != "none" and r.verdict.passed]
    return max(ok, key=lambda r: (r.ref.share_worse, r.total.profit_factor), default=None)


def costs_for(name: str) -> BacktestParams:
    return {**COSTS, "entry_filter": FILTERS[name]}


# --- exécution -----------------------------------------------------------------------------------
def run_dev(bars: Sequence[Bar], spec: SymbolSpec, symbol: str, make_strategy: Callable[[], Strategy], *,
            random_runs: int = RANDOM_RUNS, names: Sequence[str] = NAMES) -> list[FilterReport]:  # fmt: skip
    """Les 5 configurations sur les données de DÉVELOPPEMENT uniquement (l'appelant a déjà retiré la réserve)."""
    reports = []
    for name in names:
        costs = costs_for(name)
        total = run_backtest(bars, make_strategy(), spec, symbol, **costs)
        folds = [run_backtest(part, make_strategy(), spec, symbol, **costs) for part in split_folds(bars, N_BLOCKS)]
        ref = random_reference(bars, spec, symbol, total.net_profit, runs=random_runs, **costs)
        reports.append(FilterReport(name, total, folds, ref, judge_dev(total, folds, ref)))
    return reports


def run_holdout(bars: Sequence[Bar], spec: SymbolSpec, symbol: str, make_strategy: Callable[[], Strategy],
                name: str) -> tuple[BacktestResult, Verdict]:  # fmt: skip
    r = run_backtest(bars, make_strategy(), spec, symbol, **costs_for(name))
    return r, judge_holdout(r)


class HoldoutAlreadyOpened(Exception):
    pass


def open_holdout_once(marker: Path, name: str, audit: AuditLog | None = None) -> None:
    """Écrit le marqueur AVANT de lire la réserve (un plantage ne rouvre rien) et le consigne dans le journal d'audit."""
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(marker, "x", encoding="utf-8") as f:
            json.dump({"filtre": name, "ouvert_le": datetime.now().astimezone().isoformat()}, f)
    except FileExistsError:
        raise HoldoutAlreadyOpened(f"La réserve a déjà été ouverte ({marker}) : {marker.read_text(encoding='utf-8')}") from None
    if audit:
        audit.append("HoldoutOpened", {"protocole": "H-1", "filtre": name})


# --- analyse des trades perdants (descriptive, jamais pour régler un paramètre) -----------------
@dataclass
class GroupStat:
    trades: int = 0
    losers: int = 0
    net: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.trades - self.losers) / self.trades if self.trades else 0.0


DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def loser_breakdown(log: Sequence[Trade]) -> dict[str, dict[str, GroupStat]]:
    out: dict[str, dict[str, GroupStat]] = {k: defaultdict(GroupStat) for k in ("sens", "séance", "jour", "motif de sortie")}
    for t in log:
        utc = server_to_utc_eet(datetime.fromisoformat(t.opened))  # l'ouverture est en heure SERVEUR
        keys = {"sens": t.side, "séance": session_of(utc).value, "jour": DAYS[utc.weekday()], "motif de sortie": t.reason}
        for dim, key in keys.items():
            g = out[dim][key]
            g.trades += 1
            g.losers += t.profit <= 0
            g.net += t.profit
    return out
