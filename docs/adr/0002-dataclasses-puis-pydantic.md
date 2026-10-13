# ADR 0002 – Dataclasses dans le noyau, Pydantic aux frontières

**Statut :** accepté · **Date :** 2026-10-12

**Décision.** Les objets du domaine (`core/models.py`) sont des `dataclass(frozen=True)` validées dans
`__post_init__`, sans dépendance externe. Pydantic v2 sera utilisé aux FRONTIÈRES : API de l'agent (M9) et
sorties de l'IA / du LLM (M3), où la validation de données non fiables est indispensable.

**Pourquoi.** Le noyau (risque, exécution) doit rester minimal et testable sans installation lourde.
