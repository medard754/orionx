"""API locale (T-14) : état de l'agent, signaux en attente, approbation humaine, arrêt d'urgence, flux d'événements.

Sécurité (ADR 0007) : écoute UNIQUEMENT 127.0.0.1 ; jeton aléatoire obligatoire (Authorization: Bearer, ou ?token= pour le
WebSocket) ; en-tête Host vérifié (anti DNS rebinding) ; aucune règle CORS (une page web ne peut pas appeler l'API) ;
aucun journal des requêtes (le jeton n'y apparaît jamais). L'API n'a AUCUN pouvoir propre : l'approbation passe par
Agent.approve (risque ré-évalué, ApprovedOrder signé, journal d'audit), l'arrêt d'urgence par le drapeau habituel.

Dashboard (M9, ADR 0008) : le navigateur n'a jamais le jeton. Un code à usage unique (60 s) s'échange contre un cookie de
session HttpOnly + SameSite=Strict ; toute action par cookie exige en plus l'en-tête X-Requested-By ; CSP stricte, pas de
cadre (anti clickjacking) ; le WebSocket par cookie vérifie l'Origin.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from orionx.api.feed import EventFeed
from orionx.monitoring.flag import write_kill_flag

if TYPE_CHECKING:
    from orionx.agent import Agent

HOST = "127.0.0.1"
COOKIE = "orionx_session"
CSRF_HEADER, CSRF_VALUE = "x-requested-by", "orionx-dashboard"
STATIC = Path(__file__).parent / "static"


class Sessions:
    """Codes de connexion à usage unique (60 s) et sessions du dashboard, en mémoire : tout expire au redémarrage."""

    def __init__(self, code_ttl: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl, self._clock = code_ttl, clock
        self._codes: dict[str, float] = {}
        self._sessions: set[str] = set()

    def issue_code(self) -> str:
        code = secrets.token_urlsafe(24)
        self._codes[code] = self._clock() + self._ttl
        return code

    def redeem(self, code: str) -> str | None:
        expires = self._codes.pop(code, None)  # usage unique : retiré même s'il a expiré
        if expires is None or self._clock() > expires:
            return None
        session = secrets.token_urlsafe(32)
        self._sessions.add(session)
        return session

    def valid(self, session: str | None) -> bool:
        return bool(session) and session in self._sessions


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


def create_app(agent: Agent, token: str, kill_flag: Path, feed: EventFeed | None = None, *, port: int = 8765,
               sessions: Sessions | None = None) -> FastAPI:  # fmt: skip
    app = FastAPI(title="ORIONX - API locale", docs_url=None, redoc_url=None, openapi_url=None)  # pas de pages publiques
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST, "localhost"])
    sessions = sessions or Sessions()
    app.state.sessions = sessions
    app.state.feed = feed
    origins = {f"http://{HOST}:{port}", f"http://localhost:{port}"}
    csp = (f"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self' ws://{HOST}:{port} "
           f"ws://localhost:{port}; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")  # fmt: skip

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Any]) -> Response:
        response: Response = await call_next(request)
        response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                                 "Cache-Control": "no-store", "Content-Security-Policy": csp})  # fmt: skip
        return response

    def bearer_ok(authorization: str | None) -> bool:
        scheme, _, value = (authorization or "").partition(" ")
        return scheme.lower() == "bearer" and secrets.compare_digest(value.encode(), token.encode())

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        if not bearer_ok(authorization):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "jeton manquant ou invalide", {"WWW-Authenticate": "Bearer"})

    def require_auth(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
        """Jeton (scripts) OU session du dashboard ; une action par session exige l'en-tête anti-CSRF."""
        if bearer_ok(authorization):
            return
        if not sessions.valid(request.cookies.get(COOKIE)):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "jeton manquant ou invalide", {"WWW-Authenticate": "Bearer"})
        if request.method not in ("GET", "HEAD") and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "en-tête X-Requested-By manquant")

    auth = [Depends(require_auth)]

    # --- dashboard ----------------------------------------------------------------------
    @app.post("/api/session/code", dependencies=[Depends(require_token)])
    async def session_code() -> dict[str, str]:
        """Code de connexion à usage unique pour ouvrir le dashboard (jeton requis : jamais par cookie)."""
        return {"code": sessions.issue_code()}

    @app.get("/login")
    async def login(code: str = "") -> Response:
        session = sessions.redeem(code)
        if session is None:
            return PlainTextResponse("Code invalide ou expiré : relancez « python -m orionx.main dashboard ».", status_code=401)
        response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(COOKIE, session, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")

    @app.get("/app.js")
    async def app_js() -> FileResponse:
        return FileResponse(STATIC / "app.js", media_type="text/javascript; charset=utf-8")

    @app.get("/style.css")
    async def style_css() -> FileResponse:
        return FileResponse(STATIC / "style.css", media_type="text/css; charset=utf-8")

    @app.get("/logo.png")
    async def logo() -> FileResponse:
        return FileResponse(STATIC / "logo.png", media_type="image/png")

    @app.get("/icon.png")
    async def icon() -> FileResponse:
        return FileResponse(STATIC / "icon.png", media_type="image/png")

    @app.get("/favicon.ico")
    async def favicon() -> FileResponse:
        return FileResponse(STATIC / "favicon.png", media_type="image/png")

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

    @app.get("/api/analysis", dependencies=auth)
    async def analysis() -> list[dict[str, Any]]:
        """Analyse de marché de chaque symbole (tendance, momentum, volatilité, niveaux, séance, décision). Lecture seule."""
        return agent.analysis()

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
        by_token = bool(given) and secrets.compare_digest(given.encode(), token.encode())
        by_session = sessions.valid(ws.cookies.get(COOKIE)) and ws.headers.get("origin") in origins  # anti détournement
        if feed is None or not (by_token or by_session):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await ws.accept()
        q = feed.subscribe()
        try:
            while True:
                item = await q.get()
                if item is None:  # arrêt de l'API : on ferme proprement (1001 = départ)
                    await ws.close(code=status.WS_1001_GOING_AWAY)
                    break
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

    server = _Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning", access_log=False, lifespan="off",
                                    timeout_graceful_shutdown=3))  # fmt: skip
    task = asyncio.create_task(server.serve())
    await stop.wait()
    feed: EventFeed | None = getattr(app.state, "feed", None)
    if feed is not None:
        feed.close()  # ferme les flux des navigateurs : sinon l'arrêt attendrait leur déconnexion
    server.should_exit = True
    await task
