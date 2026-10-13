# Validation sur MetaTrader 5 (compte DÉMO) – T-03 à T-12

Les 101 tests automatisés valident la logique avec un courtier simulé. Ce document valide ce qui ne peut l'être
qu'avec un vrai terminal. **Faire les étapes dans l'ordre. Ne passer à la suivante que si la précédente est OK.**
Prérequis : MT5 ouvert et connecté (compte démo Hedging), bouton « Trading Algo » VERT, marché ouvert.
Toutes les commandes se lancent depuis `agent/` avec le `(.venv)` actif.

## 1. L'agent démarre et journalise (aucun ordre)
```
python -m orionx.main run --max-cycles 30
python -m orionx.main audit-tail
python -m orionx.main audit-verify
```
Attendu : « Agent démarré : autonomie=semi_auto… », puis `AgentStarted`, `AgentStopped` dans le journal, et
« INTÈGRE ». Un `DataStale` suivi d'un `DataRecovered` au démarrage est normal (premier prix d'âge inconnu).
Preuve à garder : sortie des 3 commandes.

## 2. Intégrité du journal
Ouvrir `data/audit.db` avec un outil SQLite, tenter de modifier une ligne : le trigger doit refuser. Puis
`audit-verify` doit toujours répondre INTÈGRE.

## 3. Arrêt d'urgence (le plus important)
1. Terminal A : `python -m orionx.main demo-trade --hold 180` puis taper `OUI` (la position reste 3 minutes).
2. Terminal B, pendant ce délai :
   `python -m orionx.main kill --level 2 --yes` → le stop peut se resserrer ; il ne s'éloigne jamais. Vérifier
   dans l'onglet « Trade » de MT5.
   `python -m orionx.main kill --level 3 --yes` → la position doit se fermer en quelques secondes.
3. `python -m orionx.main clear-kill`
Attendu : « position #… clôturée » et aucune ligne « ÉCHECS ». Le message « FERMETURE ÉCHOUÉE » affiché ensuite
par demo-trade est normal (la position n'existe plus).
Preuve : sortie de la commande `kill` + capture de l'onglet Trade avant/après.

## 4. Drapeau d'arrêt vu par l'agent en marche
Terminal A : `python -m orionx.main run`. Terminal B : `python -m orionx.main kill --level 1 --yes`.
Attendu : l'agent affiche un arrêt d'urgence au cycle suivant ; `audit-tail` contient `KillSwitchActivated`.
Ctrl+C dans A, puis `clear-kill`.

## 5. Backtest sur l'historique réel
`python -m orionx.main backtest --bars 3000`
Attendu : nombre de trades, profit factor, drawdown. **Résultats indicatifs seulement.** Noter les chiffres :
une stratégie n'est envisagée qu'après un walk-forward sur plusieurs périodes (voir plan).

## 6. Données (T-04)
Laisser `run` tourner pendant une heure : `data/market.db` doit contenir des bougies M1/M5/H1 fermées.
Couper le Wi-Fi 20 secondes : un `DataStale` doit apparaître dans le journal, puis `DataRecovered`.

## 7. NE PAS FAIRE avant validation complète
Activer `autonomy: auto` + `--allow-auto`. Cette étape ne vient qu'après : étapes 1 à 6 OK, une semaine de
fonctionnement en signaux seuls, revue de code des modules `risk/`, `execution/`, `monitoring/` par les deux
développeurs, puis un test automatique sur démo à 0,01 lot (réduire `risk_per_trade_pct`).

## Non validé sur MT5 à ce jour
Trailing et sortie partielle (validés sur simulateur seulement : il faudrait +20 pips de gain), reconnexion
après coupure du terminal, comportement des modes de remplissage selon le courtier.
