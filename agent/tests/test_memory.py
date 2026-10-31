"""Mémoire de marché : agrégation par minute, états, fiche des symboles, un fichier par courtier, rapport, et branchement sur l'agent."""

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orionx.agent import Agent
from orionx.connector_mt5.simulated import SimulatedGateway
from orionx.core.bus import EventBus
from orionx.core.config import AppConfig
from orionx.core.events import MarketStateUpdated, TickReceived
from orionx.core.models import Tick, utcnow
from orionx.memory.recorder import MarketMemory, memory_path, render_report
from tests.helpers import EURUSD
from tests.test_agent import NOWEEKEND, StubStrategy, new_bar

T0 = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)


def tick(sec: float, bid: float = 1.1000, ask: float = 1.1001, symbol: str = "EURUSD") -> TickReceived:
    t = T0 + timedelta(seconds=sec)
    return TickReceived(Tick(symbol, bid, ask, t, received_at=t))


def rows(db: MarketMemory, sql: str) -> list[tuple]:
    return db._db.execute(sql).fetchall()


class PathTests(unittest.TestCase):
    def test_one_file_per_broker_server_so_sources_never_mix(self) -> None:
        d = Path("data")
        a = memory_path(d, {"server": "ICMarketsEU-Demo"})
        b = memory_path(d, {"server": "MetaQuotes-Demo"})
        self.assertNotEqual(a, b)
        self.assertEqual(a.name, "icmarketseu-demo.db")
        self.assertEqual(memory_path(d, None).name, "inconnu.db")
        self.assertEqual(memory_path(d, {"server": "../../etc/passwd"}).parent, d / "memory", "pas d'évasion de dossier")


class AggregationTests(unittest.IsolatedAsyncioTestCase):
    async def test_minute_row_keeps_last_price_and_real_spread_stats(self) -> None:
        mem = MarketMemory(":memory:")
        await mem.on_tick(tick(1, 1.1000, 1.1002))  # spread 2 pts
        await mem.on_tick(tick(20, 1.1001, 1.1002))  # spread 1
        await mem.on_tick(tick(40, 1.1003, 1.1007))  # spread 4
        await mem.on_tick(tick(65, 1.1004, 1.1005))  # minute suivante : la première est écrite
        sql = "SELECT minute, ticks, bid, ask, spread_min, spread_avg, spread_max FROM quotes_1m"
        ((minute, n, bid, ask, smin, savg, smax),) = rows(mem, sql)
        self.assertEqual((minute, n, bid, ask), (T0.isoformat(), 3, 1.1003, 1.1007))
        self.assertAlmostEqual(smin, 0.0001, places=7)
        self.assertAlmostEqual(smax, 0.0004, places=7)
        self.assertAlmostEqual(savg, 0.0007 / 3, places=7)
        mem.close()

    async def test_flush_writes_the_open_minute_and_a_restart_merges_instead_of_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.db"
            first = MarketMemory(path)
            await first.on_tick(tick(1, 1.1000, 1.1002))
            await first.on_tick(tick(2, 1.1000, 1.1002))
            first.close()
            second = MarketMemory(path)  # redémarrage de l'agent dans la même minute
            await second.on_tick(tick(30, 1.1001, 1.1005))  # spread 4
            second.close()
            db = sqlite3.connect(path)
            ((n, avg, smax),) = db.execute("SELECT ticks, spread_avg, spread_max FROM quotes_1m").fetchall()
            db.close()
        self.assertEqual(n, 3)
        self.assertAlmostEqual(avg, (0.0002 + 0.0002 + 0.0004) / 3, places=7)
        self.assertAlmostEqual(smax, 0.0004, places=7)

    async def test_late_ticks_are_ignored_and_symbols_are_separate(self) -> None:
        mem = MarketMemory(":memory:")
        await mem.on_tick(tick(70, symbol="EURUSD"))
        await mem.on_tick(tick(5, symbol="EURUSD"))  # tick en retard d'une minute : ignoré
        await mem.on_tick(tick(10, symbol="GBPUSD"))
        mem.flush()
        got = rows(mem, "SELECT symbol, minute, ticks FROM quotes_1m ORDER BY symbol")
        self.assertEqual(got, [("EURUSD", (T0 + timedelta(minutes=1)).isoformat(), 1), ("GBPUSD", T0.isoformat(), 1)])
        mem.close()

    async def test_time_comes_from_the_local_observation_not_the_broker_clock(self) -> None:
        mem = MarketMemory(":memory:")
        broker_time = T0 + timedelta(hours=2)  # heure serveur (UTC+2) : ne doit PAS servir d'horodatage
        await mem.on_tick(TickReceived(Tick("EURUSD", 1.1, 1.1001, broker_time, received_at=T0)))
        mem.flush()
        self.assertEqual(rows(mem, "SELECT minute FROM quotes_1m"), [(T0.isoformat(),)])
        mem.close()


