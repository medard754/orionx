from __future__ import annotations

from datetime import datetime, timedelta, timezone

from orionx.core.config import RiskConfig
from orionx.core.models import AccountState, Side, SymbolSpec, Tick, TradeProposal
from orionx.risk.approval import ApprovalSigner
from orionx.risk.manager import RiskManager

T0 = datetime(2026, 10, 12, 10, 0, 0, tzinfo=timezone.utc)
EURUSD = SymbolSpec("EURUSD", 5, 1e-5, 1e-5, 1.0, 0.01, 100.0, 0.01)
SPECS = {"EURUSD": EURUSD}


class FixedClock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


def tick(bid: float = 1.08412, ask: float = 1.08420, at: datetime = T0) -> Tick:
    return Tick("EURUSD", bid, ask, at)


def account(balance: float = 10_000.0, equity: float | None = None, demo: bool = True,
            margin_level: float | None = None) -> AccountState:  # fmt: skip
    return AccountState(balance, balance if equity is None else equity, balance, margin_level, "USD", demo)


def proposal(side: Side = Side.BUY, sl: float = 1.0820, tp: float = 1.0910, pid: str = "p1",
             valid_s: float = 120) -> TradeProposal:  # fmt: skip
    return TradeProposal(pid, "EURUSD", side, sl, tp, 0.7, "test", ("t",), T0, T0 + timedelta(seconds=valid_s))


def manager(cfg: RiskConfig | None = None, clock: FixedClock | None = None) -> tuple[RiskManager, ApprovalSigner, FixedClock]:
    clock = clock or FixedClock()
    signer = ApprovalSigner(key=b"k" * 32, clock=clock)
    rm = RiskManager(cfg or RiskConfig(), signer, max_data_age_seconds=3.0, clock=clock)
    rm.start(account())
    return rm, signer, clock
