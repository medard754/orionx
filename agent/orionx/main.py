"""Point d'entrée en ligne de commande.

  python -m orionx.main simulate          # scénario complet sur courtier simulé (sans MT5)
  python -m orionx.main mt5-check         # lecture seule d'un compte DÉMO MT5 (aucun ordre)
  python -m orionx.main demo-trade        # T-02 : UN ordre démo de 0,01 lot, puis fermeture
  python -m orionx.main run               # boucle de l'agent (signaux seuls par défaut)
  python -m orionx.main run --api         # T-14 : + API locale (approbation des signaux, état, flux en direct)
  python -m orionx.main run --api --dashboard   # M9 : + ouvre le dashboard dans le navigateur
  python -m orionx.main dashboard         # M9 : rouvre le dashboard d'un agent lancé avec --api
  python -m orionx.main kill --level 3    # arrêt d'urgence depuis un autre terminal
  python -m orionx.main clear-kill        # retire le drapeau d'arrêt d'urgence
  python -m orionx.main audit-verify      # vérifie l'intégrité du journal
  python -m orionx.main backtest          # backtest de la stratégie sur l'historique MT5 (lecture seule)
  python -m orionx.main backtest --h1     # protocole H-1/V-3 : développement seul (docs/validation/H-1_protocole.md)
  python -m orionx.main risk-set --profile moderate   # changer de profil de risque (hausse = délai de 24 h)
  python -m orionx.main risk-status       # configuration de risque active / en attente
  python -m orionx.main risk-check --symbol EURUSD --side buy   # que déciderait le risque ? (aucun ordre)
  python -m orionx.main state             # état du marché (régime, séance, volatilité) et annonces à venir
  python -m orionx.main alert-setup       # M8 : configure le bot Telegram (jeton dans le coffre Windows)
  python -m orionx.main alert-test        # M8 : envoie un message de test
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.bus import EventBus
from orionx.core.config import AppConfig, load_config
from orionx.core.events import OrderFilled, TradeApproved, TradeRejected
from orionx.core.models import Side, SymbolSpec, TradeProposal, utcnow
from orionx.execution.executor import Executor
from orionx.risk.approval import ApprovalSigner
from orionx.risk.manager import RiskManager

if TYPE_CHECKING:
    from orionx.alerts.router import AlertRouter
    from orionx.connector_mt5.mt5_real import Mt5Gateway

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


def _mt5_gateway() -> Mt5Gateway | None:
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


async def run_agent(cfg: AppConfig, allow_auto: bool, cycle: float, max_cycles: int | None, api_port: int | None = None,
                    open_dashboard: bool = False) -> int:
    from orionx.agent import Agent
    from orionx.audit.log import AuditLog
    from orionx.data.engine import MarketStore
    from orionx.decision.registry import build_engine
    from orionx.monitoring.flag import read_kill_flag
    from orionx.risk.guard import RiskParamGuard

    gw = _mt5_gateway()
    if gw is None:
        return 2
    from orionx.analysis.calendar import EconomicCalendar

    audit = AuditLog(AUDIT_DB)
    calendar = None
    if cfg.analysis.calendar_path:
        calendar = EconomicCalendar.from_csv(cfg.analysis.calendar_path)
        print(f"Calendrier économique : {len(calendar.events)} événements chargés ({cfg.analysis.calendar_path}).")
    else:
        print("Aucun calendrier économique configuré : pas de blocage autour des annonces (analysis.calendar_path).")
    feed = None
    if api_port:
        try:
            from orionx.api import server as api
            from orionx.api.feed import EventFeed
        except ImportError:
            print('API locale indisponible : pip install -e ".[api]" (ou relancez sans --api).')
            return 2
        feed = EventFeed()
    alerts = _alert_router(cfg)
    await gw.connect()
    engine = build_engine(cfg.strategies, cfg.decision, cfg.analysis)
    agent = Agent(gw, cfg, engine=engine, risk_guard=RiskParamGuard(DATA_DIR), audit=audit, store=MarketStore(DATA_DIR / "market.db"),
                  calendar=calendar, allow_auto=allow_auto, kill_flag=KILL_FLAG, alerts=alerts, feed=feed)  # fmt: skip
    print(f"Stratégies activées : {list(cfg.strategies)} · profil de risque actif : {agent.risk.cfg.risk_per_trade_pct} % par trade.")
    print(f"Agent démarré : autonomie={agent.autonomy}, symboles={cfg.symbols}. Ctrl+C pour arrêter.")
    print("Les positions ouvertes restent protégées par leur stop-loss même si l'agent s'arrête.")
    flag = read_kill_flag(KILL_FLAG)
    if flag:  # un drapeau oublié est appliqué dès le 1er cycle (niveau 3 : clôture de toutes les positions)
        print(f"ATTENTION : drapeau d'arrêt d'urgence présent (niveau {flag.get('level')}, « {flag.get('reason', '')} », "
              f"posé le {flag.get('at', '?')}) : il sera appliqué dès le premier cycle. Pour le lever : python -m orionx.main clear-kill")
    api_stop, api_task = asyncio.Event(), None
    if api_port:
        token_file = DATA_DIR / "api_token"
        token = api.new_token()  # nouveau jeton à chaque démarrage
        api.write_token(token_file, token)
        sessions = api.Sessions()
        app = api.create_app(agent, token, KILL_FLAG, feed, port=api_port, sessions=sessions)
        api_task = asyncio.create_task(api.serve(app, api_port, api_stop))
        print(f"API locale : http://127.0.0.1:{api_port}/api/status (jeton dans {token_file}, jamais affiché ici).")
        if open_dashboard:
            await asyncio.sleep(1.0)  # le temps que le serveur écoute
            _open_browser(api_port, sessions.issue_code())
    try:
        await agent.run(asyncio.Event(), cycle_seconds=cycle, max_cycles=max_cycles)
    finally:
        if api_task:
            api_stop.set()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(api_task, timeout=5)
        await gw.shutdown()
        if alerts:
            alerts.close()  # laisse partir les dernières alertes (au plus 30 s, réseau lent)
        print("Agent arrêté.")
    if agent.aborted:
        print(f"ARRÊT DÉFINITIF : {agent.aborted}. Vérifiez le compte du terminal MT5, puis relancez l'agent.")
        return 4
    return 0


def _open_browser(port: int, code: str) -> None:
    import webbrowser

    # Code à usage unique (60 s) échangé contre un cookie de session : le navigateur ne voit jamais le jeton.
    webbrowser.open(f"http://127.0.0.1:{port}/login?code={code}")
    print(f"Dashboard ouvert dans le navigateur (http://127.0.0.1:{port}). Pour le rouvrir : python -m orionx.main dashboard")


def dashboard_cmd(port: int) -> int:
    """Rouvre le dashboard d'un agent déjà lancé avec --api (nouveau code de connexion à usage unique)."""
    import json
    import urllib.error
    import urllib.request

    token_file = DATA_DIR / "api_token"
    if not token_file.exists():
        print("Aucun agent avec API : lancez d'abord « python -m orionx.main run --config config.yaml --api ».")
        return 2
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/session/code", method="POST",
                                 headers={"Authorization": f"Bearer {token_file.read_text(encoding='utf-8').strip()}"})  # fmt: skip
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            code = json.loads(resp.read().decode("utf-8"))["code"]
    except urllib.error.HTTPError as exc:
        print(f"L'API a refusé ({exc.code}) : l'agent a sans doute redémarré avec un nouveau jeton ; relancez cette commande.")
        return 3
    except OSError:
        print(f"Aucune API sur le port {port} : l'agent est-il lancé avec --api ?")
        return 3
    _open_browser(port, code)
    return 0


