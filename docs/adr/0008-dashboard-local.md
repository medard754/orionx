# ADR 0008 – Dashboard web local (M9)

**Statut :** accepté · **Date :** 2026-10-27

**Contexte.** Le dashboard doit afficher l'état de l'agent, les signaux et permettre l'approbation et l'arrêt d'urgence depuis
un navigateur. Une page web qui peut déclencher des ordres expose de nouvelles attaques que l'API seule n'avait pas : une
autre page ouverte dans le même navigateur peut tenter d'appeler `127.0.0.1` (CSRF, détournement de WebSocket, clickjacking),
et du texte venant de l'API (motifs, messages d'erreur du courtier) ne doit jamais devenir du code (XSS).

**Décisions.**

1. **Le navigateur ne voit jamais le jeton de l'API (ADR 0007).** `run --api --dashboard` (ou la commande `dashboard`) obtient un
   **code à usage unique, valable 60 s**, ouvre `/login?code=…` ; le serveur l'échange contre un **cookie de session**
   `HttpOnly` + `SameSite=Strict` (illisible par le JavaScript, non envoyé par un autre site). Le code ne s'obtient qu'avec le
   jeton : une session ne peut pas en fabriquer d'autres. Sessions et codes sont en mémoire : tout expire au redémarrage.
2. **Anti-CSRF** : toute action (méthode autre que GET) faite par cookie exige l'en-tête `X-Requested-By: orionx-dashboard`, qu'un
   formulaire ou une image d'un autre site ne peut pas envoyer. Le jeton Bearer des scripts n'est pas concerné.
3. **WebSocket** : accepté avec le jeton, ou avec la session SI l'en-tête `Origin` est celui du dashboard (anti détournement).
4. **Politique de sécurité de contenu stricte** : `default-src 'none'`, scripts et styles de notre propre origine seulement, aucun
   script ni style en ligne, `frame-ancestors 'none'` + `X-Frame-Options: DENY` (pas de cadre : anti clickjacking),
   `connect-src` limité à notre origine, `nosniff`, `no-referrer`, `no-store`.
5. **Aucune injection de HTML** : le script n'écrit que par `textContent` ; un test interdit `innerHTML`, `eval`, `document.write`,
   les scripts et styles en ligne. Aucune bibliothèque externe (pas de CDN, pas de chaîne d'approvisionnement).
6. **Confirmation humaine** avant chaque approbation et chaque arrêt d'urgence. Le dashboard n'a aucun pouvoir propre : ses actions
   sont celles de l'API (ADR 0007 : risque ré-évalué, ordre signé, journal d'audit).

**Limites assumées.** Même niveau de confiance que l'ADR 0007 : un programme malveillant qui s'exécute déjà sous votre compte Windows
peut lire `data/api_token` ou la mémoire du navigateur. Les pages sont servies en HTTP clair sur la boucle locale (pas de HTTPS,
inutile ici). Le dashboard ne remplace pas les alertes Telegram : il n'est consulté que lorsque vous le regardez.
Validé sur simulateur et sur un serveur local réel ; à valider dans un vrai navigateur avec l'agent (`RUNBOOK_VALIDATION.md` §12).
