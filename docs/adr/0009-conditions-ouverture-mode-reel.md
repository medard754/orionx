# ADR 0009 – Conditions d'ouverture du mode réel (usage sur son propre compte)

**Statut :** proposé (brouillon, **ne change aucun comportement du code**) · **Date :** 2026-10-30

**Contexte.** Le but du projet est d'utiliser ORIONX, un jour, sur le compte réel de son propriétaire. Aujourd'hui, trois verrous
l'interdisent : `mode: demo` seul accepté (`core/config.py`), refus de démarrer sur un compte non démo et arrêt `ACCOUNT_CHANGED` si
le compte devient non démo (`agent.py`). Les retirer touche aux garde-fous du risque : une ADR est obligatoire (`CLAUDE.md`).

État des preuves au 30 octobre 2026 :
- **Aucun avantage statistique démontré** : H-1 et H-2 sont NO-GO (7 hypothèses, réserve de 6 mois toujours vierge).
- Le démo MetaQuotes n'a ni spread ni commission : les résultats y sont trop optimistes.
- Modules validés sur démo : connecteur, exécution, kill switch, reconnexion, changement de compte, exposition par devise, annonces.
  Restent : le test du vendredi (8.8) et V-4 (signaux seuls, rejeu `signals-report`).
- Relecture : un seul développeur (ADR 0004) ; l'ADR 0004 exige un relecteur humain externe avant tout compte réel.

**Pourquoi l'écrire maintenant.** Fixer les conditions *avant* d'en avoir envie, comme le principe n°10 interdit de régler les critères
après avoir vu les résultats. Cette ADR ne déverrouille rien : elle liste les portes à franchir, dans l'ordre.

## Décision proposée

Le mode réel ne s'ouvre que si **toutes** les portes ci-dessous sont franchies, constatées par écrit dans `docs/validation/`, et si
l'ADR passe au statut « accepté » par une décision séparée, **au moins 7 jours** après sa rédaction (relecture à froid, comme les 24 h
de `RiskParamGuard`).

**P1. Validations démo complètes.** RUNBOOK §8 entier (dont 8.8, vendredi), §9 à §14, V-4 faite (≥ 30 signaux résolus).

**P2. Ce qu'on prétend et ce qu'on ne prétend pas.** Le réel n'est pas ouvert parce que l'agent « gagne » : ce n'est pas démontré.
Première phase = **copilote de risque** : le propriétaire choisit le sens et le stop (ticket guidé), ORIONX calcule la taille et refuse
ce qui dépasse les limites. L'exécution automatique (`autonomy: auto`) reste verrouillée tant qu'une stratégie n'a pas passé un protocole
pré-enregistré (PF > 1,2 sur 3 blocs sur 4, drawdown < 10 %, meilleure que 97,5 % des tirages aléatoires, **puis** réserve ouverte une seule
fois, **puis** V-4 positive avec intervalle ne contenant pas 0). Une perte reste possible, y compris totale : le risk manager la limite, il ne l'écarte pas.

**P3. Coûts réels mesurés.** Compte démo du **même courtier** que le futur compte réel, avec ses vrais spreads et commissions ; backtests
et rejeu refaits avec ces coûts, sans toucher aux paramètres pour embellir (principe n°10).

**P4. Relecture humaine externe** de `risk/`, `execution/`, `connector_mt5/`, `core/config.py`, de l'API locale et du code de déverrouillage
décrit en P6 (ADR 0004). Remarques tracées et traitées.

**P5. Limites de la première phase** (écrites dans `config.yaml`, jamais plus larges, relevées seulement par `RiskParamGuard` avec son délai de 24 h) :
somme qu'on accepte de perdre **en totalité** ; `risk_per_trade_pct ≤ 0,25` ; `max_open_positions = 1` ; perte jour ≤ 1 %, semaine ≤ 2 %,
drawdown ≤ 5 % ; `semi_auto` seulement ; mêmes arrêts que sur démo, drawdown jamais libéré automatiquement.

**P6. Déverrouillage technique (à coder dans une PR séparée, sous cette ADR).** Un compte réel n'est accepté que si **tout** est vrai :
1. `mode: real` dans `config.yaml` **et** option de lancement explicite `--allow-real` ;
2. le numéro de compte connecté est dans une liste blanche de `config.yaml` (un seul numéro) ;
3. confirmation tapée à chaque démarrage (« je risque de l'argent réel sur le compte N ») ;
4. le passage à `mode: real` est lui-même une augmentation de risque : retardé de 24 h par `RiskParamGuard` ;
5. l'entrée est écrite dans le journal d'audit et un message part sur Telegram avec le numéro de compte, à chaque démarrage ;
6. tout changement de compte pendant l'exécution reste un arrêt définitif (ADR 0006) ;
7. tests : chaque verrou est testé isolément (un seul manquant = refus), et un test garantit que sans ces conditions un compte réel est refusé comme aujourd'hui.

**P7. Répétition.** Avant le premier ordre réel : kill switch (niveaux 1 à 3), reconnexion et alerte rejoués de bout en bout ; premier ordre
réel = **0,01 lot**, avec stop, surveillé en direct, comme `demo-trade`.

**P8. Retour automatique à la prudence.** Après tout arrêt d'urgence, ou 4 pertes consécutives, ou écart entre résultat réel et rejeu :
retour au démo, analyse écrite dans `docs/validation/`, nouvelle décision pour repartir. Pas de « se refaire ».

**P9. Périmètre juridique.** Compte du propriétaire **uniquement**, aucun fonds de tiers, aucun signal diffusé comme conseil. Toute ouverture
à des tiers (abonnements, M11) exige un avis juridique préalable : c'est un autre sujet, une autre ADR.

## Options écartées
- **Rester en démo pour toujours** : sans danger, mais l'objectif du projet est l'usage réel ; la phase copilote en petit montant le permet prudemment.
- **Ouvrir le réel dès que V-4 est positive** : trop mince (une semaine, coûts du démo, relecture seul).
- **Un simple drapeau `--live`** : un seul geste d'inattention suffit ; d'où les verrous cumulés de P6.

## Conséquences
- Le réel reste refusé par le code tant que P1 à P9 ne sont pas constatées et que la PR de P6 n'est pas fusionnée.
- Travail à prévoir : compte démo avec vrais spreads (P3), relecteur externe (P4), code de déverrouillage et ses tests (P6).
- Cette ADR sera revue si un second développeur rejoint le projet ou si une stratégie passe un protocole.
