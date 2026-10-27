"""Contrat unique d'accès au courtier. Tout le reste du code dépend de ce Protocol."""

from __future__ import annotations

from typing import Protocol

from orionx.core.models import AccountState, Bar, OrderResult, Position, Side, SymbolSpec, Tick


class Gateway(Protocol):
    async def connect(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def reconnect(self) -> None:
        """Referme puis rouvre la liaison avec le terminal. Lève GatewayError si c'est impossible."""
        ...

    async def get_account(self) -> AccountState: ...

    async def get_symbol_spec(self, symbol: str) -> SymbolSpec: ...

    async def get_tick(self, symbol: str) -> Tick: ...

    async def get_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
        """Bougies FERMÉES, de la plus ancienne à la plus récente (la bougie en cours est exclue)."""
        ...

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

    async def close_position(self, ticket: int, volume: float | None = None) -> OrderResult:
        """Clôture totale, ou partielle si `volume` est fourni."""
        ...