class StateAndSymbolTests(unittest.IsolatedAsyncioTestCase):
    async def test_market_states_and_symbol_sheet_are_recorded(self) -> None:
        fixed = datetime(2026, 10, 30, 9, 30, tzinfo=UTC)
        mem = MarketMemory(":memory:", {"server": "X", "company": "Y"}, specs={"EURUSD": EURUSD}, clock=lambda: fixed)
        state = MarketStateUpdated("EURUSD", "trend_up", "london", volatility_ratio=1.4, trend_strength=0.9, efficiency=0.31, atr=0.0012)
        await mem.on_state(state)
        await mem.on_tick(tick(1))
        self.assertEqual(rows(mem, "SELECT symbol, regime, session, atr, volatility_ratio FROM states"),
                         [("EURUSD", "trend_up", "london", 0.0012, 1.4)])  # fmt: skip
        ((pip, digits, vmin),) = rows(mem, "SELECT pip, digits, volume_min FROM symbols WHERE symbol='EURUSD'")
        self.assertAlmostEqual(pip, 0.0001)
        self.assertEqual((digits, vmin), (5, 0.01))
        self.assertEqual(dict(rows(mem, "SELECT key, value FROM meta"))["server"], "X")
        mem.close()

    async def test_attached_to_the_bus_it_listens_to_both_events(self) -> None:
        bus, mem = EventBus(), MarketMemory(":memory:")
        mem.attach(bus)
        await bus.publish(tick(1))
        state = MarketStateUpdated("EURUSD", "range", "asia", volatility_ratio=0.9, trend_strength=0.1, efficiency=0.2, atr=0.001)
        await bus.publish(state)
        mem.flush()
        self.assertEqual(len(rows(mem, "SELECT * FROM quotes_1m")), 1)
        self.assertEqual(len(rows(mem, "SELECT * FROM states")), 1)
        mem.close()


class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_gives_the_real_spread_by_hour_in_pips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.db"
            mem = MarketMemory(path, {"company": "IC Markets", "server": "ICMarketsEU-Demo", "margin_mode": "hedging", "currency": "EUR",
                                      "leverage": "30"}, specs={"EURUSD": EURUSD})  # fmt: skip
            for minute in range(60):  # 09h : spread constant de 0,2 pip
                await mem.on_tick(tick(minute * 60 + 1, 1.10000, 1.10002))
            for minute in range(60, 120):  # 10h : 1,0 pip
                await mem.on_tick(tick(minute * 60 + 1, 1.10000, 1.10010))
            mem.close()
            lines: list[str] = []
            self.assertEqual(render_report(path, out=lines.append), 0)
        text = "\n".join(lines)
        self.assertIn("IC Markets", text)
        self.assertIn("ICMarketsEU-Demo", text)
        self.assertIn("09h :    60 · 0.20", text)
        self.assertIn("10h :    60 · 1.00", text)
        self.assertIn("commission", text, "dit ce que la mesure ne contient pas")

    def test_an_empty_memory_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "e.db"
            MarketMemory(path).close()
            lines: list[str] = []
            self.assertEqual(render_report(path, out=lines.append), 2)
        self.assertIn("Aucune donnée", lines[-1])


class AgentIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_agent_feeds_the_memory_without_changing_its_behaviour(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gw = SimulatedGateway({"EURUSD": EURUSD})
            gw.set_tick("EURUSD", 1.08412, 1.08420, utcnow())
            gw.set_bars("EURUSD", "H1", [new_bar(i) for i in range(5)])
            cfg = AppConfig(symbols=("EURUSD",), autonomy="signals_only", risk=NOWEEKEND)
            agent = Agent(gw, cfg, strategy=StubStrategy(), kill_flag=Path(tmp) / "flag.json")
            mem = MarketMemory(Path(tmp) / "m.db", await gw.broker_info(), specs=agent.specs)
            mem.attach(agent.bus)
            await agent.start()
            await agent.run_cycle()
            gw.set_tick("EURUSD", 1.08430, 1.08440, utcnow())
            await agent.run_cycle()
            mem.close()
            db = sqlite3.connect(Path(tmp) / "m.db")
            n = db.execute("SELECT SUM(ticks) FROM quotes_1m WHERE symbol='EURUSD'").fetchone()[0]
            pip = db.execute("SELECT pip FROM symbols WHERE symbol='EURUSD'").fetchone()
            db.close()
        self.assertGreaterEqual(n, 2)
        self.assertIsNotNone(pip)


if __name__ == "__main__":
    unittest.main()
