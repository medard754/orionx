"""Calendrier économique tenu à la main (fichier CSV). Source volontairement simple et fiable.

Format (UTC) :  datetime_utc,currency,impact,title
                2026-10-14T12:30:00Z,USD,high,Inflation (CPI)
Les lignes vides et celles qui commencent par # sont ignorées.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orionx.core.errors import ConfigError

RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class EconomicEvent:
    time: datetime
    currency: str
    impact: str
    title: str


def currencies_of(symbol: str) -> set[str]:
    s = symbol.upper()
    return {s[:3], s[3:6]} if len(s) >= 6 else {s}


class EconomicCalendar:
    def __init__(self, events: Iterable[EconomicEvent] = ()) -> None:
        self.events = sorted(events, key=lambda e: e.time)

    @classmethod
    def from_csv(cls, path: str | Path) -> EconomicCalendar:
        events: list[EconomicEvent] = []
        lines = [(i, line) for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1)
                 if line.strip() and not line.lstrip().startswith("#")]  # fmt: skip
        for (i, _line), row in zip(lines, csv.reader(text for _, text in lines), strict=True):
            if i == lines[0][0] and row and row[0].strip().lower() == "datetime_utc":
                continue  # en-tête
            try:
                when = datetime.fromisoformat(row[0].strip().replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                impact = row[2].strip().lower()
                if impact not in RANK:
                    raise ValueError(f"impact inconnu : {impact}")
                events.append(EconomicEvent(when, row[1].strip().upper(), impact, ",".join(row[3:]).strip()))
            except (ValueError, IndexError) as exc:
                raise ConfigError(f"Calendrier, ligne {i} invalide : {exc}") from exc
        return cls(events)

    def _relevant(self, symbol: str, min_impact: str) -> list[EconomicEvent]:
        cur, rank = currencies_of(symbol), RANK[min_impact]
        return [e for e in self.events if e.currency in cur and RANK[e.impact] >= rank]

    def blackout(self, symbol: str, now: datetime, before_min: float, after_min: float,
                 min_impact: str = "high") -> EconomicEvent | None:  # fmt: skip
        for e in self._relevant(symbol, min_impact):
            if e.time - timedelta(minutes=before_min) <= now <= e.time + timedelta(minutes=after_min):
                return e
        return None

    def upcoming(self, symbol: str, now: datetime, hours: float = 24.0, min_impact: str = "medium") -> list[EconomicEvent]:
        end = now + timedelta(hours=hours)
        return [e for e in self._relevant(symbol, min_impact) if now <= e.time <= end]