def _alert_router(cfg: AppConfig) -> AlertRouter | None:
    from orionx.alerts.router import AlertRouter
    from orionx.alerts.telegram import TelegramNotifier, load_credentials

    creds = load_credentials()
    if creds is None:
        print("Alertes Telegram : désactivées (aucun identifiant ; voir « alert-setup »).")
        return None
    signals = "oui" if cfg.alerts.signals else "non"
    print(f"Alertes Telegram : activées (niveau minimal {cfg.alerts.min_level}, signaux : {signals}).")
    return AlertRouter(TelegramNotifier(*creds), cfg.alerts)


def alert_cmd(command: str, visible: bool = False) -> int:
    import getpass

    from orionx.alerts.telegram import (
        AlertError,
        TelegramNotifier,
        check_token,
        clean_token,
        keyring_module,
        load_credentials,
        store_credentials,
    )

    if command == "alert-test":
        creds = load_credentials()
        if creds is None:
            print("Aucun identifiant Telegram : lancez d'abord « alert-setup ».")
            return 2
        try:
            TelegramNotifier(*creds).send("✅ ORIONX : test d'alerte réussi. Les alertes de l'agent arriveront ici.")
        except AlertError as exc:
            print(f"Échec : {exc}")
            return 3
        print("Message de test envoyé : vérifiez Telegram.")
        return 0
    if keyring_module() is None:  # vérifié AVANT la saisie : ne jamais faire taper un jeton pour rien
        print("Coffre Windows indisponible. Installez-le d'abord, puis relancez « alert-setup » :")
        print('  pip install -e ".[alerts]"')
        return 2
    print("1. Dans Telegram, ouvrez @BotFather, envoyez /newbot et suivez les étapes : il vous donne un JETON.")
    print("2. Ouvrez la conversation avec votre nouveau bot et envoyez-lui /start.")
    if visible:  # le collage dans une saisie invisible échoue souvent sous Windows ; rien n'est enregistré en clair
        raw = input("3. Collez le jeton (il s'affiche à l'écran, effacez-le ensuite avec cls) : ")
    else:
        raw = getpass.getpass("3. Collez le jeton (la saisie reste invisible ; clic droit pour coller) : ")
    token, note = clean_token(raw)  # espaces, guillemets, jeton collé deux fois
    if note:
        print(note)
    if not token:
        print("Annulé.")
        return 2
    problem = check_token(token)
    if problem:
        print(problem)
        print("Autre méthode : python -m orionx.main alert-setup --visible")
        return 3
    try:
        chat = TelegramNotifier(token, "").discover_chat_id()
        if chat is None:
            print("Aucun message trouvé : envoyez /start à votre bot, puis relancez « alert-setup ».")
            return 3
        store_credentials(token, chat)
        TelegramNotifier(token, chat).send("✅ ORIONX : bot configuré. Les alertes de l'agent arriveront ici.")
    except AlertError as exc:
        print(f"Échec : {exc}")
        return 3
    print("Identifiants enregistrés dans le coffre Windows (jamais dans le dépôt). Message de test envoyé.")
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
        from orionx.audit.log import AuditLog

        AuditLog(AUDIT_DB).append("ManualKill", {"level": level, "actions": report.actions, "failures": report.failures})
        print("Actions :", *report.actions, sep="\n  - ")
        if report.failures:
            print("ÉCHECS (à traiter à la main dans MT5) :", *report.failures, sep="\n  - ")
            return 9
    finally:
        await gw.shutdown()
    return 0


