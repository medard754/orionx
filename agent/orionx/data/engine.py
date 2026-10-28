"""Données de marché temps réel (T-04) : ticks, bougies, détection de données périmées."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from orionx.connector_mt5.gateway import Gateway
from orionx.core.bus import EventBus
from orionx.core.errors import InvalidQuoteError
from orionx.core.events import BarClosed, DataRecovered, DataStale, TickReceived
from orionx.core.models import Bar, Tick, utcnow

log = logging.getLogger(__name__)

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def _floor(t: datetime, minutes: int) -> datetime:
    ts = int(t.timestamp())
    return datetime.fromtimestamp(ts - ts % (minutes * 60), tz=UTC)


class BarBuilder:
    """Agrège des ticks (prix bid) en bougies. Renvoie la bougie dès qu'elle est fermée."""

    def __init__(self, timeframe: str) -> None:
        self._minutes = TF_MINUTES[timeframe]
        self._bucket: datetime | None = None
        self._o = self._h = self._l = self._c = 0.0
        self._n = 0

    def update(self, t: datetime, price: float) -> Bar | None:
        bucket = _floor(t, self._minutes)
        if self._bucket is None or bucket > self._bucket:
            closed = None
            if self._bucket is not None:
                closed = Bar(self._bucket, self._o, self._h, self._l, self._c, float(self._n))
            self._bucket, self._o = bucket, price
            self._h = self._l = self._c = price
            self._n = 1
            return closed
        if bucket == self._bucket:
            self._h, self._l, self._c = max(self._h, price), min(self._l, price), price
            self._n += 1
        return None  # tick en retard : ignoré


class MarketStore:
    """Stockage SQLite des bougies fermées (historique pour backtest et audit)."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS bars (symbol TEXT, tf TEXT, time TEXT, o REAL, h REAL, l REAL, c REAL, v REAL,"
            " PRIMARY KEY (symbol, tf, time))"
        )

    def save_bar(self, symbol: str, tf: str, b: Bar) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)",
            (symbol, tf, b.time.isoformat(), b.open, b.high, b.low, b.close, b.volume),
        )
        self._db.commit()

    def load_bars(self, symbol: str, tf: str, limit: int = 1000) -> list[Bar]:
        rows = self._db.execute(
            "SELECT time,o,h,l,c,v FROM bars WHERE symbol=? AND tf=? ORDER BY time DESC LIMIT ?", (symbol, tf, limit)
        ).fetchall()
        return [Bar(datetime.fromisoformat(t), o, h, lo, c, v) for t, o, h, lo, c, v in reversed(rows)]


class MarketData:
    def __init__(
        self,
        gateway: Gateway,
        symbols: Sequence[str],
        bus: EventBus | None = None,
        *,
        stale_after: float = 3.0,
        timeframes: Sequence[str] = ("M1", "M5", "H1"),
        store: MarketStore | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._gw, self._symbols, self._bus, self._store, self._clock = gateway, list(symbols), bus, store, clock
        self._stale_after, self._timeframes = stale_after, tuple(timeframes)
        self._builders = {s: {tf: BarBuilder(tf) for tf in timeframes} for s in self._symbols}
        self.last: dict[str, Tick] = {}
        self._stale: set[str] = set()
        self._invalid: set[str] = set()  # symboles dont la dernière cotation lue était incohérente
        self.confirmed: set[str] = set()  # symboles dont on a vu le prix CHANGER au moins une fois (flux réellement vivant)

    def watch(self, symbol: str) -> None:
        """Suit aussi un symbole non configuré (ex. position ouverte à la main sur une autre paire)."""
        if symbol not in self._builders:
            self._symbols.append(symbol)
            self._builders[symbol] = {tf: BarBuilder(tf) for tf in self._timeframes}

    async def poll(self) -> list[Tick]:
        """Un passage : lit les prix, publie ce qui a changé, signale les flux gelés."""
        changed: list[Tick] = []
        for sym in self._symbols:
            prev = self.last.get(sym)
            try:
                tick = await self._gw.get_tick(sym)
            except InvalidQuoteError as exc:  # on garde le dernier prix valide, qui vieillit : le mode sûr prendra le relais
                if sym not in self._invalid:
                    log.warning("%s : on attend une cotation valide", exc)
                self._invalid.add(sym)
                if prev is None:
                    continue
                tick = prev
            else:
                self._invalid.discard(sym)
            if (tick.bid, tick.ask, tick.time) != ((prev.bid, prev.ask, prev.time) if prev else None):
                if prev is not None:
                    self.confirmed.add(sym)
                self.last[sym] = tick
                changed.append(tick)
                await self._emit(TickReceived(tick))
                for tf, builder in self._builders[sym].items():
                    bar = builder.update(tick.time, tick.bid)
                    if bar is not None:
                        if self._store:
                            self._store.save_bar(sym, tf, bar)
                        await self._emit(BarClosed(sym, tf, bar))
            age = (self._clock() - tick.observed_at).total_seconds()
            if age > self._stale_after and sym not in self._stale:
                self._stale.add(sym)
                await self._emit(DataStale(sym, age))
            elif age <= self._stale_after and sym in self._stale:
                self._stale.discard(sym)
                await self._emit(DataRecovered(sym))
        return changed

    async def _emit(self, event: object) -> None:
        if self._bus:
            await self._bus.publish(event)
