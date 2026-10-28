# ADR 0007 – API locale (T-14)

**Statut :** accepté · **Date :** 2026-10-27

**Contexte.** En `semi_auto`, un signal attend une approbation humaine. Seul un appel de code pouvait l'approuver. Le
dashboard (M9) aura besoin d'une interface. Une API qui peut déclencher des ordres est une surface d'attaque, même sur un
seul PC : autres programmes locaux, pages web malveillantes qui visent `localhost`, fuite du jeton dans des journaux.

**Décisions.**

1. **Aucun pouvoir propre.** L'approbation appelle `Agent.approve` : le risque est ré-évalué avec les prix du moment, seul
   un `ApprovedOrder` signé atteint l'Executor (principe n°2). L'approbation est refusée si l'agent est arrêté
   (`aborted`) ou si le compte a changé (contrôle de l'ADR 0006 ajouté à `approve`, qui en manquait). L'arrêt d'urgence
   écrit le même drapeau que la commande `kill`.
2. **Local uniquement** : écoute sur `127.0.0.1`, jamais sur le réseau. En-tête `Host` vérifié (`127.0.0.1`, `localhost`)
   contre le DNS rebinding.
3. **Jeton obligatoire** : aléatoire (256 bits), régénéré à chaque démarrage, écrit dans `data/api_token` (hors du dépôt),
   **jamais affiché** dans la console. `Authorization: Bearer` pour le HTTP, `?token=` pour le WebSocket (les navigateurs ne
   peuvent pas y mettre d'en-tête). Comparaison à temps constant.
4. **Pas de CORS, pas de pages publiques** (`/docs`, `/openapi.json` désactivés), **pas de journal des requêtes** (le jeton
   du WebSocket y apparaîtrait).
5. **Même processus, même boucle** que l'agent (ADR 0001) ; Ctrl+C reste à l'agent. Le flux d'événements (`EventFeed`) est en
   bibliothèque standard : l'agent ne dépend pas de FastAPI, l'API est facultative (`pip install -e ".[api]"`, `run --api`).
6. **Journal** : chaque décision humaine est écrite (`SignalApproval`, `SignalRejectedByUser`, avec `via`).

**Limites assumées.** Un programme malveillant déjà exécuté sous votre compte Windows peut lire `data/api_token` : l'API ne
protège pas contre lui (même limite que l'ADR 0003). Pas de HTTPS : inutile en boucle locale. Validé sur simulateur et sur un
vrai serveur local ; à valider avec l'agent sur MT5 démo (`RUNBOOK_VALIDATION.md` §11).
