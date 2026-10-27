# Analyse de marché temps réel (M3 v1, à base de règles)

Pas d'IA dans cette version : des règles lisibles, testées, dont chaque décision est expliquée et journalisée.
L'IA viendra remplacer ou compléter ces règles quand on aura MESURÉ ce qu'elles apportent.

## Ce que l'agent calcule
Pour chaque symbole, à chaque nouvelle bougie H1 fermée (`MarketState`) :
| Mesure | Définition |
|---|---|
| Régime | `trend_up`, `trend_down`, `range`, `high_vol`, `unknown` |
| Volatilité | ATR actuel / ATR médian des 120 dernières bougies (`high_vol` si >= 1,8) |
| Efficacité | Ratio de Kaufman sur 20 bougies : 0 = bruit, 1 = ligne droite (tendance si >= 0,25) |
| Force de tendance | (EMA20 - EMA50) / ATR (tendance si >= 0,3 en valeur absolue, pente de l'EMA50 concordante) |
| Séance | asia 23h-7h, london 7h-12h, overlap 12h-16h, new_york 16h-21h, off 21h-23h (UTC) |
Un changement de régime ou de séance publie `MarketStateUpdated` dans le journal d'audit.

## Heure du courtier
MT5 étiquette ses heures en heure SERVEUR (UTC+2 l'hiver, UTC+3 l'été chez la plupart). L'agent MESURE le décalage
sur un prix frais (arrondi à 30 min, recalculé en continu : il suit le changement d'heure). Sans mesure : règle EET.
Le backtest utilise la règle EET (approximation : à vérifier si votre courtier est différent).

## Ce qui bloque une entrée (règles déterministes, dans le gestionnaire de risques)
| Code | Condition | Réglage |
|---|---|---|
| `NEWS_BLACKOUT` | annonce de l'impact configuré dans les 30 min avant / 30 min après, sur l'une des devises du symbole | `analysis.news_*`, calendrier CSV |
| `HIGH_VOLATILITY` | régime `high_vol` | `analysis.block_high_vol` |
| `SESSION_BLOCKED` | séance dans `analysis.blocked_sessions` | liste vide par défaut |

## Surveillance d'un trade ouvert (`ThesisDegraded`)
Pour chaque position, à chaque cycle, l'agent regarde si la logique du trade tient encore :
`régime opposé à la position` · `tendance perdue` (régime favorable à l'entrée devenu range) ·
`volatilité anormale` · `annonce imminente`.
Réaction, UNE SEULE FOIS par changement de raisons : le stop est resserré (jamais éloigné). Si
`analysis.thesis_exit_min_reasons: N` est réglé, la position est clôturée dès N raisons.

## Calendrier économique
Fichier CSV tenu à la main (`calendar.example.csv`), heures en UTC, une ligne par annonce. Si le fichier n'est pas
configuré, aucun blocage autour des annonces (l'agent l'indique au démarrage). Récupération automatique : plus tard.

## Filtres = hypothèses à tester (backtest)
`python -m orionx.main backtest --bars 15000 --filters none,vol,session,trend,all`
`vol` : pas de forte volatilité · `session` : londres, overlap, new york · `trend` : régime concordant avec le sens du trade ·
`all` : les trois. Chaque filtre est comparé à un témoin aléatoire SOUMIS AU MÊME FILTRE.
**Règle : un filtre n'est retenu que s'il améliore les résultats sur des données jamais regardées.**
Le filtre `trend` n'agit pas sur un calendrier historique (non disponible) : le backtest ne teste pas les annonces.
