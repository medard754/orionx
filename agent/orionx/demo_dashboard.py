"""Démonstration du dashboard SANS MetaTrader 5 : courtier simulé, prix qui bougent, signal à approuver, événements.

Tout est simulé et étiqueté comme tel dans le dashboard (pastille « DÉMO SIMULÉE »). Rien n'est lu ni écrit dans vos vraies
données : le drapeau d'arrêt d'urgence, le journal et les bases de la démonstration vivent dans un dossier temporaire.
Aucun ordre n'atteint un courtier réel : le seul courtier est `SimulatedGateway`.
"""

from __future__ import annotations

import asyncio
import random
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from orionx.agent import Agent
from orionx.analysis.calendar import EconomicCalendar, EconomicEvent
from orionx.api.feed import EventFeed
from orionx.audit.log import AuditLog
from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.config import AppConfig, RiskConfig
from orionx.core.models import Bar, Side, SymbolSpec, TradeProposal, utcnow
from orionx.decision.registry import build_engine

SPEC = SymbolSpec("EURUSD", 5, 1e-5, 1e-5, 1.0, 0.01, 100.0, 0.01)
START = 1.0850
PIP = 1e-4


def make_bars(n: int, seed: int = 11, start: float = START, vol: float = 0.0006) -> list[Bar]:
    """Marche aléatoire par régimes (tendance, range), bougies H1 valides, la dernière proche de maintenant."""
    rnd, price, drift, out = random.Random(seed), start, 0.0, []
    end = utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    for i in range(n):
        if i % 90 == 0:
            drift = rnd.choice([-1, 0, 1]) * vol * 0.25
        o = price
        c = max(0.5, o + drift + rnd.gauss(0, vol))
        h = max(o, c) + abs(rnd.gauss(0, vol * 0.5))
        lo = min(o, c) - abs(rnd.gauss(0, vol * 0.5))
        out.append(Bar(end - timedelta(hours=n - 1 - i), round(o, 5), round(h, 5), round(lo, 5), round(c, 5), 100.0))
        price = c
    return out


@dataclass
class DemoWorld:
    agent: Agent
    gateway: SimulatedGateway
    feed: EventFeed
    kill_flag: Path
    rnd: random.Random
    steps: int = 0


def new_signal(world: DemoWorld) -> None:
    """Un signal d'achat ou de vente autour du prix courant, valable 10 minutes (jamais plus d'un en attente)."""
    tick = world.gateway._ticks["EURUSD"]
    now, side = utcnow(), world.rnd.choice([Side.BUY, Side.SELL])
    buy = side is Side.BUY
    entry = tick.ask if buy else tick.bid
    sl = entry - 20 * PIP if buy else entry + 20 * PIP
    tp = entry + 40 * PIP if buy else entry - 40 * PIP
    pid = f"demo-{int(now.timestamp())}"
    world.agent.pending.clear()
    world.agent.pending[pid] = TradeProposal(
        pid, "EURUSD", side, round(sl, 5), round(tp, 5), round(0.55 + world.rnd.random() * 0.3, 2), "trend_pullback",
        ("démonstration : signal simulé", "tendance et repli"), now, now + timedelta(minutes=10))  # fmt: skip


async def build_demo(workdir: Path) -> DemoWorld:
    rnd = random.Random(5)
    bars = make_bars(300)
    gw = SimulatedGateway({"EURUSD": SPEC}, balance=100_000.0)
    last = bars[-1].close
    gw.set_tick("EURUSD", last, round(last + 0.00008, 5), utcnow())
    gw.set_bars("EURUSD", "H1", bars)
    await gw.send_market_order("EURUSD", Side.BUY, 0.40, round(last - 0.0030, 5), round(last + 0.0060, 5), 10, "demo")
    await gw.send_market_order("EURUSD", Side.SELL, 0.20, round(last + 0.0035, 5), round(last - 0.0050, 5), 10, "demo")
    now = utcnow()
    calendar = EconomicCalendar([EconomicEvent(now + timedelta(minutes=95), "USD", "high", "Indice des prix à la consommation (CPI)"),
                                 EconomicEvent(now + timedelta(hours=5), "EUR", "medium", "Discours de la BCE")])  # fmt: skip
    cfg = AppConfig(symbols=("EURUSD",), autonomy="semi_auto", risk=RiskConfig(weekend_guard=False))
    feed, flag = EventFeed(), workdir / "KILL_SWITCH.json"
    engine = build_engine(("trend_pullback", "mean_reversion", "breakout"), cfg.decision, cfg.analysis)
    agent = Agent(gw, cfg, engine=engine, audit=AuditLog(workdir / "audit.db"), calendar=calendar, kill_flag=flag, feed=feed)
    agent.simulated = True
    await agent.start()
    await agent.run_cycle()
    world = DemoWorld(agent, gw, feed, flag, rnd)
    new_signal(world)
    return world


async def step_market(world: DemoWorld) -> None:
    """Un pas de la démonstration : le prix bouge, l'agent fait un cycle, de temps en temps un événement ou un nouveau signal."""
    world.steps += 1
    tick = world.gateway._ticks["EURUSD"]
    mid = (tick.bid + tick.ask) / 2 + world.rnd.gauss(0, 0.00005) + 0.000002 * (START - tick.bid) * 40
    spread = 0.00006 + world.rnd.random() * 0.00004
    world.gateway.set_tick("EURUSD", round(mid - spread / 2, 5), round(mid + spread / 2, 5), utcnow())
    await world.agent.run_cycle()
    if world.steps % 20 == 0:
        world.feed.handle("DataRecovered", {"symbol": "EURUSD"})
    if world.steps % 120 == 0 or not world.agent.pending:
        new_signal(world)
        world.feed.handle("DecisionNote", {"symbol": "EURUSD", "notes": ["signal simulé"]})


async def run_demo(port: int = 8765, open_browser: bool = True) -> int:
    import webbrowser

    from orionx.api import server as api

    workdir = Path(tempfile.mkdtemp(prefix="orionx_demo_"))
    world = await build_demo(workdir)
    sessions, stop = api.Sessions(), asyncio.Event()
    app = api.create_app(world.agent, api.new_token(), world.kill_flag, world.feed, port=port, sessions=sessions)
    server = asyncio.create_task(api.serve(app, port, stop))
    await asyncio.sleep(1.0)
    url = f"http://127.0.0.1:{port}/login?code={sessions.issue_code()}"
    print(f"DÉMONSTRATION : agent et courtier SIMULÉS, aucun MetaTrader, aucun ordre réel. Dashboard : http://127.0.0.1:{port}")
    print("Cliquez sur les onglets d'analyse, approuvez le signal, essayez l'arrêt d'urgence. Ctrl+C pour arrêter.")
    if open_browser:
        webbrowser.open(url)
    try:
        while True:
            await step_market(world)
            await asyncio.sleep(0.5)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        stop.set()
        await asyncio.wait_for(server, 10)
        print("Démonstration arrêtée.")
    return 0
