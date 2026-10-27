# ADR 0005 – Garde-fous de risque ajoutés en M5

**Statut :** accepté (rédigé après coup, revue V-2) · **Date :** 2026-10-27

**Contexte.** M5 a modifié les garde-fous du risque (`core/config.py`, `risk/manager.py`, `risk/guard.py`) sans ADR,
contrairement à la règle du projet. Cette ADR consigne ces décisions. Détail des règles : `docs/risk_rules.md`.

**Décisions.**

1. **Exposition nette par devise** (`max_currency_exposure_pct`, défaut 2 %, borne absolue 4 % ajoutée à
   `ABSOLUTE_LIMITS`). Deux paires qui partagent une devise cumulent le même risque ; le lot est réduit tant qu'il
   reste de la place, puis refusé (`CURRENCY_EXPOSURE`). Une position opposée compense.
2. **Profils** `prudent` / `moderate` / `dynamic`, toujours à l'intérieur des bornes absolues. `dynamic` abaisse
   `min_rr` à 1,2 (minimum absolu 1,0). *Justification de 1,2 à confirmer par le propriétaire avant tout usage.*
3. **Rejets supplémentaires** : `WEEKEND_GUARD` (vendredi dès 18h UTC), `NEWS_BLACKOUT`, `HIGH_VOLATILITY`,
   `SESSION_BLOCKED`, seuil de spread par symbole.
4. **Verrouillage des paramètres** (`RiskParamGuard`) : toute hausse du risque, par `config.yaml` ou `risk-set`,
   attend 24 h ; une baisse s'applique tout de suite et annule la hausse en attente. Après la revue V-2 :
   - tout champ de `RiskConfig` non classé est traité comme plus risqué s'il change (un test impose le classement) ;
   - un redémarrage ne relance pas le délai, et ne remplace pas une autre demande en attente ;
   - un `risk_active.json` illisible fait refuser le démarrage ; les écritures sont atomiques ;
   - la décision de démarrage est journalisée (`AgentStarted.risk_startup`).

**Conséquences.** Ajouter un paramètre de risque impose de le classer dans `risk/guard.py` (plus ou moins risqué
quand il augmente) et de mettre à jour `docs/risk_rules.md`. Toute nouvelle borne absolue ou tout nouveau profil
demande une nouvelle ADR.
