"""Point d'entrée en ligne de commande.

  python -m orionx.main simulate          # scénario complet sur courtier simulé (sans MT5)
  python -m orionx.main mt5-check         # lecture seule d'un compte DÉMO MT5 (aucun ordre)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.bus import EventBus
from orionx.core.config import AppConfig, load_config
from orionx.core.events import OrderFilled, TradeApproved, TradeRejected
from orionx.core.models import Side, SymbolSpec, TradeProposal, utcnow
from orionx.execution.executor import Executor
from orionx.risk.approval import ApprovalSigner
from orionx.risk.manager import RiskManager

EURUSD = SymbolSpec("EURUSD", 5, 1e-5, 1e-5, 1.0, 0.01, 100.0, 0.01)


def _config(path: str | None) -> AppConfig:
    return load_config(path) if path else AppConfig()


async def simulate(cfg: AppConfig) -> int:
    now = utcnow()
    gw = SimulatedGateway({"EURUSD": EURUSD})
    gw.set_tick("EURUSD", 1.08412, 1.08420, now)
    signer = ApprovalSigner()
    risk = RiskManager(cfg.risk, signer, max_data_age_seconds=cfg.max_data_age_seconds)
    executor = Executor(gw, signer, bus := EventBus())

    async def show(event: object) -> None:
        print(f"  événement : {event}")

    for kind in (TradeApproved, TradeRejected, OrderFilled):
        bus.subscribe(kind, show)

    account = await gw.get_account()
    risk.start(account)
    tick = await gw.get_tick("EURUSD")
    specs = {"EURUSD": EURUSD}

    def proposal(pid: str, sl: float, tp: float) -> TradeProposal:
        return TradeProposal(pid, "EURUSD", Side.BUY, sl, tp, 0.72, "demo", ("scénario",), now, now + timedelta(minutes=2))

    print("1) Proposition valide (SL 1.0820, TP 1.0910)")
    d = risk.evaluate(proposal("p1", 1.0820, 1.0910), tick, account, await gw.get_positions(), specs)
    print(f"  décision : approuvé={d.approved} volume={d.order.volume if d.order else '-'} raisons={d.reasons}")
    if d.order:
        await bus.publish(TradeApproved(d.order))
        await executor.execute(d.order)  # publie lui-même OrderFilled / OrderFailed

    print("2) Proposition avec rapport risque/rendement trop faible")
    d2 = risk.evaluate(proposal("p2", 1.0820, 1.0850), tick, account, await gw.get_positions(), specs)
    print(f"  décision : approuvé={d2.approved} raisons={d2.reasons}")
    await bus.publish(TradeRejected("p2", d2.reasons))
    return 0


async def mt5_check() -> int:
    from orionx.connector_mt5.mt5_real import Mt5Gateway  # import tardif (Windows)

    path = os.environ.get("ORIONX_MT5_PATH")
    if os.environ.get("ORIONX_MT5_LOGIN"):
        try:
            gw = Mt5Gateway(
                int(os.environ["ORIONX_MT5_LOGIN"]),
                os.environ["ORIONX_MT5_PASSWORD"],
                os.environ["ORIONX_MT5_SERVER"],
                path,
            )
        except KeyError as exc:
            print(f"Variable d'environnement manquante : {exc}. Voir .env.example")
            return 2
    else:
        print("Aucun identifiant fourni : rattachement au compte déjà connecté dans le terminal MT5.")
        gw = Mt5Gateway(path=path)
    await gw.connect()
    try:
        acc = await gw.get_account()
        print(f"Compte : équité={acc.equity} {acc.currency} démo={acc.is_demo}")
        if not acc.is_demo:
            print("REFUS : ce n'est pas un compte démo. ORIONX v0 ne travaille qu'en démo.")
            return 3
        print("Spec EURUSD :", await gw.get_symbol_spec("EURUSD"))
        print("Tick EURUSD :", await gw.get_tick("EURUSD"))
        print("Positions   :", await gw.get_positions())
    finally:
        await gw.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orionx")
    parser.add_argument("command", choices=["simulate", "mt5-check"])
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = _config(str(args.config) if args.config else None)
    if args.command == "simulate":
        return asyncio.run(simulate(cfg))
    return asyncio.run(mt5_check())


if __name__ == "__main__":
    sys.exit(main())
