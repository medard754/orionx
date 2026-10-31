"""Mémoire de marché : ce que l'agent VOIT, enregistré en continu (étape 1 du plan « agent à mémoire »).

Passif et en lecture seule : l'enregistreur écoute le bus d'événements, n'envoie aucun ordre et n'influence aucune décision.
Il garde, pour chaque courtier dans un fichier séparé (jamais de mélange de sources) :
- `quotes_1m` : par minute et par symbole, le dernier prix et le spread réel (min / moyen / max) ;
- `states` : chaque état de marché publié par l'analyse (régime, séance, volatilité, efficience, force de tendance) ;
- `symbols` : la fiche du symbole chez ce courtier (taille du pip, lot minimal, valeur du tick) ;
- `meta` : identité du courtier (société, serveur, type de marge, devise, levier).

Le temps est celui de l'horloge locale (UTC) à laquelle le prix a été observé, jamais l'heure serveur du courtier (principe n°8).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from orionx.core.bus import EventBus
from orionx.core.events import MarketStateUpdated, TickReceived
from orionx.core.models import SymbolSpec, utcnow

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes_1m (symbol TEXT, minute TEXT, ticks INTEGER, bid REAL, ask REAL,
  spread_min REAL, spread_avg REAL, spread_max REAL, PRIMARY KEY (symbol, minute));
CREATE TABLE IF NOT EXISTS states (symbol TEXT, ts TEXT, regime TEXT, session TEXT, atr REAL, efficiency REAL,
  trend_strength REAL, volatility_ratio REAL, PRIMARY KEY (symbol, ts));
CREATE TABLE IF NOT EXISTS symbols (symbol TEXT PRIMARY KEY, pip REAL, digits INTEGER, volume_min REAL, volume_step REAL,
  tick_value REAL, recorded_at TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
_UPSERT = """
INSERT INTO quotes_1m (symbol, minute, ticks, bid, ask, spread_min, spread_avg, spread_max) VALUES (?,?,?,?,?,?,?,?)
ON CONFLICT (symbol, minute) DO UPDATE SET
  spread_avg = (spread_avg * ticks + excluded.spread_avg * excluded.ticks) / (ticks + excluded.ticks),
  ticks = ticks + excluded.ticks, bid = excluded.bid, ask = excluded.ask,
  spread_min = MIN(spread_min, excluded.spread_min), spread_max = MAX(spread_max, excluded.spread_max)
