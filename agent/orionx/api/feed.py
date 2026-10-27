"""Flux d'événements en direct pour l'API (T-14). Bibliothèque standard uniquement : l'agent ne dépend pas de FastAPI.

Même entrée que les alertes, `handle(kind, payload)`, alimentée par le journal de l'agent et par le bus. Un abonné lent
perd des messages plutôt que de ralentir l'agent ; une erreur ici ne remonte jamais dans la boucle de trading.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from typing import Any

from orionx.audit.log import audited_events, to_jsonable
from orionx.core.bus import EventBus
from orionx.core.models import utcnow

log = logging.getLogger(__name__)
Item = dict[str, Any] | None  # None : fin du flux (arrêt de l'API)


class EventFeed:
    def __init__(self, max_queue: int = 500) -> None:
        self._max = max_queue
        self._subs: dict[asyncio.Queue[Item], asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue[Item]:
        """À appeler depuis la boucle de l'abonné : ses messages y seront livrés (sûr même depuis un autre fil)."""
        q: asyncio.Queue[Item] = asyncio.Queue(maxsize=self._max)
        with self._lock:
            self._subs[q] = asyncio.get_running_loop()
        return q

    def unsubscribe(self, q: asyncio.Queue[Item]) -> None:
        with self._lock:
            self._subs.pop(q, None)

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    def handle(self, kind: str, payload: Any = None) -> None:
        try:
            item = {"kind": kind, "at": utcnow().isoformat(), "payload": to_jsonable(payload) if payload is not None else {}}
            with self._lock:
                subs = list(self._subs.items())
            for q, loop in subs:
                try:
                    loop.call_soon_threadsafe(self._put, q, item)
                except RuntimeError:  # boucle de l'abonné fermée
                    self.unsubscribe(q)
        except Exception:  # noqa: BLE001 - le flux ne doit jamais casser l'agent
            log.exception("Événement %s non diffusé", kind)

    def close(self) -> None:
        """À l'arrêt : chaque abonné reçoit la fin du flux et ferme sa connexion (sinon l'arrêt du serveur attend)."""
        with self._lock:
            subs = list(self._subs.items())
        for q, loop in subs:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._put, q, None)

    @staticmethod
    def _put(q: asyncio.Queue[Item], item: Item) -> None:
        with contextlib.suppress(asyncio.QueueFull):  # abonné trop lent : il perd ce message, l'agent n'attend pas
            q.put_nowait(item)


def attach_feed(bus: EventBus, feed: EventFeed) -> None:
    async def forward(event: Any) -> None:
        feed.handle(type(event).__name__, event)

    for cls in audited_events():
        bus.subscribe(cls, forward)
