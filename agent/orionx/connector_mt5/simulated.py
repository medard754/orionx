"""Courtier simulé en mémoire : tests, backtest et démonstrations sans MT5."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from orionx.core.errors import GatewayError
from orionx.core.models import (
    AccountState,
    Bar,
    OrderResult,
    Position,
    Side,
    SymbolSpec,
    Tick,
)


class SimulatedGateway:
    def __init__(
        self,
        specs: dict[str, SymbolSpec],
        balance: float = 10_000.0,
        *,
        drop_sl_on_fill: bool = False,
        fail_modify: bool = False,
        is_demo: bool = True,
        fail_close: bool = False,
    ) -> None:
        self._specs = specs
        self._balance = balance
        self._ticks: dict[str, Tick] = {}
        self._positions: dict[int, Position] = {}
        self._next_ticket = 1000
        self.closed: list[tuple[Position, float]] = []
        self.reject_next = False
        self.drop_sl_on_fill = drop_sl_on_fill  # simule un courtier qui n'enregistre pas le SL
        self.fail_modify = fail_modify
        self._is_demo = is_demo
        self.fail_close = fail_close
        self._bars: dict[tuple[str, str], list[Bar]] = {}
        self.orders_sent = 0

    # --- pilotage du simulateur -------------------------------------------------
    def set_tick(self, symbol: str, bid: float, ask: float, time: datetime) -> None:
        self._ticks[symbol] = Tick(symbol, bid, ask, time)
        self._check_exits(symbol)

    def set_bars(self, symbol: str, timeframe: str, bars: list[Bar]) -> None:
        self._bars[(symbol, timeframe)] = list(bars)

    # --- Gateway ------------------------------------------------------------------
    async def connect(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def get_account(self) -> AccountState:
        unrealized = sum(self._pnl(p) for p in self._positions.values())
        equity = self._balance + unrealized
        return AccountState(
            balance=self._balance,
            equity=equity,
            margin_free=equity,
            margin_level=None if not self._positions else 1000.0,
            currency="USD",
            is_demo=self._is_demo,
        )

    async def get_symbol_spec(self, symbol: str) -> SymbolSpec:
        try:
            return self._specs[symbol]
        except KeyError as exc:
            raise GatewayError(f"Symbole inconnu : {symbol}") from exc

    async def get_tick(self, symbol: str) -> Tick:
        try:
            return self._ticks[symbol]
        except KeyError as exc:
            raise GatewayError(f"Aucun tick pour {symbol}") from exc

    async def get_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
        return self._bars.get((symbol, timeframe), [])[-count:]

    async def get_positions(self) -> list[Position]:
        return [self._with_profit(p) for p in self._positions.values()]

    async def send_market_order(
        self,
        symbol: str,
        side: Side,
        volume: float,
        sl: float,
        tp: float,
        max_slippage_points: int,
        comment: str,
    ) -> OrderResult:
        self.orders_sent += 1
        if self.reject_next:
            self.reject_next = False
            return OrderResult.fail("Requote (simulé)", retcode=10004)
        tick = await self.get_tick(symbol)
        price = tick.ask if side is Side.BUY else tick.bid
        ticket = self._next_ticket
        self._next_ticket += 1
        self._positions[ticket] = Position(
            ticket=ticket,
            symbol=symbol,
            side=side,
            volume=volume,
            price_open=price,
            sl=None if self.drop_sl_on_fill else sl,
            tp=None if self.drop_sl_on_fill else tp,
            profit=0.0,
            opened_at=tick.time,
        )
        return OrderResult(ok=True, ticket=ticket, price=price, retcode=10009)

    async def modify_position(self, ticket: int, sl: float, tp: float) -> OrderResult:
        pos = self._positions.get(ticket)
        if pos is None:
            return OrderResult.fail("Position introuvable")
        if self.fail_modify:
            return OrderResult.fail("Modification refusée (simulé)", retcode=10016)
        self._positions[ticket] = Position(
            pos.ticket, pos.symbol, pos.side, pos.volume, pos.price_open, sl, tp, pos.profit, pos.opened_at
        )
        return OrderResult(ok=True, ticket=ticket, retcode=10009)

    async def close_position(self, ticket: int, volume: float | None = None) -> OrderResult:
        pos = self._positions.get(ticket)
        if pos is None:
            return OrderResult.fail("Position introuvable")
        if self.fail_close:
            return OrderResult.fail("Clôture refusée (simulé)", retcode=10006)
        tick = self._ticks[pos.symbol]
        price = tick.bid if pos.side is Side.BUY else tick.ask
        if volume is None or volume >= pos.volume - 1e-9:
            self._close(pos, price)
        else:
            if volume <= 0:
                return OrderResult.fail("Volume invalide")
            part = replace(pos, volume=volume)
            profit = self._pnl_at(part, price)
            self._balance += profit
            self.closed.append((part, profit))
            self._positions[ticket] = replace(pos, volume=round(pos.volume - volume, 8))
        return OrderResult(ok=True, ticket=ticket, price=price, retcode=10009)

    # --- interne ------------------------------------------------------------------
    def _pnl_at(self, pos: Position, price: float) -> float:
        spec = self._specs[pos.symbol]
        move = (price - pos.price_open) if pos.side is Side.BUY else (pos.price_open - price)
        return move / spec.tick_size * spec.tick_value * pos.volume

    def _pnl(self, pos: Position) -> float:
        tick = self._ticks.get(pos.symbol)
        if tick is None:
            return 0.0
        return self._pnl_at(pos, tick.bid if pos.side is Side.BUY else tick.ask)

    def _with_profit(self, pos: Position) -> Position:
        return Position(
            pos.ticket, pos.symbol, pos.side, pos.volume, pos.price_open,
            pos.sl, pos.tp, self._pnl(pos), pos.opened_at,
        )  # fmt: skip

    def _close(self, pos: Position, price: float) -> None:
        profit = self._pnl_at(pos, price)
        self._balance += profit
        self.closed.append((pos, profit))
        del self._positions[pos.ticket]

    def _check_exits(self, symbol: str) -> None:
        tick = self._ticks[symbol]
        for pos in list(self._positions.values()):
            if pos.symbol != symbol:
                continue
            if pos.side is Side.BUY:
                if pos.sl is not None and tick.bid <= pos.sl:
                    self._close(pos, min(tick.bid, pos.sl))  # gap : fill au pire prix
                elif pos.tp is not None and tick.bid >= pos.tp:
                    self._close(pos, pos.tp)
            else:
                if pos.sl is not None and tick.ask >= pos.sl:
                    self._close(pos, max(tick.ask, pos.sl))
                elif pos.tp is not None and tick.ask <= pos.tp:
                    self._close(pos, pos.tp)
