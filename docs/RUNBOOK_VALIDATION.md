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

## 6 bis. Analyse de marché (M3 v1)
1. `copy calendar.example.csv calendar.csv` puis ajouter une annonce qui tombe dans 10 minutes (heure UTC !), et dans
   `config.yaml` : `analysis:` -> `calendar_path: calendar.csv`.
2. `python -m orionx.main state --config config.yaml` : doit afficher régime, séance, volatilité, ATR et l'annonce à venir.
   Comparer le régime avec le graphique H1 : est-ce cohérent à l'oeil ? Séance cohérente avec l'heure UTC actuelle ?
3. `python -m orionx.main run --config config.yaml` pendant la fenêtre de l'annonce ; `audit-tail` doit montrer
   `MarketStateUpdated`. Avec une position ouverte (`demo-trade --hold 600`) : `ThesisDegraded` puis un stop resserré.
4. `python -m orionx.main backtest --bars 15000 --filters none,vol,session,trend,all` (quelques minutes).

## 8. Modules M3 à M7 : ordre de test sur MT5 démo
Préparer : `copy config.example.yaml config.yaml` (garder l'ancien en sauvegarde), puis `symbols: [EURUSD]` et
`max_data_age_seconds: 10.0`. Toutes les commandes avec `--config config.yaml`, depuis `agent/`.

**8.1 Profils et verrouillage de 24 h** : `risk-status` ; `risk-set --profile prudent` ; `risk-set --profile dynamic`
(attendu « delayed », date à +24 h) ; `risk-status` (changement en attente) ; `risk-set --profile prudent` (« cancelled » : demande
en attente annulée, config active conservée ; « immediate » seulement si prudent est plus prudent que la config active).
**Faire les étapes 8.1 et 8.2 dans un dossier isolé** pour ne pas toucher à votre vraie config de risque :
`$env:ORIONX_DATA_DIR = "$env:TEMP\orionx_validation"` avant les commandes (VALIDÉ le 28 oct. 2026 : `docs/validation/V-M3M7_2026-10-28.md`).
**8.2 Démarrage** : `run --max-cycles 30 --config config.yaml` puis `audit-tail`, `audit-verify`.
Attendu : « Stratégies activées », `AgentStarted` avec `strategies`, journal « INTÈGRE ».
**8.3 Qualité d'exécution** : `demo-trade --hold 20`. Attendu : une ligne « Qualité d'exécution : N tentative(s), latence X ms,
glissement Y point(s) ». Noter X et Y : ils servent à régler `max_slippage_points`.
**8.4 Exposition par devise (simulation, aucun ordre)** : dans config.yaml, `risk:` -> `max_total_open_risk_pct: 2.0` (pas 4.0 : une hausse met toute la config en attente 24 h) et
`max_currency_exposure_pct: 1.0`. Dans MT5, ouvrir à la main un ACHAT GBPUSD de 6 lots avec un stop-loss à 20 pips sous
l'entrée (risque d'environ 1,2 % : le compte démo est à 100 000 $). Puis :
`risk-check --symbol EURUSD --side buy --config config.yaml` -> attendu REFUSÉ `CURRENCY_EXPOSURE` (déjà short USD) ;
`risk-check --symbol EURUSD --side sell --config config.yaml` -> attendu APPROUVÉ (vendre EURUSD = long USD, compense).
Fermer la position GBPUSD et remettre les valeurs de config.yaml après le test.
**8.5 Annonce et thèse dégradée** : `copy calendar.example.csv calendar.csv`, puis
`Add-Content calendar.csv "$((Get-Date).ToUniversalTime().AddMinutes(10).ToString('yyyy-MM-ddTHH:mm:ssZ')),EUR,high,Test BCE"` ;
dans config.yaml, `analysis:` -> `calendar_path: calendar.csv`. Fenêtre B : `run --config config.yaml`. Fenêtre A :
`demo-trade --hold 300` (OUI). Attendu dans `audit-tail` : `ThesisDegraded` (raison « annonce imminente ») puis `PositionAdjusted`
(`THESIS_STOP` : le stop est resserré, de moitié de la distance si la position n'est pas en gain, au point d'entrée si elle l'est ;
`THESIS_WATCH` seulement si le stop est déjà serré). VALIDÉ le 28 oct. (`docs/validation/V-8.5_annonce_2026-10-28.txt`).
Retirer ensuite la ligne de test du calendrier.
**8.6 Mode sûr** : `run --config config.yaml`, couper le Wi-Fi 45 s : `DataFrozen` puis `CircuitBreakerTripped` ; au retour : `DataRestored`.
**8.7 Backtests** (quelques minutes chacun) : `backtest --bars 15000 --strategy trend_pullback --config config.yaml`, idem avec
`mean_reversion`, `breakout`, `engine:trend_pullback,breakout`. Aucune stratégie n'a d'avantage démontré : ne rien activer sans le témoin aléatoire.
**8.8 Week-end (facultatif, un vendredi)** : `positions: weekend_flat: true`, une position démo ouverte, vérifier la fermeture vers 20h30 UTC.