async def riskcheck_cmd(cfg: AppConfig, symbol: str, side: str, sl_pips: float) -> int:
    from orionx.core.models import Side
    from orionx.risk.guard import RiskParamGuard
    from orionx.riskcheck import check_proposal

    risk_cfg = RiskParamGuard(DATA_DIR).preview(cfg.risk)  # la config que l'agent utiliserait, pas config.yaml
    gw = _mt5_gateway()
    if gw is None:
        return 2
    await gw.connect()
    try:
        return await check_proposal(gw, cfg, symbol, Side(side), sl_pips, risk_cfg=risk_cfg)
    finally:
        await gw.shutdown()


def risk_cmd(command: str, profile: str | None) -> int:
    from orionx.core.config import risk_from_profile
    from orionx.risk.guard import RiskParamGuard

    guard = RiskParamGuard(DATA_DIR)
    if command == "risk-status":
        active, pending = guard.active(), guard.pending()
        print("Configuration de risque ACTIVE :", active if active else "aucune (l'agent n'a jamais démarré)")
        print("Changement en attente :", f"appliqué à partir de {pending[1]:%d/%m/%Y %H:%M} UTC -> {pending[0]}" if pending else "aucun")
        return 0
    if not profile:
        print("Précisez --profile prudent|moderate|dynamic")
        return 2
    change = guard.request(risk_from_profile(profile))
    when = f" (à partir de {change.effective_at:%d/%m/%Y %H:%M} UTC)" if change.effective_at else ""
    print(f"Profil « {profile} » : {change.status}{when} - {change.note}.")
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


