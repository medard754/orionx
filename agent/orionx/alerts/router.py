"""Alertes (M8) : choisit ce qui mérite d'être envoyé, le formate et l'expédie SANS jamais bloquer ni casser l'agent.

Source unique `handle(kind, payload)` : événements du bus et entrées du journal passent par le même chemin.
L'envoi se fait dans un fil dédié : une panne Telegram ou un réseau lent n'ont aucun effet sur la boucle de trading.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from orionx.audit.log import to_jsonable
from orionx.core.bus import EventBus
from orionx.core.config import AlertsConfig
from orionx.core.models import utcnow

log = logging.getLogger(__name__)

LEVELS = {"info": 0, "important": 1, "critique": 2}
HEADERS = {"info": "ℹ️ ORIONX", "important": "🟠 ORIONX · IMPORTANT", "critique": "🔴 ORIONX · CRITIQUE"}
SIGNAL_KINDS = ("SignalOnly", "SignalPending")
HALT_LABELS = {
    "DAILY_LOSS_LIMIT": "perte maximale du jour atteinte",
    "WEEKLY_LOSS_LIMIT": "perte maximale de la semaine atteinte",
    "MAX_DRAWDOWN": "drawdown maximal atteint (ne se lève PAS seul)",
    "CONSECUTIVE_LOSSES": "trop de pertes consécutives",
    "GATEWAY_ERRORS": "liaison MT5 en erreur : mode sûr, reconnexion en cours",
    "ACCOUNT_CHANGED": "compte du terminal changé : agent arrêté",
}
DEDICATED = {"DATA_STALE", "KILL_SWITCH", "ACCOUNT_CHANGED"}  # ont leur propre message : pas de doublon
CLOSING = {"THESIS_EXIT", "TIME_EXIT", "WEEKEND_FLAT", "PARTIAL_CLOSE", "THESIS_REDUCE"}
UNCONTROLLED = ("POSITION_OPEN", "SLIPPAGE_CLOSE_FAILED")  # une position est restée ouverte hors de tout contrôle


@dataclass(frozen=True)
class Alert:
    level: str
    key: str  # deux alertes de même clé rapprochées ne sont envoyées qu'une fois
    text: str


def _order_line(o: dict[str, Any]) -> str:
    return f"{str(o.get('side', '')).upper()} {o.get('volume')} {o.get('symbol')} · SL {o.get('sl')} · TP {o.get('tp')}"


def format_alert(kind: str, p: dict[str, Any]) -> Alert | None:
    """Traduit un événement en alerte, ou None s'il ne mérite pas d'être envoyé."""
    if kind == "AccountChanged":
        return Alert("critique", kind, f"Agent ARRÊTÉ : {p.get('reason')}. Vérifiez le compte du terminal MT5 avant de relancer.")
    if kind == "KillSwitchActivated":
        return Alert("critique", f"{kind}:{p.get('level')}", f"Arrêt d'urgence niveau {p.get('level')} : {p.get('reason')}")
    if kind == "CircuitBreakerTripped":
        reason = str(p.get("reason"))
        if reason in DEDICATED:
            return None
        return Alert("critique", f"{kind}:{reason}", f"Arrêt automatique des entrées : {HALT_LABELS.get(reason, reason)}")
    if kind == "PositionAdjusted":
        action, ticket, detail = str(p.get("action")), p.get("ticket"), p.get("detail")
        if action == "UNPROTECTED":
            return Alert("critique", f"{kind}:{action}:{ticket}", f"Position #{ticket} SANS stop-loss : {detail}")
        if action.endswith("_FAILED"):
            return Alert("important", f"{kind}:{action}:{ticket}", f"Échec {action} sur la position #{ticket} : {detail}")
        if action in CLOSING:
            return Alert("important", f"{kind}:{action}:{ticket}", f"{action} sur la position #{ticket} : {detail}")
        return None  # stop resserré, surveillance : trop fréquent pour une alerte
    if kind == "OrderFilled":
        o, r = p.get("order") or {}, p.get("result") or {}
        text = f"Ordre exécuté : {_order_line(o)} · prix {r.get('price')} · #{r.get('ticket')}"
        return Alert("important", f"{kind}:{o.get('proposal_id')}", text)
    if kind == "OrderFailed":
        o, r = p.get("order") or {}, p.get("result") or {}
        msg = str(r.get("message"))
        level = "critique" if any(u in msg for u in UNCONTROLLED) else "important"
        return Alert(level, f"{kind}:{o.get('proposal_id')}", f"Ordre en échec : {_order_line(o)} · {msg}")
    if kind == "PositionClosed":
        return Alert("important", f"{kind}:{p.get('ticket')}",
                     f"Position #{p.get('ticket')} {p.get('symbol')} fermée (dernier résultat connu {p.get('last_known_profit')})")
    if kind == "GatewayReconnectFailed":
        if p.get("attempt") == 1:
            return Alert("important", "GatewayDown", f"Liaison MT5 perdue : reconnexion en cours ({p.get('error')})")
        return Alert("info", f"{kind}:{p.get('attempt')}", f"Toujours pas de liaison MT5 après {p.get('attempt')} tentatives")
    if kind == "GatewayReconnected":
        return Alert("important", kind, f"Liaison MT5 rétablie après {p.get('down_seconds')} s. Mode sûr jusqu'à 30 cycles réussis.")
    if kind == "GatewayRecovered":
        return Alert("important", kind, "Reprise normale : le mode sûr est levé.")
    if kind == "DataFrozen":
        symbols = ", ".join(p.get("symbols") or [])
        return Alert("important", kind, f"Prix figés depuis plus de {p.get('limit_seconds')} s ({symbols}) : plus aucune entrée.")
    if kind == "DataRestored":
        return Alert("info", kind, "Prix de nouveau frais : entrées possibles.")
    if kind == "RiskConfigChanged":
        return Alert("important", kind, f"Paramètres de risque appliqués : {p.get('risk_per_trade_pct')} % par trade, "
                                        f"risque total {p.get('max_total_open_risk_pct')} %")  # fmt: skip
    if kind == "AgentStarted":
        return Alert("info", kind, f"Agent démarré : {p.get('autonomy')}, symboles {', '.join(p.get('symbols') or [])}")
    if kind == "AgentStopped":
        return Alert("important", kind, f"Agent arrêté après {p.get('cycles')} cycles. Les positions gardent leur stop-loss serveur.")
    if kind == "ThesisDegraded":
        reasons = ", ".join(p.get("reasons") or [])
        return Alert("info", f"{kind}:{p.get('ticket')}", f"Thèse dégradée sur #{p.get('ticket')} {p.get('symbol')} : {reasons}")
    if kind in SIGNAL_KINDS:
        prop = p.get("proposal") or {}
        what = "en attente d'approbation" if kind == "SignalPending" else "non exécuté (signaux seuls)"
        return Alert("info", f"{kind}:{prop.get('id')}", f"Signal {what} : {str(prop.get('side', '')).upper()} {prop.get('symbol')} · "
                                                         f"SL {prop.get('sl')} · TP {prop.get('tp')} · score {p.get('score')}")  # fmt: skip
    return None


class Notifier(Protocol):
    def send(self, text: str) -> None:
        """Lève une exception en cas d'échec (jamais de secret dans son message)."""
        ...


