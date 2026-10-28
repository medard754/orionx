# Travailler sur ORIONX

## Flux Git

- `main` est protégée : uniquement par pull request, CI verte. Un seul développeur : pour `risk/`, `execution/`,
  `connector_mt5/` et `core/config.py`, revue `/code-review` puis relecture personnelle au moins 24 h plus tard (ADR 0004).
- Branches : `feat/T-04-collecte-ticks`, `fix/...`. Une branche = une tâche de `docs/PROJECT_STATUS.md`.
- Commits courts et clairs (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- Pull requests petites (moins de 400 lignes si possible).

## Définition de « terminé » pour une tâche

1. Le code + les tests dans la même PR.
2. `python -m unittest discover -s tests -t .` passe.
3. Cas limites traités (données périmées, erreur du courtier, valeurs extrêmes).
4. La documentation concernée est mise à jour (`docs/risk_rules.md`, ADR si décision d'architecture).
5. `docs/PROJECT_STATUS.md` est mis à jour.

## Revue de code : ce que l'on vérifie en priorité

- Un ordre peut-il partir sans stop-loss ou sans approbation signée ? (ce doit être impossible)
- Un identifiant peut-il se retrouver dans un log ou dans Git ?
- Que se passe-t-il si le courtier répond lentement, mal, ou plus du tout ?
- Le test aurait-il échoué avant la correction ?
