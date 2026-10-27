# ADR 0004 – Relecture du code critique avec un seul développeur

**Statut :** accepté · **Date :** 2026-10-27

**Contexte.** Le projet n'a plus de second développeur. La règle « toute modification de `risk/`, `execution/`,
`connector_mt5/` ou `core/config.py` est relue par l'autre développeur » ne peut plus être appliquée, et GitHub
n'autorise pas l'auteur d'une pull request à l'approuver lui-même.

**Décision.** La relecture croisée est remplacée, pas supprimée. Toute pull request qui touche ces chemins exige :

1. une revue de code automatisée par l'assistant (Claude Code, `/code-review`), dont les remarques sont tracées dans
   la pull request et traitées une par une ;
2. une relecture par le développeur **au moins 24 h après** la dernière modification (même logique que
   `RiskParamGuard`) ;
3. la CI verte ; aucun test de règle de risque assoupli.

Sur GitHub, `main` reste protégée (pull request obligatoire, CI verte), sans approbation obligatoire.

**Limite assumée.** Une revue automatisée ne vaut pas un second humain : elle peut manquer une erreur de logique
métier. Avant tout passage à un compte réel, un relecteur humain externe relira `risk/`, `execution/` et
`connector_mt5/`. Cette ADR sera revue si un second développeur rejoint le projet.
