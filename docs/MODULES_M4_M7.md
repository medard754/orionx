# Modules M4 à M7 : ce qui est implémenté

Tout est testé sur le simulateur (192 tests). Rien de ce document n'est validé sur MetaTrader 5 : voir RUNBOOK_VALIDATION.md §8.

## M4 Moteur de décision (`orionx/decision/`)
| Fonction du cahier des charges | Implémentation |
|---|---|
| F4.1 bibliothèque de stratégies activables | `trend_pullback`, `mean_reversion`, `breakout` (registre `decision/registry.py`). Seule `trend_pullback` est activée par défaut ; les autres sont des HYPOTHÈSES non validées |
| F4.2 choix selon le régime | Chaque stratégie a ses régimes d'application (tendance, range). Régime `unknown` = aucune entrée |
| F4.3 / F4.4 proposition + rapport rendement/risque | `Candidate` : proposition, score, stratégies d'accord, R:R calculé |
| F4.5 classement et diversification | Plusieurs opportunités : meilleur score d'abord ; deux entrées dans le même sens sur une même devise sont écartées (`max_per_currency_direction`) |
| F4.6 signaux contradictoires | Une stratégie dit achat, l'autre vente sur le même symbole : AUCUN trade (`CONFLICT`) |
| F4.7 renforcer / réduire / sortir | Renforcer : option `allow_pyramiding`, seulement si la position est DÉJÀ sans risque (stop au point d'entrée ou mieux) et de même sens. Jamais de moyenne à la baisse. Réduire, sortir, resserrer : règles de M7 |
| F4.8 modes | signals_only, semi_auto (approbation humaine, re-vérifiée par M5), auto (verrouillé : `--allow-auto`) |

## M5 Gestionnaire de risques
Nouveau : exposition NETTE par devise (acheter EURUSD en étant déjà short USD via GBPUSD réduit ou refuse le lot,
vendre EURUSD le compense) · garde week-end (plus d'entrée le vendredi à partir de 18h UTC) · seuil de spread par
symbole · profils prudent / modéré / dynamique (`risk: {profile: ...}`) · **verrouillage des paramètres** : toute
hausse du risque (config.yaml ou `risk-set`) n'est appliquée qu'après 24 h ; une baisse l'est tout de suite ; modifier
le fichier ne contourne pas le délai · mode sûr automatique si les prix sont figés depuis 30 s · chaque arrêt
automatique est publié (`CircuitBreakerTripped`) dans le journal.

## M6 Exécution
Nouveau : nouvelles tentatives limitées (2) sur rejets « sûrs » (requote, prix modifié, pas de cotation), avec
approbation re-vérifiée · réponse ambiguë (délai dépassé) : l'ordre n'est JAMAIS renvoyé, on cherche d'abord la position
(par son commentaire) · contrôle du glissement : au-delà de la tolérance, la position est clôturée aussitôt
· qualité d'exécution mesurée (latence, glissement, tentatives, taux de réussite) et journalisée (`ExecutionQuality`).

## M7 Gestion des positions
Nouveau : stop suiveur à N × ATR (option) · fermeture avant le week-end (option `weekend_flat`) · réaction à une annonce
(`news_action` : resserrer, réduire de moitié, ou clôturer) · réduction à partir de N raisons de dégradation ·
positions ouvertes à la main gérées ou laissées à l'utilisateur (`manage_foreign`). Déjà là : breakeven, stop suiveur à
1R, sortie partielle, sortie temporelle, reprise des positions au redémarrage. Le stop n'est jamais éloigné.

## Commandes
`risk-set --profile moderate` · `risk-status` · `risk-check --symbol EURUSD --side buy` (simulation à blanc) · `backtest --strategy trend_pullback|mean_reversion|breakout|engine[:a,b]`
