"""Simulation à blanc du gestionnaire de risques sur l'état RÉEL du compte : n'envoie AUCUN ordre.

Répond à « si l'agent voulait acheter / vendre ce symbole maintenant, que déciderait le risque ? » (taille de lot,
réduction ou refus par exposition, week-end, spread...). Les contrôles d'annonces et de régime (agent) ne sont pas inclus.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from orionx.connector_mt5.gateway import Gateway
from orionx.core.config import AppConfig, RiskConfig
from orionx.core.models import Side, TradeProposal, utcnow
from orionx.demo_trade import wait_fresh_tick
from orionx.risk.approval import ApprovalSigner
from orionx.risk.manager import RiskManager


async def check_proposal(gw: Gateway, cfg: AppConfig, symbol: str, side: Side, sl_pips: float = 20.0, rr: float = 3.0,
                         out: Callable[[str], None] = print, sleep=None, risk_cfg: RiskConfig | None = None) -> int:  # type: ignore[no-untyped-def]  # fmt: skip
    """`risk_cfg` : la configuration de risque que l'agent utiliserait réellement (RiskParamGuard.preview), pas config.yaml."""
    import asyncio

    risk_cfg = risk_cfg or cfg.risk
    account, spec = await gw.get_account(), await gw.get_symbol_spec(symbol)
    tick = await wait_fresh_tick(gw, symbol, sleep or asyncio.sleep)
    if tick is None:
        out("Aucun prix frais : marché fermé ou flux gelé.")
        return 4
    positions = await gw.get_positions()
    specs = {symbol: spec}
    for p in positions:
        specs.setdefault(p.symbol, await gw.get_symbol_spec(p.symbol))
    out(f"Compte : équité={account.equity} {account.currency} · positions ouvertes : {len(positions)}")
    for p in positions:
        out(f"  - {p.symbol} {p.side.value} {p.volume} lot, SL={p.sl}")
    buy, pip, now = side is Side.BUY, spec.point * 10, utcnow()
    entry = tick.ask if buy else tick.bid
    sl = entry - sl_pips * pip if buy else entry + sl_pips * pip
    tp = entry + rr * sl_pips * pip if buy else entry - rr * sl_pips * pip
    prop = TradeProposal("riskcheck", symbol, side, sl, tp, 0.5, "riskcheck", ("simulation",), now, now + timedelta(minutes=2))
    signer = ApprovalSigner()
    risk = RiskManager(risk_cfg, signer, max_data_age_seconds=cfg.max_data_age_seconds)
    risk.start(account)
    d = risk.evaluate(prop, tick, account, positions, specs)
    out(f"Proposition simulée : {side.value.upper()} {symbol}, stop à {sl_pips:.0f} pips, objectif {rr:.0f}x")
    if d.approved and d.order:
        out(f"-> APPROUVÉ : {d.order.volume} lot (risque cible {risk_cfg.risk_per_trade_pct} % du capital)")
    else:
        out(f"-> REFUSÉ : {', '.join(d.reasons)}")
    if d.notes:
        out(f"   notes : {', '.join(d.notes)}")
    out("(simulation : aucun ordre n'a été envoyé)")
    return 0 if d.approved else 5
