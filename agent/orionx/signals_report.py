"""Rejoue les signaux passés de l'agent sur les bougies qui ont suivi : « que serait-il arrivé ? » (essai V-4).

Aucun ordre, aucune écriture : on lit le journal d'audit et les bougies du terminal. C'est le seul test hors historique
dont on dispose : les signaux sont émis EN DIRECT, avant que le marché ne bouge, donc sans biais de sélection.

Règles posées AVANT de regarder les résultats (principe n°10) :
- entrée au prix de référence enregistré avec le signal (achat : ask, vente : bid) ;
- sortie au premier contact du stop ou de l'objectif sur les bougies SUIVANT la bougie d'ancrage ;
- même bougie qui touche les deux : le stop est compté (hypothèse pessimiste, signalée) ;
- vente : les bougies sont en prix bid, la sortie se paie à l'ask (bid + spread du signal) ;
- coût supplémentaire par trade (`cost_pips`, 1 pip par défaut : le démo MetaQuotes n'a ni spread ni commission) ;
- au-delà de `max_bars` bougies sans résultat : « non résolu », jamais compté comme un gain.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from orionx.core.models import Bar, Side

MIN_SIGNALS = 30  # en dessous : aucune conclusion (un échantillon trop petit ne prouve rien)
PIP = 0.0001  # EURUSD ; les autres paires à 5 décimales (JPY exclu : passer `pip` si besoin)


@dataclass(frozen=True)
class Signal:
    id: str
    symbol: str
    side: Side
    sl: float
    tp: float
    strategy: str
    timeframe: str
    anchor_bar: datetime
    entry: float
    spread: float
    ts: str  # horodatage du journal (heure locale UTC)
    status: str  # "approved" | "rejected:<raisons>" | "pending" : ce que l'agent en a fait


@dataclass(frozen=True)
class Outcome:
    signal: Signal
    result: str  # "tp" | "sl" | "open" | "no_data"
    r: float | None  # gain en multiples du risque, coût inclus ; None si non résolu
    ambiguous: bool = False  # la bougie de sortie touchait aussi l'autre niveau
    bars_used: int = 0


def load_signals(entries: list[dict[str, Any]]) -> list[Signal]:
    """Reconstitue les signaux à partir des entrées du journal (TradeProposed + SignalContext + TradeRejected)."""
    contexts = {e["payload"]["proposal_id"]: e["payload"] for e in entries if e["kind"] == "SignalContext"}
    rejected = {e["payload"]["proposal_id"]: e["payload"].get("reasons", []) for e in entries if e["kind"] == "TradeRejected"}
    approved = {e["payload"]["order"]["proposal_id"] for e in entries if e["kind"] == "TradeApproved"}
    signals = []
    for e in entries:
        if e["kind"] != "TradeProposed":
            continue
        p = e["payload"]["proposal"]
        ctx = contexts.get(p["id"])
        if ctx is None:
            continue  # signal d'avant l'enregistrement du contexte : impossible à rejouer
        status = "rejected:" + ",".join(rejected[p["id"]]) if p["id"] in rejected else "approved" if p["id"] in approved else "pending"
        signals.append(
            Signal(
                p["id"], p["symbol"], Side(p["side"]), float(p["sl"]), float(p["tp"]), p["strategy"], ctx["timeframe"],
                datetime.fromisoformat(ctx["anchor_bar"]), float(ctx["entry"]), float(ctx["spread"]), e["ts"], status,
            )  # fmt: skip
        )
    return signals


def evaluate(signal: Signal, bars: list[Bar], *, cost_pips: float = 1.0, max_bars: int = 48, pip: float = PIP) -> Outcome:
    """Premier contact stop / objectif sur les bougies qui suivent la bougie d'ancrage."""
    after = [b for b in bars if b.time > signal.anchor_bar]
    if not after:
        return Outcome(signal, "no_data", None)
    buy = signal.side is Side.BUY
    risk = abs(signal.entry - signal.sl)
    reward = abs(signal.tp - signal.entry)
    if risk <= 0:
        return Outcome(signal, "no_data", None)
    cost_r = cost_pips * pip / risk
    for n, b in enumerate(after[:max_bars], start=1):
        if buy:  # on sort en vendant au bid : les bougies sont en bid
            hit_sl, hit_tp = b.low <= signal.sl, b.high >= signal.tp
        else:  # on sort en rachetant à l'ask = bid + spread
            hit_sl, hit_tp = b.high + signal.spread >= signal.sl, b.low + signal.spread <= signal.tp
        if hit_sl:  # stop prioritaire, y compris quand les deux sont touchés dans la même bougie
            return Outcome(signal, "sl", -1.0 - cost_r, ambiguous=hit_tp, bars_used=n)
        if hit_tp:
            return Outcome(signal, "tp", reward / risk - cost_r, bars_used=n)
    return Outcome(signal, "open", None, bars_used=min(len(after), max_bars))


