"""H-3 : gel des données (protocole §2 et §9, étape 3).

Lit l'historique de bougies du courtier, l'écrit dans des fichiers CSV et enregistre leurs empreintes sha256 dans un manifeste commité.
Les données elles-mêmes ne sont pas versionnées (`agent/data/` est ignoré) ; le manifeste suffit à prouver qu'on n'a pas changé de données :
`verify_manifest` signale toute empreinte qui diffère, et le banc d'essai refuse alors de s'exécuter.

Les heures sont celles du SERVEUR du courtier, telles que livrées (principe n°8 : jamais comparées à l'horloge locale).
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COLUMNS = ("time", "open", "high", "low", "close", "tick_volume", "spread")
Row = tuple[int, float, float, float, float, int, int]
Fetcher = Callable[[str, str, int], list[Row]]  # (symbole, unité de temps, année) -> bougies de cette année, anciennes d'abord
DEFAULT_SYMBOLS = ("GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURUSD")
DEFAULT_TIMEFRAMES = ("H1", "D1")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_name(symbol: str, timeframe: str) -> str:
    return f"h3_{symbol}_{timeframe}.csv"


def write_snapshot(path: Path, rows: Sequence[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(COLUMNS) + "\n")
        for r in rows:
            f.write(",".join(repr(x) for x in r) + "\n")


def read_snapshot(path: Path) -> list[Row]:
    rows: list[Row] = []
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            t, o, h, lo, c, v, s = line.rstrip("\n").split(",")
            rows.append((int(t), float(o), float(h), float(lo), float(c), int(v), int(s)))
    return rows


def year_report(rows: Sequence[Row]) -> tuple[dict[int, int], list[int]]:
    """Bougies par année (heure serveur) et années INTÉRIEURES suspectes : moins de 80 % de la médiane des années intérieures.
    Une année manquante au milieu d'un historique est presque toujours une lecture ratée, pas un trou du marché."""
    counts: dict[int, int] = {}
    for r in rows:
        year = datetime.fromtimestamp(r[0], UTC).year
        counts[year] = counts.get(year, 0) + 1
    all_years = range(min(counts), max(counts) + 1) if counts else range(0)
    inner_counts = sorted(counts.get(y, 0) for y in all_years if min(counts) < y < max(counts))
    median = inner_counts[len(inner_counts) // 2] if inner_counts else 0
    suspects = [y for y in all_years if min(counts) < y < max(counts) and counts.get(y, 0) < 0.8 * median]
    return counts, suspects


def freeze(
    fetch: Fetcher,
    out_dir: Path,
    *,
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    first_year: int = 2010,
    last_year: int | None = None,
    server: str = "",
    commission_per_lot_per_side: float | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Télécharge, écrit et empreint. La DERNIÈRE bougie de chaque série est écartée : elle est encore en formation."""
    frozen_at = clock()
    last_year = last_year or frozen_at.year
    files: list[dict[str, Any]] = []
    for symbol in symbols:
        for tf in timeframes:
            by_time: dict[int, Row] = {}
            for year in range(first_year, last_year + 1):
                got = fetch(symbol, tf, year)
                log(f"{symbol} {tf} {year} : {len(got)} bougies")
                for r in got:
                    by_time[r[0]] = r
            rows = [by_time[t] for t in sorted(by_time)][:-1]  # écarte la bougie en cours
            if not rows:
                log(f"{symbol} {tf} : AUCUNE donnée, série absente du gel")
                continue
            name = snapshot_name(symbol, tf)
            write_snapshot(out_dir / name, rows)
            files.append({
                "file": name, "symbol": symbol, "timeframe": tf, "rows": len(rows), "sha256": _sha256(out_dir / name),
                "year_counts": year_report(rows)[0], "suspect_years": year_report(rows)[1],
                "first_bar": datetime.fromtimestamp(rows[0][0], UTC).strftime("%Y-%m-%d %H:%M"),
                "last_bar": datetime.fromtimestamp(rows[-1][0], UTC).strftime("%Y-%m-%d %H:%M"),
            })  # fmt: skip
            log(f"-> {name} : {len(rows)} bougies, {files[-1]['first_bar']} -> {files[-1]['last_bar']}, sha256 {files[-1]['sha256'][:16]}")
            if files[-1]["suspect_years"]:
                log(f"   ATTENTION : années suspectes (trop peu de bougies) : {files[-1]['suspect_years']}")
    return {
        "protocol": "H-3", "frozen_at_utc": frozen_at.strftime("%Y-%m-%d %H:%M:%S"), "server": server,
        "commission_usd_per_lot_per_side": commission_per_lot_per_side, "columns": list(COLUMNS),
        "time_note": "heure SERVEUR du courtier, telle que livrée ; jamais comparée à l'horloge locale",
        "files": files,
    }  # fmt: skip


def verify_manifest(manifest: dict[str, Any], data_dir: Path) -> list[str]:
    """Liste des problèmes (fichier absent, empreinte différente, nombre de lignes différent) ; vide = données identiques au gel."""
    problems: list[str] = []
    for entry in manifest.get("files", []):
        path = data_dir / entry["file"]
        if not path.is_file():
            problems.append(f"{entry['file']} : fichier absent")
        elif _sha256(path) != entry["sha256"]:
            problems.append(f"{entry['file']} : empreinte différente (données modifiées depuis le gel)")
        elif len(read_snapshot(path)) != entry["rows"]:
            problems.append(f"{entry['file']} : nombre de bougies différent")
    return problems


def mt5_fetcher(retries: int = 3) -> Fetcher:
    """Lecture réelle (MetaTrader 5 déjà ouvert et connecté) : une année à la fois, avec nouvelles tentatives."""
    import MetaTrader5 as mt5

    frames = {"H1": mt5.TIMEFRAME_H1, "D1": mt5.TIMEFRAME_D1, "H4": mt5.TIMEFRAME_H4, "M15": mt5.TIMEFRAME_M15}

    def fetch(symbol: str, tf: str, year: int) -> list[Row]:
        mt5.symbol_select(symbol, True)
        start, end = datetime(year, 1, 1, tzinfo=UTC), datetime(year, 12, 31, 23, 59, tzinfo=UTC)
        for attempt in range(retries):
            rates = mt5.copy_rates_range(symbol, frames[tf], start, end)
            if rates is not None:
                return [
                    (
                        int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                        int(r["tick_volume"]), int(r["spread"]),
                    )
                    for r in rates
                ]  # fmt: skip
            time.sleep(2 * (attempt + 1))
        return []

    return fetch