"""


def memory_path(data_dir: Path, info: Mapping[str, str] | None) -> Path:
    """Un fichier par serveur de courtier : les données de deux courtiers ne se mélangent jamais."""
    server = (info or {}).get("server", "") or "inconnu"
    slug = re.sub(r"[^a-z0-9]+", "-", server.lower()).strip("-") or "inconnu"
    return data_dir / "memory" / f"{slug}.db"


def _minute(t: datetime) -> str:
    return t.astimezone(UTC).replace(second=0, microsecond=0).isoformat()


@dataclass
class _Open:
    minute: str
    ticks: int
    bid: float
    ask: float
    smin: float
    ssum: float
    smax: float


class MarketMemory:
    def __init__(self, path: str | Path, meta: Mapping[str, str] | None = None, specs: Mapping[str, SymbolSpec] | None = None,
                 clock: Callable[[], datetime] = utcnow) -> None:  # fmt: skip
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self._db.executescript(_SCHEMA)
        self._specs, self._clock = specs if specs is not None else {}, clock
        self._open: dict[str, _Open] = {}
        self._known: set[str] = set()
        for key, value in (meta or {}).items():
            self._db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))
        self._db.execute("INSERT OR IGNORE INTO meta VALUES ('first_start', ?)", (clock().isoformat(),))
        self._db.commit()

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(TickReceived, self.on_tick)
        bus.subscribe(MarketStateUpdated, self.on_state)

    # --- écoute ----------------------------------------------------------------------------------------------------
    async def on_tick(self, event: TickReceived) -> None:
        tick = event.tick
        minute, spread = _minute(tick.observed_at), tick.ask - tick.bid
        cur = self._open.get(tick.symbol)
        if cur is not None and minute < cur.minute:
            return  # tick en retard : ignoré
        if cur is None or minute > cur.minute:
            if cur is not None:
                self._write(tick.symbol, cur)
            self._open[tick.symbol] = _Open(minute, 1, tick.bid, tick.ask, spread, spread, spread)
            self._record_symbol(tick.symbol)
            return
        cur.ticks += 1
        cur.bid, cur.ask = tick.bid, tick.ask
        cur.smin, cur.smax, cur.ssum = min(cur.smin, spread), max(cur.smax, spread), cur.ssum + spread

    async def on_state(self, event: MarketStateUpdated) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO states VALUES (?,?,?,?,?,?,?,?)",
            (event.symbol, self._clock().astimezone(UTC).isoformat(), str(event.regime), str(event.session), event.atr,
             event.efficiency, event.trend_strength, event.volatility_ratio),
        )  # fmt: skip
        self._db.commit()

    # --- écriture --------------------------------------------------------------------------------------------------
    def _write(self, symbol: str, o: _Open) -> None:
        self._db.execute(_UPSERT, (symbol, o.minute, o.ticks, o.bid, o.ask, o.smin, o.ssum / o.ticks, o.smax))
        self._db.commit()

    def _record_symbol(self, symbol: str) -> None:
        spec = self._specs.get(symbol)
        if spec is None or symbol in self._known:
            return
        self._known.add(symbol)
        self._db.execute(
            "INSERT OR REPLACE INTO symbols VALUES (?,?,?,?,?,?,?)",
            (symbol, spec.point * 10, spec.digits, spec.volume_min, spec.volume_step, spec.tick_value, self._clock().isoformat()),
        )  # fmt: skip
        self._db.commit()

    def flush(self) -> None:
        for symbol, o in self._open.items():
            self._write(symbol, o)
        self._open.clear()

    def close(self) -> None:
        self.flush()
        self._db.close()


# --- lecture : rapport --------------------------------------------------------------------------------------------------
def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def render_report(path: str | Path, out: Callable[[str], None] = print) -> int:
    """Résumé d'un fichier mémoire : couverture et spread réel par heure UTC (en pips). Lecture seule."""
    db = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        out(f"Courtier : {meta.get('company', '?')} · serveur {meta.get('server', '?')} · marge {meta.get('margin_mode', '?')} · "
            f"devise {meta.get('currency', '?')} · levier 1:{meta.get('leverage', '?')}")  # fmt: skip
        symbols = [r[0] for r in db.execute("SELECT DISTINCT symbol FROM quotes_1m ORDER BY symbol")]
        if not symbols:
            out("Aucune donnée enregistrée pour l'instant (laisser tourner l'agent : `run`).")
            return 2
        for sym in symbols:
            pip_row = db.execute("SELECT pip FROM symbols WHERE symbol=?", (sym,)).fetchone()
            pip = pip_row[0] if pip_row else 0.0001
            first, last, n = db.execute("SELECT MIN(minute), MAX(minute), COUNT(*) FROM quotes_1m WHERE symbol=?", (sym,)).fetchone()
            states = db.execute("SELECT COUNT(*) FROM states WHERE symbol=?", (sym,)).fetchone()[0]
            out(f"\n{sym} : {n} minutes de prix entre {first[:16]} et {last[:16]} UTC · {states} états de marché · pip = {pip:g}")
            by_hour: dict[int, list[float]] = {}
            for minute, avg in db.execute("SELECT minute, spread_avg FROM quotes_1m WHERE symbol=?", (sym,)):
                by_hour.setdefault(datetime.fromisoformat(minute).hour, []).append(avg / pip)
            out("  heure UTC : minutes · spread médian · spread p95 (pips)")
            for hour in sorted(by_hour):
                values = by_hour[hour]
                if len(values) >= 5:
                    out(f"  {hour:02d}h : {len(values):5d} · {statistics.median(values):.2f} · {_quantile(values, 0.95):.2f}")
            allv = [v for values in by_hour.values() for v in values]
            out(f"  ensemble : spread médian {statistics.median(allv):.2f} pip · p95 {_quantile(allv, 0.95):.2f} pip")
        out("\nLimites : la commission n'est pas dans ces prix (à lire sur le compte) ; chiffres valables pour la période enregistrée.")
        return 0
    finally:
        db.close()
