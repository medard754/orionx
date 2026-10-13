"""T-02 : premier ordre DÉMO de bout en bout.

Chaîne complète : prix frais -> proposition -> gestionnaire de risques -> approbation signée ->
exécution -> vérification SL/TP dans le terminal -> pause -> fermeture.
Taille volontairement minimale (0,01 lot). Refuse tout compte qui n'est pas un compte démo.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta

from orionx.connector_mt5.gateway import Gateway
from orionx.core.config import RiskConfig
from orionx.core.models import Side, Tick, TradeProposal, utcnow
from orionx.execution.executor import Executor
from orionx.risk.approval import ApprovalSigner
from orionx.risk.manager import RiskManager

HINTS = {
    10027: "Le trading algorithmique est désactivé : cliquez sur « Trading Algo » (le bouton doit devenir VERT).",
    10018: "Marché fermé.",
    10030: "Mode de remplissage refusé par le courtier (à corriger dans mt5_real._filling).",
    10019: "Fonds insuffisants.",
}
# 0,0025 % de 100 000 $ = 2,5 $ de risque => 0,01 lot pour un stop à 20 pips.
TEST_RISK_PCT = 0.0025


async def wait_fresh_tick(gw: Gateway, symbol: str, sleep: Callable[[float], Awaitable[None]], timeout: float = 20.0) -> Tick | None:
    waited = 0.0
    while waited <= timeout:
        tick = await gw.get_tick(symbol)
        if (utcnow() - tick.observed_at) < timedelta(seconds=2):
            return tick
        await sleep(0.5)
        waited += 0.5
    return None


async def run_demo_trade(
    gw: Gateway,
    *,
    symbol: str = "EURUSD",
    sl_pips: float = 20.0,
    tp_pips: float = 60.0,
    hold_seconds: float = 10.0,
    confirm: Callable[[str], bool],
    out: Callable[[str], None] = print,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    account = await gw.get_account()
    out(f"Compte : équité={account.equity} {account.currency} démo={account.is_demo}")
    if not account.is_demo:
        out("REFUS : ce n'est pas un compte démo.")
        return 3

    spec = await gw.get_symbol_spec(symbol)
    tick = await wait_fresh_tick(gw, symbol, sleep)
    if tick is None:
        out("Aucun prix frais en 20 s : marché fermé ou flux gelé. Réessayez en heures de marché.")
        return 4
    out(f"Prix : bid={tick.bid} ask={tick.ask}")

    pip = spec.point * 10
    now = utcnow()
    proposal = TradeProposal(
        id=f"demo-{now:%H%M%S}", symbol=symbol, side=Side.BUY,
        sl=round(tick.ask - sl_pips * pip, spec.digits), tp=round(tick.ask + tp_pips * pip, spec.digits),
        confidence=0.5, strategy="demo-T02", rationale=("test technique",),
        created_at=now, valid_until=now + timedelta(minutes=2),
    )  # fmt: skip

    signer = ApprovalSigner()
    risk = RiskManager(RiskConfig(risk_per_trade_pct=TEST_RISK_PCT), signer, max_data_age_seconds=3.0)
    risk.start(account)
    decision = risk.evaluate(proposal, tick, account, await gw.get_positions(), {symbol: spec})
    if not decision.approved or decision.order is None:
        out(f"Gestionnaire de risques : REFUS {decision.reasons}")
        return 5
    order = decision.order
    out(f"Approuvé : ACHAT {order.volume} lot {symbol}, SL {order.sl}, TP {order.tp} (signé)")

    if not confirm("Tapez OUI pour envoyer cet ordre sur le compte DÉMO : "):
        out("Annulé. Aucun ordre envoyé.")
        return 6

    result = await Executor(gw, signer).execute(order)
    if not result.ok:
        out(f"ÉCHEC : {result.message} (code {result.retcode}). {HINTS.get(result.retcode or 0, '')}")
        return 7
    out(f"Ordre exécuté : ticket {result.ticket} au prix {result.price}")

    positions = [p for p in await gw.get_positions() if p.ticket == result.ticket]
    if not positions:
        out("ATTENTION : position introuvable après exécution.")
        return 8
    p = positions[0]
    out(f"Position visible : {p.symbol} {p.side.value} {p.volume} lot, SL={p.sl}, TP={p.tp}")
    out(f"-> Regardez l'onglet « Trade » de MetaTrader 5 : la position reste ouverte {hold_seconds:.0f} s.")
    await sleep(hold_seconds)

    closed = await gw.close_position(p.ticket)
    out("Position fermée." if closed.ok else f"FERMETURE ÉCHOUÉE : {closed.message} -> fermez-la à la main dans MT5 !")
    final = await gw.get_account()
    out(f"Solde final : {final.balance} {final.currency} (écart dû au spread)")
    return 0 if closed.ok else 9