async def state_cmd(cfg: AppConfig) -> int:
    from orionx.analysis.calendar import EconomicCalendar
    from orionx.analysis.state import analyze
    from orionx.analysis.tzutil import server_to_utc_eet

    gw = _mt5_gateway()
    if gw is None:
        return 2
    cal = EconomicCalendar.from_csv(cfg.analysis.calendar_path) if cfg.analysis.calendar_path else None
    await gw.connect()
    try:
        for sym in cfg.symbols:
            bars = await gw.get_bars(sym, "H1", cfg.analysis.vol_window + 60)
            st = analyze(sym, bars, cfg.analysis, server_to_utc_eet)
            print(f"{sym} : régime={st.regime.value} · séance={st.session.value} · volatilité x{st.volatility_ratio:.2f} "
                  f"· force de tendance {st.trend_strength:+.2f} · efficacité {st.efficiency:.2f} · ATR {st.atr:.5f}")
            if cal:
                for e in cal.upcoming(sym, utcnow(), 24, cfg.analysis.news_min_impact):
                    print(f"   annonce {e.time:%d/%m %H:%M} UTC · {e.currency} · {e.impact} · {e.title}")
    finally:
        await gw.shutdown()
    return 0


def make_strategy(spec: str, cfg: AppConfig):  # type: ignore[no-untyped-def]
    """`trend_pullback` (une stratégie seule, sans régime) · `engine:a,b` (moteur avec régimes) · `engine` (stratégies du config)."""
    from orionx.decision.registry import AVAILABLE, build_engine

    if spec.startswith("engine"):
        names = tuple(n for n in spec.partition(":")[2].split(",") if n) or cfg.strategies
        return build_engine(names, cfg.decision, cfg.analysis).as_strategy()
    if spec not in AVAILABLE:
        raise SystemExit(f"Stratégie inconnue : {spec}. Disponibles : {list(AVAILABLE)} ou engine[:a,b]")
    return AVAILABLE[spec][0]()


