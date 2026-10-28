# ORIONX – état du projet

**À mettre à jour à chaque fin de tâche.** Coller ce fichier au début d'une session de supervision.

Dernière mise à jour : 27 oct. 2026 · Phase : validation M3 à M7 sur MT5 démo · Mode : démo uniquement

## Fait

- [x] Noyau, gestionnaire de risques M5, approbation signée, Executor M6, courtier simulé
- [x] **T-01** lecture du compte démo MT5 validée · **T-02** premier ordre démo de bout en bout validé (13 oct. 2026)
- [x] **T-08** âge des données mesuré en heure locale (décalage serveur MT5) : validé sur MT5
- [x] Code écrit et testé sur simulateur (101 tests), **à valider sur MT5** via `docs/RUNBOOK_VALIDATION.md` :
  T-03 journal d'audit · T-04 données temps réel · T-05 arrêt d'urgence · T-06 boucle principale ·
  T-07 gestion des positions · T-12 stratégie tendance/repli + backtest

- [x] **M3 v1 analyse de marché** (14 oct. 2026) : régime, volatilité, séance, calendrier d'annonces, surveillance de la thèse
  des positions ouvertes, filtres testables par backtest. Testé sur simulateur (136 tests), **à valider sur MT5**
  (`RUNBOOK_VALIDATION.md` §6 bis).
- [!] Backtest EURUSD H1 sur 15 000 bougies (14 oct.) : PF 0,90, net -1 920 $, équivalent au hasard (60e centile). Aucun
  avantage démontré : l'exécution automatique reste verrouillée.
- [!] **H-1/V-3 terminé : NO-GO** (27 oct. 2026). Protocole pré-enregistré, 26 855 bougies de développement (2021-2026), 4 blocs,
  40 tirages aléatoires : `none` PF 0,86 / DD 44,7 % ; `vol`, `session`, `trend`, `all` échouent tous aux critères. Réserve de 6 mois
  **jamais ouverte**. 4 échecs sur 5 autorisés. Détail : `docs/validation/H-1_protocole.md` §9.

- [x] **M4 à M7 complétés** (16 oct. 2026) : moteur de décision (3 stratégies, régimes, conflits, classement par devise),
  risque (exposition par devise, week-end, profils, verrouillage 24 h, mode sûr), exécution (tentatives sûres, réponses
  ambiguës, glissement, qualité), positions (ATR, week-end, annonces, positions manuelles). 222 tests sur simulateur.
  **À valider sur MT5** : `RUNBOOK_VALIDATION.md` §8. Détail : `docs/MODULES_M4_M7.md`.

- [x] **M8 alertes Telegram** (27 oct. 2026) : 3 niveaux (critique, important, info), envoi dans un fil dédié (jamais bloquant),
  anti-répétition et limite de débit, jeton dans le coffre Windows ou l'environnement, jamais dans le dépôt. Signaux en option
  (`alerts: signals: true`, pour V-4). Testé sur simulateur et faux Telegram. **Bot @orionx_trader_bot connecté le 27 oct.** (jeton dans le coffre Windows) ;
  reste le test de bout en bout avec l'agent : `RUNBOOK_VALIDATION.md` §10.2.

## À faire (ordre recommandé)

| ID | Tâche | Preuve que c'est fini |
|---|---|---|
| V-1 | Dérouler `RUNBOOK_VALIDATION.md` étapes 1 à 4 sur le démo (journal, arrêt d'urgence) | Sorties et captures archivées dans `docs/validation/` |
| V-2 | Revue du code `risk/`, `execution/`, `monitoring/`, `positions/` : `/code-review` puis relecture personnelle 24 h plus tard (ADR 0004). **27 oct. : revue faite, 9 corrections + ADR 0005 sur la PR #1 ; reste la relecture personnelle** | Remarques tracées dans la PR et traitées |
| V-3 | ~~Backtest + walk-forward + trades perdants~~ : fait le 27 oct., **NO-GO** (`docs/validation/H-1_protocole.md` §9) | — |
| V-4 | Une semaine de `run` en signaux seuls (aucun ordre) ; comparer les signaux au graphique | Journal d'audit de la semaine, signaux revus |
| T-09 | ~~Règles de risque manquantes~~ : faites (M5). Reste : corrélations entre devises plus fines | — |
| H-1 | ~~Comparer les filtres~~ : fait, 4 échecs sur 5. **Reste 1 hypothèse** avant l'arrêt de `trend_pullback`. Ne pas la dépenser sans y réfléchir : la réserve est encore vierge | Décision du propriétaire : 5e hypothèse, ou nouvelle stratégie (protocole H-2) |
| T-10 | ~~`ruff` et `mypy --strict` bloquants dans la CI~~ : fait le 27 oct. (0 erreur, 0 `type: ignore` ajouté) | — |
| T-11 | Identifiants dans le coffre-fort Windows (`keyring`) | Plus aucun mot de passe en clair |
| T-13 | Reconnexion automatique du terminal MT5 : **codée et testée sur simulateur** (27 oct., ADR 0006, avec contrôle du compte à chaque cycle). Reste le test de coupure sur démo (`RUNBOOK_VALIDATION.md` §9) | Coupure réelle du terminal journalisée, mode sûr puis reprise |
| T-14 | Interface d'approbation pour `semi_auto` (API locale, puis dashboard) | Approbation d'un signal depuis l'API |

## Décisions prises

- Agent local Windows, démo uniquement, un seul courtier cible pour commencer (à choisir : ______ ).
- Dataclasses dans le noyau, Pydantic aux frontières (ADR 0002).
- Pas d'IA avant que la chaîne risque → exécution soit validée en démo.
- Un seul développeur (27 oct. 2026) : la relecture croisée devient `/code-review` + relecture différée de 24 h (ADR 0004).

## Bloquants / questions ouvertes

- Choix du courtier et du compte démo.
- Avis juridique : rendez-vous à prendre.
- Disponibilité du nom ORIONX (domaine, marque).

## Rappel des jalons du plan de 6 mois

Mois 1 : l'agent lit un compte démo en direct · Mois 2 : agent en démo interne avec toutes les règles de risque.
