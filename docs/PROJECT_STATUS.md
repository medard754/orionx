# ORIONX – état du projet

**À mettre à jour à chaque fin de tâche.** Coller ce fichier au début d'une session de supervision.

Dernière mise à jour : 13 oct. 2026 · Phase : validation du socle (mois 1-2) · Mode : démo uniquement

## Fait

- [x] Noyau, gestionnaire de risques M5, approbation signée, Executor M6, courtier simulé
- [x] **T-01** lecture du compte démo MT5 validée · **T-02** premier ordre démo de bout en bout validé (13 oct. 2026)
- [x] **T-08** âge des données mesuré en heure locale (décalage serveur MT5) : validé sur MT5
- [x] Code écrit et testé sur simulateur (101 tests), **à valider sur MT5** via `docs/RUNBOOK_VALIDATION.md` :
  T-03 journal d'audit · T-04 données temps réel · T-05 arrêt d'urgence · T-06 boucle principale ·
  T-07 gestion des positions · T-12 stratégie tendance/repli + backtest

## À faire (ordre recommandé)

| ID | Tâche | Qui | Preuve que c'est fini |
|---|---|---|---|
| V-1 | Dérouler `RUNBOOK_VALIDATION.md` étapes 1 à 4 sur le démo (journal, arrêt d'urgence) | Dev A + B | Sorties et captures archivées dans `docs/validation/` |
| V-2 | Revue croisée du code `risk/`, `execution/`, `monitoring/`, `positions/` (chacun relit le code de l'autre) | Dev A + B | PR approuvées, liste des remarques traitées |
| V-3 | Backtest sur 3000+ bougies puis walk-forward sur plusieurs périodes ; analyser les trades perdants | Dev A | Rapport court : résultats, limites, décision go / no-go |
| V-4 | Une semaine de `run` en signaux seuls (aucun ordre) ; comparer les signaux au graphique | Dev B | Journal d'audit de la semaine, signaux revus |
| T-09 | Règles de risque manquantes : exposition par devise, fenêtres d'annonces, week-end | Dev A | Tests + `docs/risk_rules.md` |
| T-10 | `ruff` et `mypy --strict` bloquants dans la CI (le code n'a pas encore été passé au linter) | Dev B | CI verte sans `continue-on-error` |
| T-11 | Identifiants dans le coffre-fort Windows (`keyring`) | Dev B | Plus aucun mot de passe en clair |
| T-13 | Reconnexion automatique du terminal MT5 après coupure (aujourd'hui : mode safe, pas de reconnexion) | Dev A | Test de coupure sur démo |
| T-14 | Interface d'approbation pour `semi_auto` (API locale, puis dashboard) | Dev B | Approbation d'un signal depuis l'API |

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
