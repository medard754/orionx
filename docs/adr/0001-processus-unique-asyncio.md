# ADR 0001 – Un seul processus asynchrone, bus d'événements interne

**Statut :** accepté · **Date :** 2026-10-12

**Contexte.** L'agent doit réagir en temps réel avec une latence faible et une complexité minimale.

**Décision.** Un seul processus Python (asyncio). Les modules communiquent par un bus d'événements typés
(`orionx/core/bus.py`) et par des interfaces (`Protocol`). Tous les appels MetaTrader 5 passent par un
unique thread (MT5 n'est pas thread-safe).

**Conséquences.** Simple et rapide. Un découpage en processus reste possible plus tard sans changer les contrats.
