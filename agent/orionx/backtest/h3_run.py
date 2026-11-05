# ruff: noqa: E501  (lignes de rapport en français, longues par nature)
"""H-3 : exécution unique du développement (protocole §5, §9 étape 5).

Ordre : vérifier les empreintes du gel -> couper chaque série en développement / réserve (la réserve n'est JAMAIS lue ici) -> juger les six
familles sur les quatre symboles de décision -> publier la sortie complète, quelle qu'elle soit. Un marqueur empêche une seconde exécution :
relancer jusqu'à obtenir un résultat plus flatteur serait précisément ce que ce protocole interdit (principe n°10).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orionx.backtest.h3 import detect_candlesticks, detect_divergences, detect_figures
from orionx.backtest.h3_bench import (
    HORIZONS_CANDLES,
    HORIZONS_FIGURES,
    FamilyTable,
    SymbolData,
    build_symbol_data,
    build_table,
    family_max_test,
    fib_trades,
    judge_directional,
    judge_fibonacci,
    judge_plan,
    judge_squeeze,
    judge_squeeze_symbol,
    split_dev_reserve,
    variants_from_detector,
)
from orionx.backtest.h3_freeze import read_snapshot, snapshot_name, verify_manifest

DECISION_SYMBOLS = ("GBPUSD", "USDJPY", "AUDUSD", "USDCAD")
INFO_SYMBOLS = ("EURUSD",)
MARKER = "h3_dev_run.json"
PERMUTATIONS = 1000
PLAN_DRAWS = 200


class H3Refused(Exception):
    """Le banc refuse de s'exécuter (empreinte différente, fichier absent, exécution déjà faite)."""


def _load(data_dir: Path, symbols: tuple[str, ...], timeframe: str) -> tuple[dict[str, SymbolData], dict[str, int]]:
    dev: dict[str, SymbolData] = {}
    reserve_rows: dict[str, int] = {}
    for s in symbols:
        rows = read_snapshot(data_dir / snapshot_name(s, timeframe))
        d, r = split_dev_reserve(rows)
        dev[s] = build_symbol_data(s, d)
        reserve_rows[s] = len(r)
    return dev, reserve_rows


def _span(sds: dict[str, SymbolData]) -> tuple[int, int]:
    return min(sd.times[0] for sd in sds.values()), max(sd.times[-1] for sd in sds.values())


