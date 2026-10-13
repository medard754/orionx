"""Bus d'événements asynchrone minimal (in-process)."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)
Handler = Callable[[Any], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: type, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    async def publish(self, event: object) -> None:
        for handler in list(self._handlers[type(event)]):
            try:
                await handler(event)
            except Exception:  # un abonné défaillant ne doit jamais bloquer les autres
                log.exception("Handler en échec pour %s", type(event).__name__)