## 9. Reconnexion et changement de compte (T-13)
**9.1 Coupure du terminal** (VALIDÉ le 28 oct. 2026) : `run --config config.yaml` en signaux seuls, compte démo. Fermer MetaTrader 5 (pas
l'agent). **MT5 se rouvre tout seul** en quelques secondes : c'est la bibliothèque MetaTrader5 qui relance le terminal quand l'agent se
reconnecte (pratique pour la reprise, mais l'agent rouvrira aussi un MT5 que vous avez fermé volontairement ; arrêtez l'agent d'abord).
Attendu sur Telegram, dans cet ordre : 🔴 « liaison MT5 en erreur », 🟠 « Liaison MT5 rétablie après N s » (N = durée depuis la 1re
erreur), puis 🟠 « Reprise normale » 30 cycles plus tard. Dans `audit-tail` : `CycleError`, `CircuitBreakerTripped` (GATEWAY_ERRORS),
`GatewayReconnected` (avec `down_seconds`), `GatewayRecovered`. `GatewayReconnectFailed` n'apparaît que si la 1re tentative échoue.
Aucune entrée n'est possible tant que le mode sûr dure. Vérifier que les positions ouvertes n'ont pas été touchées.
**9.2 Changement de compte** : MT5 se rouvrant seul en quelques secondes, on ne peut pas changer de compte « pendant la coupure ». On
teste directement le contrôle, qui s'exécute à CHAQUE cycle : agent lancé et MT5 ouvert, dans MT5 menu Fichier > Se connecter à un compte de
trading, choisir un AUTRE compte DÉMO (en ouvrir un : Fichier > Ouvrir un compte). Attendu en moins de 2 s : 🔴 « Agent ARRÊTÉ : compte
différent… », `AccountChanged` et `CircuitBreakerTripped` (ACCOUNT_CHANGED) dans `audit-tail`, message « ARRÊT DÉFINITIF » dans la
console (code de sortie 4), et aucune lecture ni modification de position du nouveau compte. Reconnecter ensuite le compte d'origine.
**9.3 Ne JAMAIS tester avec un compte réel** : le contrôle protège justement de ce cas, ne le provoquez pas.

## 10. Alertes Telegram (M8)
**10.1 Mise en service** : `pip install -e ".[alerts]"` (coffre Windows), puis `python -m orionx.main alert-setup` : créer le bot avec
@BotFather, lui envoyer `/start`, coller le jeton (saisie invisible). Le chat est trouvé automatiquement ; un message de test arrive.
Variante sans coffre : variables d'environnement `ORIONX_TELEGRAM_TOKEN` et `ORIONX_TELEGRAM_CHAT_ID` (jamais dans config.yaml).
**10.2 Test** : `alert-test` -> message reçu. Puis `run --config config.yaml` (le démarrage affiche « Alertes Telegram : activées ») ;
dans une autre fenêtre, `kill --level 1 --yes` -> alerte CRITIQUE « Arrêt d'urgence niveau 1 » sur le téléphone ; `clear-kill` ensuite.
**10.3 Coupure** : refaire §9.1 -> alertes « Liaison MT5 perdue », « liaison MT5 en erreur », « Liaison MT5 rétablie », « Reprise normale ».
**10.4 Semaine de signaux seuls (V-4)** : `alerts: signals: true` dans config.yaml pour recevoir chaque signal.

## 11. API locale (T-14)
**11.1** `python -m pip install -e ".[api]"`, puis `autonomy: semi_auto` dans config.yaml et `run --config config.yaml --api`.
Attendu : « API locale : http://127.0.0.1:8765/api/status (jeton dans data\api_token, jamais affiché ici) ».
**11.2** Dans une autre fenêtre (dossier `agent`) :
`$t = Get-Content data\api_token; Invoke-RestMethod http://127.0.0.1:8765/api/status -Headers @{Authorization="Bearer $t"}`
-> état de l'agent. Sans l'en-tête -> erreur 401.
**11.3** À l'arrivée d'un signal : `Invoke-RestMethod http://127.0.0.1:8765/api/signals -Headers @{Authorization="Bearer $t"}`, puis
`Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/signals/<id>/approve -Headers @{Authorization="Bearer $t"}`.
Attendu : ordre démo exécuté avec SL/TP, `SignalApproval` (via api) dans `audit-tail`. Une 2e approbation est refusée.
**11.4** Arrêt d'urgence : `Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/kill -Headers @{Authorization="Bearer $t"}
-ContentType application/json -Body '{"level":1}'` -> `KillSwitchActivated` et alerte Telegram ; `clear-kill` ensuite.
Ne jamais coller le contenu de `data\api_token` dans un message ou un ticket.

## 12. Dashboard web (M9)
**12.0 Voir le rendu sans MetaTrader** : `python -m orionx.main demo-dashboard` (dossier `agent`). Agent et courtier SIMULÉS : prix qui
bougent, signal à approuver, positions, annonces, arrêt d'urgence ; pastille jaune « DÉMO SIMULÉE ». N'utilise ni votre config, ni
votre journal, ni votre drapeau d'arrêt (dossier temporaire) et n'envoie aucun ordre à un courtier. Ctrl+C pour arrêter.
**12.1** `python -m pip install -e ".[api]"` (à refaire après le 28 oct. : ajoute `websockets`, sans lequel le flux en direct reste muet), `autonomy: semi_auto` dans config.yaml, puis
`python -m orionx.main run --config config.yaml --api --dashboard`. Le navigateur s'ouvre sur le dashboard (aucun jeton à saisir).
Si le navigateur se ferme : `python -m orionx.main dashboard` (dossier `agent`) le rouvre.
**12.2 À regarder** : pastille « en marche » (pas « agent injoignable »), « démo » dans Compte, équité et solde, pastille « en direct »
sur les événements ; arrêter MT5 doit faire passer la pastille en « mode sûr » puis les événements de reconnexion s'afficher.
**12.3 Signal** : à l'arrivée d'un signal, il apparaît avec Approuver / Rejeter ; une confirmation est demandée ; l'approbation envoie
un ordre démo avec stop et objectif, la position apparaît dans « Positions ouvertes ». Un signal expiré ne peut pas être approuvé.
**12.3 bis Analyse du marché** : la section « Analyse du marché » a 6 onglets (Tendance, Momentum, Volatilité, Niveaux, Séance et
annonces, Décision de l'agent) ; chacun commence par une phrase de lecture. Vérifier que le régime affiché correspond au journal
(`MarketStateUpdated`), que le RSI et le graphique suivent le marché (comparer avec MT5), que les annonces du calendrier
apparaissent (§8.5), et que « Décision » liste les stratégies applicables. Liens directs : `/#momentum`, `/#decision`…
**12.4 Arrêt d'urgence** : bouton niveau 1 + confirmation -> alerte Telegram et pastille « mode sûr » au cycle suivant ; `clear-kill` ensuite.
**12.5 Sécurité** : copier l'adresse du dashboard dans un autre navigateur (sans cookie) -> « Session expirée ». Relancer
`dashboard` -> le nouveau code fonctionne UNE fois (le recoller dans l'adresse échoue).

## 13. Ticket d'ordre guidé
**13.0 Sans MetaTrader** : `python -m orionx.main demo-dashboard`, carte « Ticket d'ordre guidé » : choisir Achat/Vente et un stop ; l'estimation
(risque maximal, gain visé, taille) se met à jour en direct. « Préparer le ticket » le place dans les signaux en attente.
**13.1 Sur MT5 démo** (marché ouvert, `autonomy: semi_auto`, `run --api --dashboard`) : préparer un ticket, vérifier les montants contre votre
calcul (capital × risque par trade ÷ distance du stop), approuver. Attendu : un seul ordre démo, **avec stop-loss** à la distance demandée
(onglet Trade de MT5), taille égale à celle annoncée, `TicketPrepared` puis `SignalApproval` (via api) dans `audit-tail`. **Réduire
`risk_per_trade_pct`** pour l'essai (0,5 % de 100 000 $ = 500 $ de risque, soit plusieurs lots) ; une baisse s'applique tout de suite.
**13.2 Refus attendus** : stop de 3 ou de 500 pips, gain visé de 0,5, mode `signals_only` (409), plus de 3 tickets en attente, agent arrêté ;
chaque refus affiche une phrase claire et ne laisse rien en attente. Le ticket expire au bout de 10 minutes.

## 7. NE PAS FAIRE avant validation complète
Activer `autonomy: auto` + `--allow-auto`. Cette étape ne vient qu'après : étapes 1 à 6 OK, une semaine de
fonctionnement en signaux seuls, revue de code des modules `risk/`, `execution/`, `monitoring/` par les deux
développeurs, puis un test automatique sur démo à 0,01 lot (réduire `risk_per_trade_pct`).

## Non validé sur MT5 à ce jour
Trailing et sortie partielle (validés sur simulateur seulement : il faudrait +20 pips de gain), reconnexion
après coupure du terminal (§9, codée et testée sur simulateur), comportement des modes de remplissage selon le courtier.
