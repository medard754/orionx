# Règles de risque implémentées (M5)

| Code | Règle | Valeur par défaut | Borne absolue |
|---|---|---|---|
| `HALTED:*` | Arrêt actif (perte jour/semaine, drawdown, pertes consécutives, kill switch, `GATEWAY_ERRORS`, `ACCOUNT_CHANGED`) | — | — |
| `LIVE_DISABLED` | Compte non démo refusé (v0). Contrôlé aussi à chaque cycle par l'agent : un compte différent ou non démo arrête l'agent (`ACCOUNT_CHANGED`, ADR 0006) | — | — |
| `UNKNOWN_SYMBOL` | Symbole ou tick incohérent | — | — |
| `PROPOSAL_EXPIRED` | Proposition périmée | — | — |
| `STALE_DATA` | Tick plus vieux que `max_data_age_seconds` | 3 s | — |
| `INVALID_SL` / `INVALID_TP` | Stop ou objectif du mauvais côté du prix | — | — |
| `SPREAD_TOO_WIDE` | Spread > seuil | 30 pts | — |
| `MAX_POSITIONS` | Trop de positions ouvertes | 3 | 5 |
| `MARGIN_LEVEL_LOW` | Niveau de marge insuffisant | 300 % | — |
| `RR_TOO_LOW` | Rapport rendement/risque insuffisant | 1,5 | min 1,0 |
| `UNPROTECTED_POSITION` | Une position ouverte n'a pas de SL : tout est bloqué | — | — |
| `TOTAL_RISK_EXCEEDED` | Risque cumulé ouvert au plafond | 2 % | 4 % |
| `VOLUME_BELOW_MIN` | Le lot minimal dépasserait le risque autorisé | — | — |
| `WEEKEND_GUARD` | Vendredi dès 18h UTC jusqu'à la réouverture | activé | — |
| `CURRENCY_EXPOSURE` | Risque net sur une devise au plafond (le lot est d'abord réduit s'il reste de la place) | 2 % | 4 % |
| `NEWS_BLACKOUT` | Annonce majeure dans la fenêtre (calendrier CSV) | ±30 min | — |
| `HIGH_VOLATILITY` | Régime de forte volatilité (ATR >= 1,8 × sa médiane) | activé | — |
| `SESSION_BLOCKED` | Séance interdite par la configuration | aucune | — |
| note `VOLUME_REDUCED` | Lot réduit pour respecter le risque cumulé | — | — |

Risque par trade : 0,5 % (borne 1 %). Perte jour 3 % (5 %), semaine 6 % (10 %), drawdown 10 % (20 %).
Le drawdown ne se lève jamais automatiquement ; les arrêts « jour » se lèvent au changement de jour.

**Reste à faire :** voir PROJECT_STATUS. Les annonces sont traitées par M3 (docs/ANALYSE_M3.md).
