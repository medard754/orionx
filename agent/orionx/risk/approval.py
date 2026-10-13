"""Approbation signée (HMAC). M6 refuse tout ordre qui n'a pas été signé par M5.

Limite assumée : la clé vit dans le même processus. Cela empêche les contournements
accidentels (un module qui enverrait un ordre « brut »), pas un code malveillant.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from orionx.core.errors import ApprovalError
from orionx.core.models import ApprovedOrder, utcnow


class ApprovalSigner:
    def __init__(self, key: bytes | None = None, clock: Callable[[], datetime] = utcnow) -> None:
        self._key = key or secrets.token_bytes(32)
        self._clock = clock

    def _digest(self, order: ApprovedOrder) -> str:
        return hmac.new(self._key, order.canonical(), hashlib.sha256).hexdigest()

    def sign(self, order: ApprovedOrder) -> ApprovedOrder:
        return replace(order, signature=self._digest(order))

    def verify(self, order: ApprovedOrder) -> None:
        if not order.signature:
            raise ApprovalError("Ordre non signé")
        if not hmac.compare_digest(order.signature, self._digest(order)):
            raise ApprovalError("Signature invalide (ordre modifié ou signé par un autre module)")
        if self._clock() > order.expires_at:
            raise ApprovalError("Approbation expirée")
