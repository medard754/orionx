"""Point d'entrée en ligne de commande.

  python -m orionx.main simulate          # scénario complet sur courtier simulé (sans MT5)
  python -m orionx.main mt5-check         # lecture seule d'un compte DÉMO MT5 (aucun ordre)
  python -m orionx.main demo-trade        # T-02 : UN ordre démo de 0,01 lot, puis fermeture
  python -m orionx.main run               # boucle de l'agent (signaux seuls par défaut)
  python -m orionx.main kill --level 3    # arrêt d'urgence depuis un autre terminal
  python -m orionx.main clear-kill        # retire le drapeau d'arrêt d'urgence
  python -m orionx.main audit-verify      # vérifie l'intégrité du journal
  python -m orionx.main backtest          # backtest de la stratégie sur l'historique MT5 (lecture seule)
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

DATA_DIR = Path(os.environ.get("ORIONX_DATA_DIR", "data"))
KILL_FLAG = DATA_DIR / "KILL_SWITCH.json"
AUDIT_DB = DATA_DIR / "audit.db"
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


def _mt5_gateway() -> "Mt5Gateway | None":  # noqa: UP037
    from orionx.connector_mt5.mt5_real import Mt5Gateway  # import tardif (Windows)

    path = os.environ.get("ORIONX_MT5_PATH")
    if os.environ.get("ORIONX_MT5_LOGIN"):
        try:
            return Mt5Gateway(
                int(os.environ["ORIONX_MT5_LOGIN"]),
                os.environ["ORIONX_MT5_PASSWORD"],
                os.environ["ORIONX_MT5_SERVER"],
                path,
            )
        except KeyError as exc:
            print(f"Variable d'environnement manquante : {exc}. Voir .env.example")
            return None
    print("Aucun identifiant fourni : rattachement au compte déjà connecté dans le terminal MT5.")
    return Mt5Gateway(path=path)


async def mt5_check() -> int:
    gw = _mt5_gateway()
    if gw is None:
        return 2
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


async def demo_trade(auto_yes: bool, hold: float) -> int:
    from orionx.demo_trade import run_demo_trade

    gw = _mt5_gateway()
    if gw is None:
        return 2
    await gw.connect()
    try:
        return await run_demo_trade(
            gw,
            hold_seconds=hold,
            confirm=(lambda _msg: True) if auto_yes else (lambda msg: input(msg).strip().upper() == "OUI"),
        )
    finally:
        await gw.shutdown()


async def run_agent(cfg: AppConfig, allow_auto: bool, cycle: float, max_cycles: int | None) -> int:
    from orionx.agent import Agent
    from orionx.audit.log import AuditLog
    from orionx.data.engine import MarketStore
    from orionx.strategies.trend_pullback import TrendPullback

    gw = _mt5_gateway()
    if gw is None:
        return 2
    audit = AuditLog(AUDIT_DB)
    await gw.connect()
    agent = Agent(gw, cfg, strategy=TrendPullback(), audit=audit, store=MarketStore(DATA_DIR / "market.db"),
                  allow_auto=allow_auto, kill_flag=KILL_FLAG)  # fmt: skip
    print(f"Agent démarré : autonomie={agent.autonomy}, symboles={cfg.symbols}. Ctrl+C pour arrêter.")
    print("Les positions ouvertes restent protégées par leur stop-loss même si l'agent s'arrête.")
    try:
        await agent.run(asyncio.Event(), cycle_seconds=cycle, max_cycles=max_cycles)
    finally:
        await gw.shutdown()
        print("Agent arrêté.")
    return 0


async def kill_cmd(level: int, auto_yes: bool) -> int:
    from orionx.connector_mt5.mt5_real import Mt5Gateway  # noqa: F401
    from orionx.monitoring.flag import write_kill_flag
    from orionx.monitoring.killswitch import KillSwitch

    if not auto_yes and input(f"Arrêt d'urgence niveau {level}. Tapez OUI pour confirmer : ").strip().upper() != "OUI":
        print("Annulé.")
        return 6
    write_kill_flag(KILL_FLAG, level, "commande manuelle")
    print(f"Drapeau écrit ({KILL_FLAG}) : l'agent en cours d'exécution s'arrêtera au prochain cycle.")
    gw = _mt5_gateway()
    if gw is None:
        return 2
    await gw.connect()
    try:
        signer = ApprovalSigner()
        ks = KillSwitch(gw, Executor(gw, signer), RiskManager(AppConfig().risk, signer))
        report = await ks.activate(level, "commande manuelle")
        print("Actions :", *report.actions, sep="\n  - ")
        if report.failures:
            print("ÉCHECS (à traiter à la main dans MT5) :", *report.failures, sep="\n  - ")
            return 9
    finally:
        await gw.shutdown()
    return 0


def audit_cmd(tail: bool) -> int:
    from orionx.audit.log import AuditLog

    if not AUDIT_DB.exists():
        print(f"Aucun journal ({AUDIT_DB}).")
        return 2
    log = AuditLog(AUDIT_DB)
    if tail:
        for e in log.tail(30):
            print(e["id"], e["ts"], e["kind"], e["payload"])
        return 0
    r = log.verify()
    print(f"Journal : {r.checked} entrées vérifiées -> {'INTÈGRE' if r.ok else f'ALTÉRÉ ({r.reason}, ligne {r.first_bad_id})'}")
    return 0 if r.ok else 1


async def backtest_cmd(symbol: str, n_bars: int) -> int:
    from orionx.backtest.runner import run_backtest
    from orionx.strategies.trend_pullback import TrendPullback

    gw = _mt5_gateway()
    if gw is None:
        return 2
    await gw.connect()
    try:
        spec, bars = await gw.get_symbol_spec(symbol), await gw.get_bars(symbol, "H1", n_bars)
    finally:
        await gw.shutdown()
    print(f"{len(bars)} bougies H1 de {symbol} ({bars[0].time:%Y-%m-%d} -> {bars[-1].time:%Y-%m-%d})")
    r = run_backtest(bars, TrendPullback(), spec, symbol, spread_points=10, commission_per_lot=7.0)
    print(f"Trades : {r.trades} | gagnants : {r.win_rate:.0%} | profit factor : {r.profit_factor:.2f}")
    print(f"Résultat net : {r.net_profit:+.2f} | drawdown max : {r.max_drawdown_pct:.1f} % | capital final : {r.final_equity:.2f}")
    print("Résultats INDICATIFS (spread 1 pip, commission 7 $/lot). Ce n'est pas une promesse de gain.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orionx")
    parser.add_argument("command", choices=["simulate", "mt5-check", "demo-trade", "run", "kill", "clear-kill", "audit-verify", "audit-tail", "backtest"])
    parser.add_argument("--yes", action="store_true", help="demo-trade : ne pas demander confirmation")
    parser.add_argument("--hold", type=float, default=10.0, help="demo-trade : secondes avant fermeture")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--level", type=int, default=3, choices=[1, 2, 3], help="kill : niveau d'arrêt")
    parser.add_argument("--allow-auto", action="store_true", help="run : autorise l'exécution automatique des ordres")
    parser.add_argument("--cycle", type=float, default=1.0, help="run : secondes entre deux cycles")
    parser.add_argument("--max-cycles", type=int, default=None, help="run : s'arrêter après N cycles")
    parser.add_argument("--symbol", default="EURUSD", help="backtest : symbole")
    parser.add_argument("--bars", type=int, default=3000, help="backtest : nombre de bougies H1")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = _config(str(args.config) if args.config else None)
    if args.command == "simulate":
        return asyncio.run(simulate(cfg))
    if args.command == "run":
        try:
            return asyncio.run(run_agent(cfg, args.allow_auto, args.cycle, args.max_cycles))
        except KeyboardInterrupt:
            print("Arrêt demandé par l'utilisateur.")
            return 0
    if args.command == "kill":
        return asyncio.run(kill_cmd(args.level, args.yes))
    if args.command == "clear-kill":
        from orionx.monitoring.flag import clear_kill_flag

        clear_kill_flag(KILL_FLAG)
        print("Drapeau retiré. Redémarrez l'agent pour reprendre.")
        return 0
    if args.command in ("audit-verify", "audit-tail"):
        return audit_cmd(args.command == "audit-tail")
    if args.command == "backtest":
        return asyncio.run(backtest_cmd(args.symbol, args.bars))
    if args.command == "demo-trade":
        return asyncio.run(demo_trade(args.yes, args.hold))
    return asyncio.run(mt5_check())


if __name__ == "__main__":
    sys.exit(main())