def attach_alerts(bus: EventBus, router: AlertRouter) -> None:
    """Abonne le routeur aux événements du bus susceptibles de produire une alerte."""
    from orionx.core import events as ev

    async def forward(event: Any) -> None:
        router.handle(type(event).__name__, event)

    for cls in (ev.CircuitBreakerTripped, ev.KillSwitchActivated, ev.OrderFilled, ev.OrderFailed, ev.PositionAdjusted,
                ev.ThesisDegraded):  # fmt: skip
        bus.subscribe(cls, forward)


class AlertRouter:
    def __init__(self, notifier: Notifier, cfg: AlertsConfig | None = None, clock: Callable[[], datetime] = utcnow,
                 queue_size: int = 200) -> None:  # fmt: skip
        self._notifier, self._cfg, self._clock = notifier, cfg or AlertsConfig(), clock
        self._last: dict[str, datetime] = {}
        self._sent: deque[datetime] = deque()
        self.suppressed = 0  # alertes écartées par la limite de débit (signalées dans l'alerte suivante)
        self.dropped = 0  # file pleine : alertes perdues
        self.failures = 0
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=queue_size)
        self._thread = threading.Thread(target=self._worker, name="orionx-alerts", daemon=True)
        self._thread.start()

    # --- côté agent : rapide, ne lève jamais ----------------------------------------
    def handle(self, kind: str, payload: Any = None) -> None:
        try:
            self._handle(kind, to_jsonable(payload) if payload is not None else {})
        except Exception:  # noqa: BLE001 - une alerte ne doit jamais casser l'agent
            log.exception("Alerte %s ignorée (erreur de formatage)", kind)

    def _handle(self, kind: str, payload: Any) -> None:
        alert = format_alert(kind, payload if isinstance(payload, dict) else {})
        if alert is None:
            return
        forced = kind in SIGNAL_KINDS and self._cfg.signals
        if not forced and LEVELS[alert.level] < LEVELS[self._cfg.min_level]:
            return
        now = self._clock()
        window = 60.0 if alert.level == "critique" else self._cfg.dedup_seconds
        last = self._last.get(alert.key)
        if last is not None and (now - last).total_seconds() < window:
            return
        while self._sent and (now - self._sent[0]).total_seconds() >= 60:
            self._sent.popleft()
        if alert.level != "critique" and len(self._sent) >= self._cfg.max_per_minute:
            self.suppressed += 1
            return
        self._last[alert.key] = now
        self._sent.append(now)
        note = f"\n({self.suppressed} alerte(s) écartée(s) par la limite de débit)" if self.suppressed else ""
        self.suppressed = 0
        text = f"{HEADERS[alert.level]}\n{alert.text}{note}\n{now:%d/%m %H:%M:%S} UTC"
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            self.dropped += 1
            log.error("File d'alertes pleine : alerte perdue (%s)", kind)

    # --- fil d'envoi -----------------------------------------------------------------
    def _worker(self) -> None:
        while True:
            text = self._queue.get()
            try:
                if text is None:
                    return
                self._notifier.send(text)
            except Exception as exc:  # noqa: BLE001 - réseau, Telegram indisponible : on journalise et on continue
                self.failures += 1
                log.warning("Envoi d'alerte impossible : %s", exc)
            finally:
                self._queue.task_done()

    def flush(self, timeout: float = 5.0) -> bool:
        """Attend que les alertes en file soient parties (ou le délai). Vrai si tout est parti."""
        end = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < end:
            time.sleep(0.01)
        return not self._queue.unfinished_tasks

    def close(self, timeout: float = 5.0) -> None:
        """À l'arrêt : laisse partir les dernières alertes (ex. « agent arrêté ») sans bloquer plus que `timeout`."""
        self.flush(timeout)
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            return
        self._thread.join(timeout=1.0)