async def backtest_cmd(symbol: str, n_bars: int, folds: int, csv_path: Path | None, filters: str, random_runs: int,
                       strategy: str = "trend_pullback", cfg: AppConfig | None = None) -> int:  # fmt: skip
    import csv

    from orionx.analysis.filters import FILTERS
    from orionx.backtest.runner import BacktestParams, run_backtest
    from orionx.backtest.walkforward import random_reference, split_folds
    cfg = cfg or AppConfig()
    make_strategy(strategy, cfg)  # échoue tout de suite si le nom est inconnu

    names = [f.strip() for f in filters.split(",") if f.strip()]
    unknown = [n for n in names if n not in FILTERS]
    if unknown:
        print(f"Filtres inconnus : {unknown}. Disponibles : {list(FILTERS)}")
        return 2
    gw = _mt5_gateway()
    if gw is None:
        return 2
    await gw.connect()
    try:
        spec, bars = await gw.get_symbol_spec(symbol), await gw.get_bars(symbol, "H1", n_bars)
    finally:
        await gw.shutdown()
    print(f"{len(bars)} bougies H1 de {symbol} ({bars[0].time:%Y-%m-%d} -> {bars[-1].time:%Y-%m-%d}) · stratégie : {strategy}")
    if len(bars) < n_bars:
        print(f"Attention : MetaTrader n'a fourni que {len(bars)} bougies sur {n_bars} demandées.")

    def line(label: str, r) -> str:  # type: ignore[no-untyped-def]
        return (f"{label:<14} trades={r.trades:>4}  gagnants={r.win_rate:>4.0%}  PF={r.profit_factor:>5.2f}  "
                f"net={r.net_profit:>+9.2f}  DD={r.max_drawdown_pct:>5.1f} %")  # fmt: skip

    summary, first_total = [], None
    for name in names:
        costs: BacktestParams = {"spread_points": 10.0, "commission_per_lot": 7.0, "entry_filter": FILTERS[name]}
        total = run_backtest(bars, make_strategy(strategy, cfg), spec, symbol, **costs)
        first_total = first_total or total
        print(f"\n=== FILTRE « {name} » ===\n " + line("total", total) + f"   (signaux écartés : {total.filtered})")
        if folds > 1 and len(bars) // folds >= 600:
            for i, part in enumerate(split_folds(bars, folds), 1):
                block = run_backtest(part, make_strategy(strategy, cfg), spec, symbol, **costs)
                print(" " + line(f"bloc {i} ({part[0].time:%m-%d})", block))
        ref = random_reference(bars, spec, symbol, total.net_profit, runs=random_runs, **costs)
        print(f"  témoin aléatoire ({ref.runs} tirages, même filtre, mêmes coûts) : net moyen {ref.mean_net:+.0f} "
              f"(écart-type {ref.std_net:.0f}), PF moyen {ref.mean_pf:.2f} -> stratégie meilleure que {ref.share_worse:.0%} des tirages")
        summary.append((name, total, ref))
    print("\nRÉSUMÉ\n" + f"{'filtre':<10}{'trades':>8}{'PF':>7}{'net':>11}{'DD %':>7}{'bat le hasard':>15}")
    for name, t, r in summary:
        print(f"{name:<10}{t.trades:>8}{t.profit_factor:>7.2f}{t.net_profit:>+11.0f}{t.max_drawdown_pct:>7.1f}{r.share_worse:>14.0%}")
    print("\nRésultats INDICATIFS (spread 1 pip, commission 7 $/lot, un symbole, une période). Comparer des filtres sur les MÊMES")
    print("données les favorise : un filtre n'est retenu que s'il bat le hasard sur des données jamais regardées.")
    if csv_path and first_total:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ouverture", "sens", "entree", "sortie", "lots", "profit", "raison"])
            for trade in first_total.log:
                w.writerow([trade.opened, trade.side, trade.entry, trade.exit, trade.volume, round(trade.profit, 2), trade.reason])
        print(f"Détail des trades (premier filtre) : {csv_path}")
    return 0


