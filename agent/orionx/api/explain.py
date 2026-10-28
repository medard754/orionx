"""Explications en langage simple pour le « mode débutant » du dashboard. Calcul pur, aucune décision, aucun ordre.

Règle d'écriture : on DÉCRIT (ce que l'agent a vu, ce qui est en jeu en argent, pourquoi le risque accepte ou refuse) ; on ne
recommande jamais d'acheter ou de vendre. Le rappel « aucun avantage démontré » accompagne toujours une explication de signal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from orionx.core.models import AccountState, Side, SymbolSpec, Tick
from orionx.risk.manager import Decision

EDGE_NOTICE = ("Rappel : aucune stratégie d'ORIONX n'a encore démontré d'avantage statistique. Ce signal n'est pas une prévision : "
               "n'approuvez que ce que vous acceptez de perdre.")  # fmt: skip

REASONS_FR = {
    "LIVE_DISABLED": "Le compte n'est pas un compte de démonstration : ORIONX refuse de trader.",
    "UNKNOWN_SYMBOL": "Le symbole ou son prix n'est pas reconnu.",
    "PROPOSAL_EXPIRED": "Le signal a expiré : sa durée de validité est dépassée.",
    "STALE_DATA": "Le dernier prix reçu est trop ancien pour décider en sécurité.",
    "INVALID_SL": "Le stop-loss est du mauvais côté du prix : il ne protégerait pas la position.",
    "INVALID_TP": "L'objectif est du mauvais côté du prix.",
    "SPREAD_TOO_WIDE": "L'écart entre prix d'achat et de vente (spread) est trop large : le trade coûterait trop cher.",
    "MAX_POSITIONS": "Le nombre maximal de positions ouvertes est atteint.",
    "MARGIN_LEVEL_LOW": "La marge disponible du compte est insuffisante.",
    "RR_TOO_LOW": "Le gain visé est trop petit par rapport à la perte possible (rapport rendement/risque insuffisant).",
    "UNPROTECTED_POSITION": "Une position ouverte n'a pas de stop-loss : tout nouveau trade est bloqué tant qu'elle n'est pas protégée.",
    "TOTAL_RISK_EXCEEDED": "Le risque cumulé des positions ouvertes a atteint sa limite.",
    "VOLUME_BELOW_MIN": "Même avec la plus petite taille autorisée, la perte dépasserait le risque permis.",
    "WEEKEND_GUARD": "Plus aucune entrée n'est permise à l'approche de la fermeture du week-end.",
    "CURRENCY_EXPOSURE": "Le risque sur l'une des devises de cette paire est déjà au maximum.",
    "NEWS_BLACKOUT": "Une annonce économique majeure est imminente ou vient de sortir : l'agent ne prend aucune entrée.",
    "HIGH_VOLATILITY": "Le marché est anormalement agité : l'agent ne prend aucune entrée.",
    "SESSION_BLOCKED": "Cette séance de marché est exclue par votre configuration.",
}
HALTS_FR = {
    "KILL_SWITCH": "l'arrêt d'urgence est actif",
    "DAILY_LOSS_LIMIT": "la perte maximale du jour est atteinte",
    "WEEKLY_LOSS_LIMIT": "la perte maximale de la semaine est atteinte",
    "MAX_DRAWDOWN": "le recul maximal du capital (drawdown) est atteint",
    "CONSECUTIVE_LOSSES": "trop de pertes consécutives",
    "GATEWAY_ERRORS": "la liaison avec MetaTrader est en erreur (mode sûr)",
    "DATA_STALE": "les prix sont figés (mode sûr)",
    "ACCOUNT_CHANGED": "le compte du terminal a changé",
}
MANUAL_NOTICE = ("Rappel : ORIONX ne juge pas la qualité de votre idée de trade. Il calcule seulement la taille pour que la perte, si le "
                "stop-loss est touché, reste dans votre limite de risque. Aucun résultat n'est assuré : "
                "n'approuvez que ce que vous acceptez de perdre.")  # fmt: skip
NOTES_FR = {"VOLUME_REDUCED": "La taille a été réduite pour respecter le risque cumulé autorisé."}

STRATEGIES_FR = {
    "ticket_manuel": "Ce trade vient de vous : ORIONX n'a pas d'avis sur la direction du marché. "
                     "Il applique votre stop-loss et vérifie le risque.",
    "trend_pullback": "Cette stratégie cherche les petits reculs dans une tendance déjà installée : elle entre dans le sens de la tendance "
                      "après un repli.",
    "mean_reversion": "Cette stratégie parie qu'après un mouvement excessif, dans un marché sans tendance, le prix revient vers sa "
                      "moyenne.",
    "breakout": "Cette stratégie suit les cassures : quand le prix sort de sa fourchette récente avec de l'élan, elle parie que le "
                "mouvement continue.",
}  # fmt: skip


def reason_fr(code: str) -> str:
    """Phrase simple pour un code de rejet du risque. Un code inconnu reste visible (jamais masqué)."""
    if code.startswith("HALTED:"):
        halt = code.split(":", 1)[1]
        return f"Aucune entrée possible : {HALTS_FR.get(halt, halt)}."
    return REASONS_FR.get(code, f"Refus du gestionnaire de risques ({code}).")


def strategy_blurb(name: str) -> str:
    for key, text in STRATEGIES_FR.items():
        if name.startswith(key):
            return text
    return "Cette stratégie propose des entrées selon des règles fixes sur les prix passés."


def fr(value: float | None) -> str:
    """Nombre à la française (virgule décimale), comme les montants."""
    return "—" if value is None else f"{value:g}".replace(".", ",")


def money(value: float | None, currency: str) -> str:
    return "—" if value is None else f"{value:,.2f} {currency}".replace(",", " ").replace(".", ",")


def build_preview(decision: Decision, side: Side, tick: Tick, spec: SymbolSpec, account: AccountState) -> dict[str, Any]:
    """Ce que le gestionnaire de risques ferait de ce signal MAINTENANT, avec les montants en jeu. Lecture seule."""
    out: dict[str, Any] = {"available": True, "would_approve": decision.approved, "currency": account.currency,
                           "reasons_fr": [reason_fr(r) for r in decision.reasons], "notes_fr": [NOTES_FR.get(n, n) for n in decision.notes],
                           "volume": None, "risk_cash": None, "risk_pct": None, "reward_cash": None, "reward_pct": None,
                           "rr": None}  # fmt: skip
    if not decision.approved or decision.order is None:
        return out
    o, entry = decision.order, (tick.ask if side is Side.BUY else tick.bid)
    capital = min(account.balance, account.equity)

    def cash(price: float) -> float:
        return abs(entry - price) / spec.tick_size * spec.tick_value * o.volume

    risk, reward = cash(o.sl), cash(o.tp)
    out.update(volume=o.volume, risk_cash=round(risk, 2), reward_cash=round(reward, 2),
               risk_pct=round(risk / capital * 100, 3) if capital > 0 else None,
               reward_pct=round(reward / capital * 100, 3) if capital > 0 else None,
               rr=round(reward / risk, 2) if risk > 0 else None)  # fmt: skip
    return out


def explain_signal(*, side: str, symbol: str, strategy: str, rationale: Sequence[str], regime_label: str | None,
                   session_label: str | None, sl: float, tp: float, preview: Mapping[str, Any],
                   manual: bool = False) -> list[str]:  # fmt: skip
    """Paragraphes « Pourquoi ce signal ? » : direction, logique, contexte, argent en jeu, verdict du risque, rappel d'honnêteté."""
    buy = side == "buy"
    who = "vous pariez" if manual else "l'agent parie"
    out = [f"{'Achat' if buy else 'Vente'} sur {symbol} : {who} sur une {'hausse' if buy else 'baisse'} du prix.", strategy_blurb(strategy)]
    if rationale:
        out.append(("Votre ticket : " if manual else "Ce qu'il a repéré : ") + " ; ".join(rationale) + ".")
    context = []
    if regime_label:
        context.append(f"régime du marché : {regime_label.lower()}")
    if session_label:
        context.append(f"séance : {session_label}")
    if context:
        out.append("Contexte actuel : " + ", ".join(context) + ".")
    cur = str(preview.get("currency", ""))
    if preview.get("would_approve") and preview.get("risk_cash") is not None:
        out.append(f"Si le stop-loss ({sl}) est touché, vous perdez environ {money(preview['risk_cash'], cur)} "
                   f"({fr(preview['risk_pct'])} % du capital). Si l'objectif ({tp}) est atteint, vous gagnez environ "
                   f"{money(preview['reward_cash'], cur)}. Taille calculée par le gestionnaire de risques : "
                   f"{fr(preview['volume'])} lot.")  # fmt: skip
        out.extend(preview.get("notes_fr", []))
    else:
        reasons = list(preview.get("reasons_fr", []))
        out.append("Le gestionnaire de risques refuserait ce trade en ce moment" + (" :" if reasons else "."))
        out.extend(reasons)
    out.append(MANUAL_NOTICE if manual else EDGE_NOTICE)
    return out
