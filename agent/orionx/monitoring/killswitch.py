"""Arrêt d'urgence à 3 niveaux (T-05).

 1 = pause des entrées        (aucun nouvel ordre ; les positions restent gérées)
 2 = protéger les positions   (niveau 1 + stops resserrés, jamais éloignés)
 3 = tout clôturer            (niveau 1 + clôture de toutes les positions, avec nouvelles tentatives)

Le niveau ne peut qu'AUGMENTER ; `reset()` est une action humaine explicite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from orionx.connector_mt5.gateway import Gateway
from orionx.core.bus import EventBus
from orionx.core.events import KillSwitchActivated
from orionx.execution.executor import Executor
from orionx.positions.manager import protective_stop
from orionx.risk.manager import RiskManager

log = logging.getLogger(__name__)
HALT_REASON = "KILL_SWITCH"


@dataclass
class KillReport:
    level: int
    actions: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


class KillSwitch:
    def __init__(self, gateway: Gateway, executor: Executor, risk: RiskManager, bus: EventBus | None = None,
                 close_retries: int = 3) -> None:  # fmt: skip
        self._gw, self._executor, self._risk, self._bus = gateway, executor, risk, bus
        self._retries = close_retries
        self.level = 0

    async def activate(self, level: int, reason: str) -> KillReport:
        if level not in (1, 2, 3):
            raise ValueError("level doit valoir 1, 2 ou 3")
        report = KillReport(level)
        if level < self.level:
            report.actions.append(f"déjà au niveau {self.level} : rien à faire")
            return report
        self.level = level
        self._executor.block(f"KILL_SWITCH_L{level}")
        self._risk.halt(HALT_REASON)
        report.actions.append("nouvelles entrées bloquées")
        if level >= 2:
            await self._protect(report)
        if level == 3:
            await self._close_all(report)
        if self._bus:
            await self._bus.publish(KillSwitchActivated(level, reason))
        log.warning("ARRÊT D'URGENCE niveau %s (%s) : %s | échecs : %s", level, reason, report.actions, report.failures)
        return report

    async def _protect(self, report: KillReport) -> None:
        for pos in await self._gw.get_positions():
            try:
                spec, tick = await self._gw.get_symbol_spec(pos.symbol), await self._gw.get_tick(pos.symbol)
                new_sl = protective_stop(pos, tick, spec)
                if new_sl is None:
                    continue
                res = await self._gw.modify_position(pos.ticket, new_sl, pos.tp if pos.tp is not None else 0.0)
                (report.actions if res.ok else report.failures).append(
                    f"stop #{pos.ticket} -> {new_sl}" if res.ok else f"stop #{pos.ticket} refusé : {res.message}"
                )
            except Exception as exc:  # noqa: BLE001 - l'arrêt d'urgence ne doit jamais s'interrompre
                report.failures.append(f"stop #{pos.ticket} : {exc}")

    async def _close_all(self, report: KillReport) -> None:
        for attempt in range(1, self._retries + 1):
            remaining = await self._gw.get_positions()
            if not remaining:
                break
            for pos in remaining:
                try:
                    res = await self._gw.close_position(pos.ticket)
                    if res.ok:
                        report.actions.append(f"position #{pos.ticket} clôturée")
                except Exception as exc:  # noqa: BLE001
                    log.error("clôture #%s échouée (tentative %s) : %s", pos.ticket, attempt, exc)
        for pos in await self._gw.get_positions():
            report.failures.append(f"position #{pos.ticket} NON clôturée : à fermer à la main dans MT5")

    def reset(self) -> None:
        """Levée MANUELLE : à n'appeler que sur décision explicite de l'utilisateur."""
        self.level = 0
        self._executor.unblock()
        self._risk.resume(HALT_REASON)