def run_development(
    data_dir: Path,
    manifest: dict[str, Any],
    *,
    permutations: int = PERMUTATIONS,
    plan_draws: int = PLAN_DRAWS,
    log: Callable[[str], None] = print,
) -> tuple[list[str], bool]:
    """Exécute le développement UNE fois. Retourne (lignes du rapport, au moins une famille a passé)."""
    problems = verify_manifest(manifest, data_dir)
    if problems:
        raise H3Refused("Données différentes du gel : " + " ; ".join(problems))
    marker = data_dir / MARKER
    if marker.exists():
        raise H3Refused(
            f"Le développement a déjà été exécuté ({marker.read_text(encoding='utf-8').strip()[:120]}). Une seule exécution (protocole §9)."
        )
    symbols = DECISION_SYMBOLS + INFO_SYMBOLS
    h1, reserve_h1 = _load(data_dir, symbols, "H1")
    d1, _ = _load(data_dir, DECISION_SYMBOLS, "D1")
    lines: list[str] = []

    def out(text: str = "") -> None:
        lines.append(text)
        log(text)

    out(
        f"H-3 développement - {datetime.now(UTC):%Y-%m-%d %H:%M} UTC - serveur {manifest.get('server')} - commission {manifest.get('commission_usd_per_lot_per_side')} USD/lot/côté"
    )
    out(f"Symboles de décision : {', '.join(DECISION_SYMBOLS)} ; information seulement : {', '.join(INFO_SYMBOLS)}")
    for s in symbols:
        out(
            f"  {s} H1 développement : {len(h1[s].bars)} bougies ({datetime.fromtimestamp(h1[s].times[0], UTC):%Y-%m-%d} -> {datetime.fromtimestamp(h1[s].times[-1], UTC):%Y-%m-%d}), réserve non lue : {reserve_h1[s]} bougies"
        )
    verdicts: dict[str, bool] = {}

    tables_cache: dict[tuple[int, ...], dict[str, FamilyTable]] = {}
    for name, detect, horizons in (
        ("a chandeliers", detect_candlesticks, HORIZONS_CANDLES),
        ("c figures", detect_figures, HORIZONS_FIGURES),
        ("d divergences RSI", detect_divergences, HORIZONS_FIGURES),
    ):
        out()
        out(f"== Famille {name} ==")
        tables = tables_cache.setdefault(tuple(horizons), {s: build_table(h1[s], horizons) for s in symbols})
        variants = variants_from_detector(h1, detect, horizons)
        v = judge_directional(name, variants, h1, tables, decision_symbols=DECISION_SYMBOLS, permutations=permutations)
        best = v.result.best
        out(f"  variantes retenues (>= 100 signaux) : {v.result.considered}")
        if best:
            out(
                f"  meilleure : {best.variant}, {best.signals} signaux, rendement net moyen {best.mean_net:+.4f} ATR, p = {v.result.p_value:.4f}"
            )
        if v.consistency:
            out(
                f"  blocs positifs {v.consistency.positive_blocks}/4 {tuple(None if m is None else round(m, 4) for m in v.consistency.block_means)} ; symboles positifs {v.consistency.positive_symbols}/4 { ({k: round(x, 4) for k, x in v.consistency.symbol_means.items()}) }"
            )
        info = family_max_test(
            {k: [r for r in refs if r.symbol in INFO_SYMBOLS] for k, refs in variants.items()},
            {s: tables[s] for s in INFO_SYMBOLS},
            permutations=permutations,
        )
        if info.best:
            out(
                f"  [information EURUSD, hors décision] meilleure {info.best.variant}, {info.best.signals} signaux, {info.best.mean_net:+.4f} ATR, p = {info.p_value:.4f}"
            )
        out(f"  critères : {v.checks}")
        out(f"  -> {'PASSE' if v.passed else 'ÉCHEC'}")
        verdicts[name] = v.passed

    dec_h1 = {s: h1[s] for s in DECISION_SYMBOLS}
    out()
    out("== Famille b Fibonacci contre niveaux témoins ==")
    trades = [t for sd in dec_h1.values() for t in fib_trades(sd)]
    start, end = _span(dec_h1)
    fv = judge_fibonacci(trades, start, end, permutations=permutations)
    out(
        f"  entrées Fibonacci {fv.fib_trades}, témoins {fv.witness_trades} ; rendement moyen Fibonacci {fv.fib_mean:+.4f}, témoins {fv.witness_mean:+.4f} ; p = {fv.p_value:.4f}"
    )
    out(f"  blocs positifs {fv.consistency.positive_blocks}/4 ; symboles positifs {fv.consistency.positive_symbols}/4")
    out(f"  critères : {fv.checks}")
    out(f"  -> {'PASSE' if fv.passed else 'ÉCHEC'}")
    verdicts["b fibonacci"] = fv.passed

    out()
    out("== Famille e étranglement de Bollinger (volatilité) ==")
    per = [judge_squeeze_symbol(sd, permutations=permutations) for sd in dec_h1.values()]
    for p in per:
        out(
            f"  {p.symbol}: {p.episodes} épisodes, rapport de volatilité moyen {p.mean_ratio:.3f}, p = {p.p_value:.4f} -> {'passe' if p.passed else 'échec'}"
        )
    ok, accepted = judge_squeeze(per)
    out(f"  symboles qui passent : {ok}/4 (3 requis) -> {'PASSE' if accepted else 'ÉCHEC'}")
    verdicts["e étranglement"] = accepted

    out()
    out("== Famille f plan de trading D1 (MMS 100, RSI, SAR) ==")
    pv = judge_plan(d1, *_span(d1), draws=plan_draws)
    out(
        f"  trades {pv.trades}, résultat net {pv.net_r:+.1f} R, profit factor {pv.profit_factor:.3f}, PF par bloc {tuple(round(p, 3) for p in pv.block_pf)}"
    )
    out(f"  drawdown maximal à 0,5 % par trade : {pv.drawdown_pct:.1f} % ; tirages aléatoires meilleurs : {pv.random_better}/{plan_draws}")
    out(f"  critères : {pv.checks}")
    out(f"  -> {'PASSE' if pv.passed else 'ÉCHEC'}")
    verdicts["f plan"] = pv.passed

    passed = [k for k, ok_ in verdicts.items() if ok_]
    out()
    out("== Décision (protocole §6) ==")
    if passed:
        out(f"  Familles passées : {passed}. La réserve peut être ouverte pour UNE famille (celle de p le plus faible).")
    else:
        out("  Aucune famille ne passe : NO-GO. La réserve reste FERMÉE.")
    marker.write_text(
        json.dumps({"run_at_utc": f"{datetime.now(UTC):%Y-%m-%d %H:%M:%S}", "passed": passed}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return lines, bool(passed)