@dataclass(frozen=True)
class Summary:
    signals: int
    resolved: int
    wins: int
    unresolved: int
    ambiguous: int
    win_rate: float | None
    mean_r: float | None
    mean_r_error: float | None  # ± 1,96 erreur-type : l'incertitude de la moyenne
    profit_factor: float | None
    conclusive: bool  # assez de signaux résolus pour que les chiffres aient un sens


def summarize(outcomes: list[Outcome]) -> Summary:
    done = [o for o in outcomes if o.r is not None]
    rs = [float(o.r) for o in done if o.r is not None]
    wins = sum(1 for o in done if o.result == "tp")
    gains, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    mean = sum(rs) / len(rs) if rs else None
    err = None
    if mean is not None and len(rs) > 1:
        var = sum((r - mean) ** 2 for r in rs) / (len(rs) - 1)
        err = 1.96 * math.sqrt(var / len(rs))
    return Summary(
        signals=len(outcomes), resolved=len(done), wins=wins, unresolved=len(outcomes) - len(done),
        ambiguous=sum(1 for o in done if o.ambiguous), win_rate=wins / len(done) if done else None, mean_r=mean,
        mean_r_error=err, profit_factor=(gains / losses if losses > 0 else None) if done else None,
        conclusive=len(done) >= MIN_SIGNALS,
    )  # fmt: skip


def render(outcomes: list[Outcome], *, cost_pips: float, max_bars: int, out: Callable[[str], None] = print) -> None:
    s = summarize(outcomes)
    out(f"Signaux rejouables : {s.signals} · résolus : {s.resolved} · non résolus : {s.unresolved} "
        f"(coût {cost_pips:g} pip/trade, horizon {max_bars} bougies)")  # fmt: skip
    by_status: dict[str, int] = defaultdict(int)
    for o in outcomes:
        by_status[o.signal.status.split(":")[0]] += 1
    out("Ce que l'agent en a fait : " + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items())))
    if s.resolved:
        pf = f"{s.profit_factor:.2f}" if s.profit_factor is not None else "n/a"
        margin = f" (± {s.mean_r_error:.2f})" if s.mean_r_error is not None else ""
        out(f"Réussite : {s.win_rate:.0%} ({s.wins}/{s.resolved}) · gain moyen : {s.mean_r:+.2f} R{margin} · facteur de profit : {pf}")
        if s.ambiguous:
            out(f"  dont {s.ambiguous} sortie(s) ambiguë(s) (stop ET objectif dans la même bougie : comptées perdantes)")
    by_strategy: dict[str, list[Outcome]] = defaultdict(list)
    for o in outcomes:
        by_strategy[o.signal.strategy].append(o)
    if len(by_strategy) > 1:
        for name, group in sorted(by_strategy.items()):
            g = summarize(group)
            out(f"  {name} : {g.resolved} résolus, gain moyen {g.mean_r:+.2f} R" if g.mean_r is not None else f"  {name} : aucun résolu")
    if not s.conclusive:
        out(f"CONCLUSION : aucune. Il faut au moins {MIN_SIGNALS} signaux résolus ; en dessous, la chance explique tout.")
    elif s.mean_r is not None and s.mean_r_error is not None and s.mean_r - s.mean_r_error <= 0:
        out("CONCLUSION : le gain moyen n'est pas distinct de zéro (son intervalle contient 0) : aucun avantage démontré.")
    else:
        out("CONCLUSION : gain moyen positif et distinct de zéro sur cet échantillon. Ce n'est PAS encore une preuve : "
            "refaire sur une autre période, avec de vrais spreads, avant tout usage réel.")  # fmt: skip
    out("Limites : bougies du terminal (prix bid), pas d'exécution réelle ; le démo MetaQuotes n'a ni spread ni commission.")


async def run_report(gw: Any, entries: list[dict[str, Any]], *, cost_pips: float = 1.0, max_bars: int = 48,
                     out: Callable[[str], None] = print) -> int:  # fmt: skip
    signals = load_signals(entries)
    if not signals:
        out("Aucun signal rejouable dans le journal (il faut des signaux enregistrés avec leur contexte : lancer `run`).")
        return 2
    outcomes: list[Outcome] = []
    bars_cache: dict[tuple[str, str], list[Bar]] = {}
    for sig in signals:
        key = (sig.symbol, sig.timeframe)
        if key not in bars_cache:
            bars_cache[key] = await gw.get_bars(sig.symbol, sig.timeframe, 5000)
        outcomes.append(evaluate(sig, bars_cache[key], cost_pips=cost_pips, max_bars=max_bars))
    render(outcomes, cost_pips=cost_pips, max_bars=max_bars, out=out)
    return 0
