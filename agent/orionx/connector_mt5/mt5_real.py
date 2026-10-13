"""Connecteur MetaTrader 5 réel (Windows uniquement).

ATTENTION : écrit d'après la documentation du package `MetaTrader5`, NON TESTÉ
sur un terminal réel. À valider d'abord sur un compte DÉMO (voir docs/PROJECT_STATUS.md).

Règle d'or : MT5 n'est pas thread-safe. Tous les appels passent par UN SEUL thread.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from typing import Any, Callable, TypeVar

from orionx.core.errors import GatewayError
from orionx.core.models import AccountState, OrderResult, Position, Side, SymbolSpec, Tick

T = TypeVar("T")
MAGIC = 26_10_12  # identifiant des ordres ORIONX


class Mt5Gateway:
    def __init__(self, login: int, password: str, server: str, path: str | None = None) -> None:
        self._login, self._password, self._server, self._path = login, password, server, path
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")
        self._mt5: Any = None

    async def _run(self, fn: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, partial(fn, *args))

    # --- cycle de vie -------------------------------------------------------------
    def _connect_sync(self) -> None:
        try:
            import MetaTrader5 as mt5  # noqa: PLC0415  (import tardif volontaire)
        except ImportError as exc:
            raise GatewayError(
                "Package MetaTrader5 introuvable : pip install -e \".[mt5]\" (Windows uniquement)"
            ) from exc
        self._mt5 = mt5
        kwargs: dict[str, Any] = {"login": self._login, "password": self._password, "server": self._server}
        ok = mt5.initialize(self._path, **kwargs) if self._path else mt5.initialize(**kwargs)
        if not ok:
            raise GatewayError(f"initialize() a échoué : {mt5.last_error()}")

    async def connect(self) -> None:
        await self._run(self._connect_sync)

    async def shutdown(self) -> None:
        if self._mt5 is not None:
            await self._run(self._mt5.shutdown)
        self._pool.shutdown(wait=False)

    # --- lectures -----------------------------------------------------------------
    async def get_account(self) -> AccountState:
        def f() -> AccountState:
            info = self._mt5.account_info()
            if info is None:
                raise GatewayError(f"account_info() a échoué : {self._mt5.last_error()}")
            return AccountState(
                balance=info.balance,
                equity=info.equity,
                margin_free=info.margin_free,
                margin_level=info.margin_level or None,
                currency=info.currency,
                is_demo=info.trade_mode == self._mt5.ACCOUNT_TRADE_MODE_DEMO,
            )

        return await self._run(f)

    def _symbol_info(self, symbol: str) -> Any:
        info = self._mt5.symbol_info(symbol)
        if info is None or not info.visible:
            self._mt5.symbol_select(symbol, True)
            info = self._mt5.symbol_info(symbol)
        if info is None:
            raise GatewayError(f"Symbole introuvable : {symbol}")
        return info

    async def get_symbol_spec(self, symbol: str) -> SymbolSpec:
        def f() -> SymbolSpec:
            i = self._symbol_info(symbol)
            return SymbolSpec(
                name=symbol,
                digits=i.digits,
                point=i.point,
                tick_size=i.trade_tick_size,
                tick_value=i.trade_tick_value,
                volume_min=i.volume_min,
                volume_max=i.volume_max,
                volume_step=i.volume_step,
            )

        return await self._run(f)

    async def get_tick(self, symbol: str) -> Tick:
        def f() -> Tick:
            t = self._mt5.symbol_info_tick(symbol)
            if t is None:
                raise GatewayError(f"Aucun tick pour {symbol}")
            # TODO(T-08) : t.time est exprimé en heure SERVEUR du courtier ; calibrer le décalage
            # avec l'horloge locale avant de s'en servir pour la détection de données périmées.
            return Tick(symbol, t.bid, t.ask, datetime.fromtimestamp(t.time, tz=timezone.utc))

        return await self._run(f)

    async def get_positions(self) -> list[Position]:
        def f() -> list[Position]:
            rows = self._mt5.positions_get() or ()
            return [
                Position(
                    ticket=r.ticket,
                    symbol=r.symbol,
                    side=Side.BUY if r.type == self._mt5.POSITION_TYPE_BUY else Side.SELL,
                    volume=r.volume,
                    price_open=r.price_open,
                    sl=r.sl or None,
                    tp=r.tp or None,
                    profit=r.profit,
                    opened_at=datetime.fromtimestamp(r.time, tz=timezone.utc),
                )
                for r in rows
            ]

        return await self._run(f)

    # --- ordres -------------------------------------------------------------------
    def _filling(self, info: Any) -> int:
        mt5 = self._mt5
        if info.filling_mode & 2:  # IOC supporté
            return int(mt5.ORDER_FILLING_IOC)
        if info.filling_mode & 1:  # FOK supporté
            return int(mt5.ORDER_FILLING_FOK)
        return int(mt5.ORDER_FILLING_RETURN)

    def _result(self, res: Any) -> OrderResult:
        if res is None:
            return OrderResult.fail(f"order_send() a échoué : {self._mt5.last_error()}")
        ok = res.retcode == self._mt5.TRADE_RETCODE_DONE
        return OrderResult(
            ok=ok,
            ticket=getattr(res, "order", None) or None,
            price=getattr(res, "price", None) or None,
            retcode=res.retcode,
            message=getattr(res, "comment", ""),
        )

    async def send_market_order(
        self, symbol: str, side: Side, volume: float, sl: float, tp: float,
        max_slippage_points: int, comment: str,
    ) -> OrderResult:  # fmt: skip
        def f() -> OrderResult:
            mt5 = self._mt5
            info = self._symbol_info(symbol)
            tick = mt5.symbol_info_tick(symbol)
            buy = side is Side.BUY
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
                "price": tick.ask if buy else tick.bid,
                "sl": sl,
                "tp": tp,
                "deviation": max_slippage_points,
                "magic": MAGIC,
                "comment": comment[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(info),
            }
            res = mt5.order_send(request)
            out = self._result(res)
            # Pour un ordre au marché, le ticket de POSITION est res.order sur la plupart des courtiers.
            return out

        return await self._run(f)

    async def modify_position(self, ticket: int, sl: float, tp: float) -> OrderResult:
        def f() -> OrderResult:
            mt5 = self._mt5
            rows = mt5.positions_get(ticket=ticket)
            if not rows:
                return OrderResult.fail("Position introuvable")
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "symbol": rows[0].symbol,
                "sl": sl,
                "tp": tp,
                "magic": MAGIC,
            }
            return self._result(mt5.order_send(request))

        return await self._run(f)

    async def close_position(self, ticket: int) -> OrderResult:
        def f() -> OrderResult:
            mt5 = self._mt5
            rows = mt5.positions_get(ticket=ticket)
            if not rows:
                return OrderResult.fail("Position introuvable")
            pos = rows[0]
            info = self._symbol_info(pos.symbol)
            tick = mt5.symbol_info_tick(pos.symbol)
            buy = pos.type == mt5.POSITION_TYPE_BUY
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": ticket,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
                "price": tick.bid if buy else tick.ask,
                "deviation": 20,
                "magic": MAGIC,
                "comment": "orionx close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(info),
            }
            return self._result(mt5.order_send(request))

        return await self._run(f)
