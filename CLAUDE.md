# ORIONX — mémoire du projet (lue automatiquement par Claude Code)

## Le projet
ORIONX est un agent de trading IA qui tourne en local sous Windows et se connecte à un MetaTrader 5 déjà ouvert.
Il analyse le marché en temps réel, propose des trades, applique une gestion du risque stricte, exécute et gère les positions.
Mode **démo uniquement** pour l'instant (le mode live est refusé par le code).
Équipe : le propriétaire (francophone), seul développeur (ADR 0004). Répondre en français.
Code du projet : `agent/orionx/` · tests : `agent/tests/` · docs : `docs/` (lire `docs/PROJECT_STATUS.md` en premier).

## Principes non négociables
1. L'IA propose, le code déterministe décide.
2. Seul `Executor` envoie des ordres ; seul un `ApprovedOrder` signé (HMAC-SHA256) par `RiskManager`, à courte expiration, peut être exécuté.
3. Aucun ordre sans stop-loss. Un stop n'est jamais éloigné du prix.
4. Démo uniquement : tout compte non démo est refusé.
5. Exécution automatique verrouillée : `autonomy: auto` ET `--allow-auto` sont requis.
6. Toute augmentation de risque est retardée de 24 h (`RiskParamGuard`) ; le démarrage ne contourne pas ce délai.
7. Les arrêts (perte jour/semaine, drawdown, pertes consécutives, erreurs gateway, kill switch, données périmées) sont publiés ; le drawdown n'est jamais libéré automatiquement.
8. Heure serveur du broker (EET, UTC+2 hiver / +3 été) jamais comparée à l'horloge locale : utiliser `Tick.received_at`.
9. Journal d'audit chaîné par hachage (`audit-verify`) ; chaque action sensible y est écrite.
10. Ne pas ajuster les paramètres pour embellir un backtest.

## Environnement et commandes
- Windows, Python 3.12, venv dans `agent/.venv` ; installation : `pip install -e ".[dev,mt5]"`.
- MT5 : compte **démo Hedging**, terminal déjà connecté (pas d'identifiants dans le code). Magic number `ORIONX_MAGIC = 261012`.
- Dépôt Windows : `C:\Users\medar\Documents\orionx\orionx\` (code dans `agent`).
- Tests (336 sur simulateur) : `python -m unittest discover -s tests -t .` depuis `agent/`.
- Commandes `python -m orionx.main` : simulate, mt5-check, demo-trade, run, kill, clear-kill, audit-verify, audit-tail, backtest, state, risk-set, risk-status, risk-check, alert-setup, alert-test, dashboard, demo-dashboard (démonstration sans MT5).
- Config : copier `config.example.yaml` en `config.yaml` (jamais versionné) ; ne pas dupliquer la section `risk:`.

## État des modules
| Module | État |
|---|---|
| M1 Connecteur MT5 | validé sur démo |
| M2 Données | partiel |
| M3 Analyse (régimes, sessions, calendrier, thèse) | simulateur seulement, à valider sur MT5 |
| M4 Décision (stratégies, conflits, pyramiding) | simulateur seulement |
| M5 Risque | partiellement validé (test d'exposition à refaire avec un SL de 20 pips) |
| M6 Exécution | demo-trade validé |
| M7 Gestion des positions | kill switch validé ; trailing non validé |
| M8 Alertes (Telegram) | codé et testé (simulateur + faux Telegram) ; à mettre en service : `alert-setup` (RUNBOOK §10) |
| M9 Dashboard web puis mobile | web local codé, testé et essayé en réel (`run --api --dashboard`, ADR 0008) ; section « Analyse du marché » à 6 onglets (28 oct., RUNBOOK §12.3 bis) ; application mobile non codée |
| M10 Journal/audit | lecture validée |
| M11 Comptes, licences, paiements | non codé |

## Faits clés
- **Aucun avantage statistique démontré** : H-1/V-3 du 27 oct. 2026 = NO-GO (EURUSD H1, 26 855 barres de développement, `none` PF 0,86, drawdown 44,7 %, meilleur que 12 % des tirages aléatoires ; `vol`, `session`, `trend`, `all` échouent aussi). 4 échecs sur 5 ; réserve de 6 mois jamais ouverte. Voir `docs/validation/H-1_protocole.md` §9.
- **H-2 (28 oct. 2026) = NO-GO aussi** : `mean_reversion` (PF 0,94, DD 11,6 %), `breakout` (PF 0,89, DD 35,5 %), routeur complet (PF 0,92, DD 35,4 %). 7 hypothèses testées, 0 retenue ; réserve toujours vierge. Ne pas continuer à chercher sur ces données (H-2 §8 et §9.4) : toute suite = nouveau protocole H-3 (autres données, symboles ou nature de signal). Voir `docs/validation/H-2_protocole.md`.
- Le compte MetaQuotes-Demo n'a ni spread ni commission : les résultats y sont trop optimistes.
- Weekend : `weekend_guard` bloque les entrées à partir du vendredi 18h UTC ; les tests de marché demandent un marché ouvert.
- Retcodes MT5 : 10004/10020/10021 réessai sûr ; 10012/10031 ambigus (ne jamais renvoyer, chercher la position par commentaire `orionx:<proposal_id>`) ; 10027 trading algo désactivé ; 10030 mode de remplissage.
- Protocole stratégie : au plus 5 hypothèses pré-enregistrées, 6 derniers mois en réserve, critères PF > 1,2 sur 3 blocs sur 4, drawdown < 10 %, meilleur que 95 % des tirages aléatoires, arrêt après 5 échecs.

## Conventions de travail
- Dataclasses figées, Pydantic seulement aux frontières API/IA ; asyncio mono-processus ; EventBus typé ; SQLite.
- Chaque changement : tests + mise à jour de `docs/PROJECT_STATUS.md`. Commits en français, style `type: message`.
- Ne jamais écrire de secrets dans le dépôt. Ne pas toucher aux garde-fous du risque sans ADR dans `docs/adr/`.

## Reste à faire
1. Valider M3 à M7 sur MT5 démo (`docs/RUNBOOK_VALIDATION.md` §8, tests 1 à 7).
2. Committer le travail non versionné (analysis, decision, risk/guard, riskcheck, stratégies, tests) après les tests.
3. Relecture personnelle de la PR (ADR 0004). V-3/H-1 faits (NO-GO) ; V-4 seulement comme test de plomberie. Décider de la 5e hypothèse ou d'une nouvelle stratégie (H-2, nouveau protocole pré-enregistré).
4. T-10 ruff/mypy bloquants, T-11 coffre Windows pour les identifiants, T-13 reconnexion MT5, T-14 API locale (FastAPI + WebSocket).
5. M8 alertes, puis M9 dashboard, puis M11. Couche IA seulement après mesures. Prévoir un compte démo avec spreads réels.