async def h1_cmd(symbol: str, open_holdout: str | None, cfg: AppConfig) -> int:
    """Protocole H-1/V-3 (docs/validation/H-1_protocole.md) : développement seul, réserve ouverte une fois."""
    import hashlib

    from orionx.audit.log import AuditLog
    from orionx.backtest import h1

    snap = DATA_DIR / f"h1_{symbol}_H1.csv"
    gw = None
    if not snap.exists():  # première exécution : on fige les données (sinon la fenêtre glisse et rien ne se reproduit)
        gw = _mt5_gateway()
        if gw is None:
            return 2
        await gw.connect()
        try:
            h1.save_snapshot(snap, await gw.get_bars(symbol, "H1", 30_000))
        finally:
            await gw.shutdown()
        print(f"Instantané des données créé : {snap}")
    bars = h1.load_snapshot(snap)
    gw = _mt5_gateway()
    if gw is None:
        return 2
    await gw.connect()
    try:
        spec = await gw.get_symbol_spec(symbol)
    finally:
        await gw.shutdown()
    dev, hold = h1.split_holdout(bars)
    digest = hashlib.sha256(snap.read_bytes()).hexdigest()[:16]
    print(f"Instantané {snap.name} sha256={digest} : {len(bars)} bougies ({bars[0].time:%Y-%m-%d} -> {bars[-1].time:%Y-%m-%d})")
    print(f"Développement : {len(dev)} bougies ({dev[0].time:%Y-%m-%d} -> {dev[-1].time:%Y-%m-%d}) · "
          f"réserve : {len(hold)} bougies ({hold[0].time:%Y-%m-%d} -> {hold[-1].time:%Y-%m-%d}), NON évaluée ici")  # fmt: skip
    make = lambda: make_strategy("trend_pullback", cfg)  # noqa: E731
    reports = h1.run_dev(dev, spec, symbol, make)

    def line(label: str, r) -> str:  # type: ignore[no-untyped-def]
        return (f"{label:<12} trades={r.trades:>4}  gagnants={r.win_rate:>4.0%}  PF={r.profit_factor:>5.2f}  "
                f"net={r.net_profit:>+9.2f}  DD={r.max_drawdown_pct:>5.1f} %")  # fmt: skip

    for r in reports:
        role = "RÉFÉRENCE" if r.name == "none" else "hypothèse"
        print()
        print(f"=== {r.name} ({role}) ===")
        print(" " + line("développement", r.total))
        for i, f in enumerate(r.folds, 1):
            print("  " + line(f"bloc {i}", f))
        for c in r.verdict.criteria:
            print(f"   [{'OK' if c.passed else 'ÉCHEC'}] {c.name}: {c.detail}")
    cand = h1.pick_candidate(reports)
    print()
    print("RÉSUMÉ DÉVELOPPEMENT")
    print(f"{'filtre':<10}{'trades':>8}{'PF':>7}{'net':>11}{'DD %':>7}{'bat le hasard':>15}  critères")
    for r in reports:
        print(f"{r.name:<10}{r.total.trades:>8}{r.total.profit_factor:>7.2f}{r.total.net_profit:>+11.0f}{r.total.max_drawdown_pct:>7.1f}"
              f"{r.ref.share_worse:>14.0%}  {'TOUS PASSÉS' if r.verdict.passed else 'échec'}")  # fmt: skip
    for name in ("none", cand.name if cand else None):
        if name:
            rep = next(r for r in reports if r.name == name)
            print()
            print(f"TRADES PERDANTS « {name} » (descriptif : ne sert jamais à régler un paramètre)")
            for dim, groups in h1.loser_breakdown(rep.total.log).items():
                cells = (f"{k} {g.losers}/{g.trades} perdants net {g.net:+.0f}" for k, g in sorted(groups.items()))
                print("  " + dim + " : " + " · ".join(cells))
    if cand is None:
        print()
        print("Aucune hypothèse ne passe tous les critères : la réserve reste FERMÉE. Décision : NO-GO.")
        return 0 if not open_holdout else 3
    print()
    print(f"Candidat désigné par la règle (§6) : « {cand.name} ».")
    if not open_holdout:
        print("Réserve fermée. Pour l'ouvrir (UNE seule fois) : backtest --h1 --open-holdout " + cand.name)
        return 0
    if open_holdout != cand.name:
        print(f"Refusé : seul « {cand.name} » peut être présenté à la réserve (règle §6), pas « {open_holdout} ».")
        return 3
    try:
        h1.open_holdout_once(DATA_DIR / "h1_holdout_opened.json", cand.name, AuditLog(AUDIT_DB))
    except h1.HoldoutAlreadyOpened as exc:
        print(f"Refusé : {exc}")
        return 3
    held, verdict = h1.run_holdout(hold, spec, symbol, make, cand.name)
    print()
    print("=== RÉSERVE (ouverture unique) ===")
    print(" " + line(cand.name, held))
    for c in verdict.criteria:
        print(f"   [{'OK' if c.passed else 'ÉCHEC'}] {c.name}: {c.detail}")
    print()
    go = "GO pour V-4 (une semaine de signaux seuls ; n'autorise PAS l'exécution automatique)."
    print("Décision : " + (go if verdict.passed else "NO-GO (échec ou résultat non concluant sur la réserve)."))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orionx")
    commands = ["simulate", "mt5-check", "demo-trade", "run", "kill", "clear-kill", "audit-verify", "audit-tail",
                "backtest", "state", "risk-set", "risk-status", "risk-check", "alert-setup", "alert-test", "dashboard"]  # fmt: skip
    parser.add_argument("command", choices=commands)
    parser.add_argument("--yes", action="store_true", help="demo-trade : ne pas demander confirmation")
    parser.add_argument("--hold", type=float, default=10.0, help="demo-trade : secondes avant fermeture")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--level", type=int, default=3, choices=[1, 2, 3], help="kill : niveau d'arrêt")
    parser.add_argument("--allow-auto", action="store_true", help="run : autorise l'exécution automatique des ordres")
    parser.add_argument("--cycle", type=float, default=1.0, help="run : secondes entre deux cycles")
    parser.add_argument("--max-cycles", type=int, default=None, help="run : s'arrêter après N cycles")
    parser.add_argument("--api", action="store_true", help="run : démarre l'API locale (127.0.0.1 seulement, jeton obligatoire)")
    parser.add_argument("--api-port", type=int, default=8765, help="run --api, dashboard : port de l'API locale")
    parser.add_argument("--dashboard", action="store_true", help="run --api : ouvre le dashboard dans le navigateur")
    parser.add_argument("--symbol", default="EURUSD", help="backtest : symbole")
    parser.add_argument("--bars", type=int, default=3000, help="backtest : nombre de bougies H1")
    parser.add_argument("--folds", type=int, default=4, help="backtest : nombre de périodes consécutives")
    parser.add_argument("--side", default="buy", choices=["buy", "sell"], help="risk-check : sens simulé")
    parser.add_argument("--sl-pips", type=float, default=20.0, help="risk-check : distance du stop en pips")
    parser.add_argument("--profile", default=None, help="risk-set : prudent | moderate | dynamic")
    parser.add_argument("--strategy", default="trend_pullback", help="backtest : stratégie, ou engine[:a,b] (moteur avec régimes)")
    parser.add_argument("--filters", default="none", help="backtest : filtres à comparer, ex. none,vol,session,trend,all")
    parser.add_argument("--random-runs", type=int, default=20, help="backtest : nombre de tirages du témoin aléatoire")
    parser.add_argument("--csv", type=Path, default=None, help="backtest : exporte les trades en CSV")
    parser.add_argument("--h1", action="store_true", help="backtest : protocole H-1/V-3 (réserve de 6 mois jamais évaluée ici)")
    parser.add_argument("--visible", action="store_true", help="alert-setup : saisie du jeton visible (si le collage invisible échoue)")
    parser.add_argument("--open-holdout", default=None, metavar="FILTRE",
                        help="backtest --h1 : ouvre la réserve UNE fois, pour le candidat désigné")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = _config(str(args.config) if args.config else None)
    if args.command == "simulate":
        return asyncio.run(simulate(cfg))
    if args.command == "run":
        try:
            return asyncio.run(run_agent(cfg, args.allow_auto, args.cycle, args.max_cycles, args.api_port if args.api else None,
                                         args.dashboard))
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
    if args.command == "risk-check":
        return asyncio.run(riskcheck_cmd(cfg, args.symbol, args.side, args.sl_pips))
    if args.command in ("risk-set", "risk-status"):
        return risk_cmd(args.command, args.profile)
    if args.command == "state":
        return asyncio.run(state_cmd(cfg))
    if args.command == "backtest":
        if args.h1:
            return asyncio.run(h1_cmd(args.symbol, args.open_holdout, cfg))
        return asyncio.run(backtest_cmd(args.symbol, args.bars, args.folds, args.csv, args.filters, args.random_runs, args.strategy, cfg))
    if args.command == "dashboard":
        return dashboard_cmd(args.api_port)
    if args.command in ("alert-setup", "alert-test"):
        return alert_cmd(args.command, args.visible)
    if args.command == "demo-trade":
        return asyncio.run(demo_trade(args.yes, args.hold))
    return asyncio.run(mt5_check())


if __name__ == "__main__":
    sys.exit(main())
