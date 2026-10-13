"""Contrat unique d'accès au courtier. Tout le reste du code dépend de ce Protocol."""

from __future__ import annotations

from typing import Protocol

from orionx.core.models import AccountState, OrderResult, Position, Side, SymbolSpec, Tick


class Gateway(Protocol):
    async def connect(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def get_account(self) -> AccountState: ...

    async def get_symbol_spec(self, symbol: str) -> SymbolSpec: ...

    async def get_tick(self, symbol: str) -> Tick: ...

    async def get_positions(self) -> list[Position]: ...

    async def send_market_order(
        self,
        symbol: str,
        side: Side,
        volume: float,
        sl: float,
        tp: float,
        max_slippage_points: int,
        comment: str,
    ) -> OrderResult: ...

    async def modify_position(self, ticket: int, sl: float, tp: float) -> OrderResult: ...

    async def close_position(self, ticket: int) -> OrderResult: ...
