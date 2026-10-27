"""API locale (T-14) : état de l'agent, signaux en attente, approbation humaine, arrêt d'urgence, flux d'événements.

Sécurité (ADR 0007) : écoute UNIQUEMENT 127.0.0.1 ; jeton aléatoire obligatoire (Authorization: Bearer, ou ?token= pour le
WebSocket) ; en-tête Host vérifié (anti DNS rebinding) ; aucune règle CORS (une page web ne peut pas appeler l'API) ;
aucun journal des requêtes (le jeton n'y apparaît jamais). L'API n'a AUCUN pouvoir propre : l'approbation passe par
Agent.approve (risque ré-évalué, ApprovedOrder signé, journal d'audit), l'arrêt d'urgence par le drapeau habituel.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from orionx.api.feed import EventFeed
from orionx.monitoring.flag import write_kill_flag

if TYPE_CHECKING:
    from orionx.agent import Agent

HOST = "127.0.0.1"


class PositionOut(BaseModel):
    ticket: int
    symbol: str
    side: str
    volume: float
    price_open: float
    sl: float | None
    tp: float | None
    profit: float


class StatusOut(BaseModel):
    autonomy: str
    cycles: int
    aborted: str | None
    halts: list[str]
    pending_signals: int
    equity: float | None
    balance: float | None
    is_demo: bool | None
    positions: list[PositionOut]


class SignalOut(BaseModel):
    id: str
    symbol: str
    side: str
    sl: float
    tp: float
    confidence: float
    strategy: str
    rationale: list[str]
    valid_until: datetime
    expired: bool


class DecisionOut(BaseModel):
    ok: bool
    message: str = ""
    ticket: int | None = None
    price: float | None = None


class KillIn(BaseModel):
    level: int = Field(ge=1, le=3, description="1 pause, 2 protection des positions, 3 clôture totale")
    reason: str = Field(default="API locale", max_length=200)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def write_token(path: Path, token: str) -> None:
    """Le jeton va dans data/ (hors du dépôt), jamais dans la console : un copier-coller de logs ne le divulgue pas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")


def create_app(agent: Agent, token: str, kill_flag: Path, feed: EventFeed | None = None) -> FastAPI:
    app = FastAPI(title="ORIONX - API locale", docs_url=None, redoc_url=None, openapi_url=None)  # pas de pages publiques
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST, "localhost"])

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, value = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(value.encode(), token.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "jeton manquant ou invalide", {"WWW-Authenticate": "Bearer"})

    auth = [Depends(require_token)]

    @app.get("/api/status", dependencies=auth)
    async def get_status() -> StatusOut:
        acc = agent.last_account
        return StatusOut(
            autonomy=agent.autonomy, cycles=agent.cycles, aborted=agent.aborted, halts=list(agent.risk.halt_reasons),
            pending_signals=len(agent.pending), equity=acc.equity if acc else None, balance=acc.balance if acc else None,
            is_demo=acc.is_demo if acc else None,
            positions=[PositionOut(ticket=p.ticket, symbol=p.symbol, side=p.side.value, volume=p.volume, price_open=p.price_open,
                                   sl=p.sl, tp=p.tp, profit=p.profit) for p in agent.last_positions],
        )  # fmt: skip

    @app.get("/api/signals", dependencies=auth)
    async def list_signals() -> list[SignalOut]:
        now = agent.now()
        return [SignalOut(id=p.id, symbol=p.symbol, side=p.side.value, sl=p.sl, tp=p.tp, confidence=p.confidence, strategy=p.strategy,
                          rationale=list(p.rationale), valid_until=p.valid_until, expired=p.valid_until <= now)
                for p in agent.pending.values()]  # fmt: skip

    @app.post("/api/signals/{proposal_id}/approve", dependencies=auth)
    async def approve(proposal_id: str) -> DecisionOut:
        res = await agent.approve(proposal_id, via="api")
        return DecisionOut(ok=res.ok, message=res.message, ticket=res.ticket, price=res.price)

    @app.post("/api/signals/{proposal_id}/reject", dependencies=auth)
    async def reject(proposal_id: str) -> DecisionOut:
        found = agent.reject(proposal_id, via="api")
        return DecisionOut(ok=found, message="" if found else "Proposition inconnue ou déjà traitée")

    @app.post("/api/kill", dependencies=auth)
    async def kill(body: KillIn) -> DecisionOut:
        write_kill_flag(kill_flag, body.level, f"API : {body.reason}")
        return DecisionOut(ok=True, message=f"Arrêt d'urgence niveau {body.level} demandé : appliqué au prochain cycle de l'agent")

    @app.websocket("/api/events")
    async def events(ws: WebSocket) -> None:
        given = ws.query_params.get("token", "")
        if feed is None or not secrets.compare_digest(given.encode(), token.encode()):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await ws.accept()
        q = feed.subscribe()
        try:
            while True:
                item: dict[str, Any] = await q.get()
                await ws.send_json(item)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            feed.unsubscribe(q)

    return app


async def serve(app: FastAPI, port: int, stop: asyncio.Event) -> None:
    """Sert l'API dans la boucle de l'agent (processus unique, ADR 0001) jusqu'à `stop`. Ctrl+C reste à l'agent."""
    import uvicorn

    class _Server(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self):  # type: ignore[no-untyped-def]  # l'agent garde la main sur Ctrl+C
            yield

    server = _Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning", access_log=False, lifespan="off"))
    task = asyncio.create_task(server.serve())
    await stop.wait()
    server.should_exit = True
    await task
