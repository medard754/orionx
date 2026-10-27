"""Envoi Telegram (bibliothèque standard uniquement) et identifiants hors du dépôt.

Le jeton du bot donne le contrôle du bot : il n'est JAMAIS écrit dans le code ni dans config.yaml. Ordre de recherche :
variables d'environnement ORIONX_TELEGRAM_TOKEN / ORIONX_TELEGRAM_CHAT_ID, puis coffre Windows (`keyring`, facultatif).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from types import ModuleType
from typing import Any

SERVICE = "orionx"
API = "https://api.telegram.org"
Opener = Callable[..., Any]


class AlertError(Exception):
    pass


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, timeout: float = 20.0, retries: int = 2, opener: Opener = urllib.request.urlopen,
                 sleep: Callable[[float], None] = time.sleep) -> None:  # fmt: skip
        # Délai large et nouvelles tentatives : mesuré le 27 oct., la poignée de main TLS vers Telegram varie de 0,3 à 11 s sur ce
        # poste. Sans conséquence pour l'agent : les alertes partent dans un fil dédié.
        self._token, self._chat_id, self._timeout, self._open = token, chat_id, timeout, opener
        self._retries, self._sleep = retries, sleep

    def __repr__(self) -> str:  # jamais le jeton, même dans un journal ou une trace
        return f"TelegramNotifier(chat_id={self._chat_id!r}, token=***)"

    def _mask(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    def _call(self, method: str, params: Mapping[str, str] | None = None) -> Any:
        data = urllib.parse.urlencode(params).encode() if params is not None else None
        req = urllib.request.Request(f"{API}/bot{self._token}/{method}", data=data, method="POST" if data else "GET")
        for attempt in range(self._retries + 1):
            try:
                with self._open(req, timeout=self._timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:  # Telegram a répondu : réessayer ne changerait rien
                hint = {401: "jeton refusé : recopiez-le depuis @BotFather", 404: "jeton mal formé : recopiez-le depuis @BotFather",
                        400: "conversation introuvable : envoyez /start au bot"}.get(exc.code, "")  # fmt: skip
                raise AlertError(f"Telegram a refusé (HTTP {exc.code}) {hint}".strip()) from None
            except Exception as exc:  # noqa: BLE001 - message masqué, et pas de chaînage : la requête contient le jeton
                if attempt < self._retries:  # réseau lent ou coupé : on réessaie (un doublon vaut mieux qu'une alerte perdue)
                    self._sleep(2.0 * (attempt + 1))
                    continue
                tries = self._retries + 1
                raise AlertError(f"Telegram injoignable (réseau, {tries} essais) : {self._mask(str(exc))}") from None
        if not body.get("ok"):
            raise AlertError(f"Telegram a refusé : {self._mask(str(body.get('description')))}")
        return body.get("result")

    def send(self, text: str) -> None:
        self._call("sendMessage", {"chat_id": self._chat_id, "text": text[:4000], "disable_web_page_preview": "true"})

    def discover_chat_id(self) -> str | None:
        """Après un message envoyé au bot (ex. /start), retrouve la conversation : évite de chercher le chat_id à la main."""
        updates = self._call("getUpdates") or []
        for u in reversed(updates):
            chat = (u.get("message") or u.get("channel_post") or {}).get("chat") or {}
            if "id" in chat:
                return str(chat["id"])
        return None


TOKEN_RE = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,50}")


def check_token(token: str) -> str | None:
    """Message d'erreur si `token` n'a pas la forme d'un jeton de bot, sinon None. N'affiche JAMAIS le jeton."""
    if TOKEN_RE.fullmatch(token):
        return None
    odd = sum(1 for c in token if not (c.isalnum() or c in ":_-"))
    detail = f"{len(token)} caractère(s) reçu(s)" + (f", dont {odd} inattendu(s)" if odd else "")
    return (f"Ce n'est pas un jeton de bot ({detail}). Attendu : des chiffres, deux-points, puis environ 35 caractères. "
            "Le collage dans une saisie invisible échoue souvent sous Windows : voir l'autre méthode ci-dessous.")  # fmt: skip


def _keyring() -> ModuleType | None:
    try:
        import keyring  # noqa: PLC0415  (facultatif : pip install -e ".[alerts]")
    except ImportError:
        return None
    module: ModuleType = keyring
    return module


def load_credentials(env: Mapping[str, str] | None = None, kr: ModuleType | None = None) -> tuple[str, str] | None:
    env = os.environ if env is None else env
    token, chat = env.get("ORIONX_TELEGRAM_TOKEN"), env.get("ORIONX_TELEGRAM_CHAT_ID")
    if token and chat:
        return token, chat
    kr = kr or _keyring()
    if kr is not None:
        token, chat = kr.get_password(SERVICE, "telegram_token"), kr.get_password(SERVICE, "telegram_chat_id")
    return (token, chat) if token and chat else None


def store_credentials(token: str, chat_id: str, kr: ModuleType | None = None) -> None:
    kr = kr or _keyring()
    if kr is None:
        raise AlertError("Coffre Windows indisponible : pip install -e \".[alerts]\" (ou variables ORIONX_TELEGRAM_TOKEN / _CHAT_ID)")
    kr.set_password(SERVICE, "telegram_token", token)
    kr.set_password(SERVICE, "telegram_chat_id", chat_id)
