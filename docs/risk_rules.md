# Règles de risque implémentées (M5)

| Code | Règle | Valeur par défaut | Borne absolue |
|---|---|---|---|
| `HALTED:*` | Arrêt actif (perte jour/semaine, drawdown, pertes consécutives, kill switch) | — | — |
| `LIVE_DISABLED` | Compte non démo refusé (v0) | — | — |
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
| note `VOLUME_REDUCED` | Lot réduit pour respecter le risque cumulé | — | — |

Risque par trade : 0,5 % (borne 1 %). Perte jour 3 % (5 %), semaine 6 % (10 %), drawdown 10 % (20 %).
Le drawdown ne se lève jamais automatiquement ; les arrêts « jour » se lèvent au changement de jour.

**Reste à faire :** exposition par devise, fenêtres d'annonces, week-end, sorties de volume (voir PROJECT_STATUS).
