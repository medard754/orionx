"""Fuseaux horaires du courtier. MT5 étiquette ses horodatages en heure SERVEUR (EET/EEST chez la plupart)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _last_sunday(year: int, month: int) -> datetime:  # mars et octobre ont 31 jours
    d = datetime(year, month, 31, 1, tzinfo=timezone.utc)
    while d.weekday() != 6:
        d -= timedelta(days=1)
    return d


def eu_dst_active(utc: datetime) -> bool:
    return _last_sunday(utc.year, 3) <= utc < _last_sunday(utc.year, 10)


def eet_offset(server_time: datetime) -> timedelta:
    """Décalage serveur -> UTC pour un courtier en heure d'Europe de l'Est (UTC+2 l'hiver, UTC+3 l'été)."""
    return timedelta(hours=3) if eu_dst_active(server_time - timedelta(hours=2)) else timedelta(hours=2)


def server_to_utc_eet(server_time: datetime) -> datetime:
    return server_time - eet_offset(server_time)


def estimate_offset(server_tick_time: datetime, local_utc: datetime) -> timedelta:
    """Décalage MESURÉ à partir d'un prix frais, arrondi à 30 minutes."""
    seconds = (server_tick_time - local_utc).total_seconds()
    return timedelta(seconds=round(seconds / 1800) * 1800)
