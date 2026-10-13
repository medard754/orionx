# ORIONX – état du projet

**À mettre à jour à chaque fin de tâche.** Coller ce fichier au début d'une session de supervision.

Dernière mise à jour : 12 oct. 2026 · Phase : socle (mois 1) · Mode : démo uniquement

## Fait

- [x] Noyau : modèles immuables, configuration avec bornes absolues, bus d'événements
- [x] Gestionnaire de risques M5 v0 (15 règles, arrêts jour/semaine/drawdown/pertes consécutives)
- [x] Approbation signée HMAC + Executor M6 v0 (idempotent, correction ou fermeture si pas de stop-loss)
- [x] Courtier simulé, 47 tests automatisés, CLI `simulate`
- [x] `Mt5Gateway` écrit **mais non testé** sur un terminal réel

## À faire (ordre recommandé)

| ID | Tâche | Qui | Preuve que c'est fini |
|---|---|---|---|
| T-01 | Installer MT5 + compte démo ; valider `mt5-check` (compte, spec, tick, positions) | Dev A | Sortie de la commande collée dans la PR, corrections de `mt5_real.py` |
| T-02 | Premier ordre démo de bout en bout via `RiskManager` → `Executor` → `Mt5Gateway`, puis fermeture | Dev A | Position ouverte avec SL/TP visible dans MT5, puis fermée ; capture + logs |
| T-03 | Journal d'audit chaîné par hachage (SQLite) + vérificateur d'intégrité | Dev B | Test : modifier une ligne fait échouer la vérification |
| T-04 | M2 : collecte ticks/bougies (M1, M5, H1), stockage SQLite, détection de données périmées | Dev B | Test : tick gelé détecté en moins de 3 s |
| T-05 | Kill switch niveaux 1 à 3 (pause / protection / clôture totale) + commande CLI | Dev A + B | Test : niveau 3 clôture tout en moins de 5 s sur le simulateur |
| T-06 | Boucle principale asynchrone (orchestration des modules) | Dev B | `python -m orionx.main run` tourne 1 h en simulation sans erreur |
| T-07 | M7 : stop suiveur, breakeven, sorties partielles (le stop ne s'éloigne jamais) | Dev A | Tests sur le simulateur |
| T-08 | Calibrer le décalage horaire serveur du courtier (`Mt5Gateway.get_tick`) | Dev A | Détection de péremption correcte sur le démo |
| T-09 | Règles manquantes : exposition par devise, fenêtres d'annonces, week-end | Dev A | Tests + `docs/risk_rules.md` |
| T-10 | Passer `ruff` et `mypy --strict` en bloquant dans la CI | Dev B | CI verte sans `continue-on-error` |
| T-11 | Identifiants dans le coffre-fort Windows (`keyring`) au lieu de variables d'environnement | Dev B | Plus aucun mot de passe en clair |
| T-12 | Première stratégie à règles (tendance H1 + repli) produisant des `TradeProposal` | Dev A | Backtest sur le simulateur avec coûts |

## Décisions prises

- Agent local Windows, démo uniquement, un seul courtier cible pour commencer (à choisir : ______ ).
- Dataclasses dans le noyau, Pydantic aux frontières (ADR 0002).
- Pas d'IA avant que la chaîne risque → exécution soit validée en démo.

## Bloquants / questions ouvertes

- Choix du courtier et du compte démo.
- Avis juridique : rendez-vous à prendre.
- Disponibilité du nom ORIONX (domaine, marque).

## Rappel des jalons du plan de 6 mois

Mois 1 : l'agent lit un compte démo en direct · Mois 2 : agent en démo interne avec toutes les règles de risque.
