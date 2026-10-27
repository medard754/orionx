# ADR 0006 – Reconnexion au terminal MT5 et identité du compte

**Statut :** accepté · **Date :** 2026-10-27

**Contexte.** L'agent se rattache au compte DÉJÀ connecté dans le terminal MT5 (aucun identifiant dans le code). Deux
constats :

1. Après une coupure, l'agent passait en mode sûr (plus d'entrée) mais continuait d'appeler une liaison morte : il ne se
   rétablissait que si le terminal se réparait seul (T-13).
2. Le compte n'était contrôlé qu'au démarrage et pour OUVRIR un trade (`LIVE_DISABLED`). La gestion des positions
   (stops, fermetures, positions ouvertes à la main) s'exécutait à chaque cycle sans vérifier le compte. Une reconnexion,
   ou un changement de compte dans MT5 par l'utilisateur, pouvait donc faire gérer les positions d'un autre compte, y
   compris un compte réel (principe n°4).

**Décisions.**

1. **Identité du compte à chaque cycle.** `AccountState` porte le `login`. Le premier compte vu au démarrage est la
   référence. À chaque cycle, avant toute lecture ou action sur les positions : compte non démo, ou numéro différent
   => `AccountChangedError`, arrêt DÉFINITIF de l'agent (`aborted`), halte `ACCOUNT_CHANGED` jamais levée automatiquement,
   événement `AccountChanged` dans le journal, code de sortie 4. Un redémarrage manuel est nécessaire.
2. **Reconnexion automatique.** Après 3 erreurs de cycle de suite de type liaison (`GatewayError`, `OSError`), l'agent
   appelle `Gateway.reconnect()` (fermeture puis réouverture de la liaison, délai de 10 s par tentative). Attente
   croissante entre tentatives : 5, 10, 20, 40, puis 60 s. Une erreur de logique ne déclenche pas de reconnexion.
3. **Le mode sûr n'est pas levé par la reconnexion.** Il l'est seulement après 30 cycles réussis de suite (règle
   existante). Les prix vus avant la coupure ne sont pas considérés comme frais après la reconnexion.
4. **Journal** : `GatewayReconnectFailed` (1re tentative puis une sur 10), `GatewayReconnected` (tentatives, durée de la
   coupure), `AccountChanged`.

**Limites assumées.**

- Le drapeau d'arrêt d'urgence est traité AVANT la lecture du compte (comportement validé sur démo, conservé) : si le
  compte a changé, un arrêt d'urgence demandé ce même cycle agirait sur le compte courant. Cas jugé acceptable, car il
  réduit le risque et reste exceptionnel.
- Une perte de connexion entre le terminal et le courtier (le terminal lui-même reste ouvert) n'est pas couverte par la
  reconnexion : elle est traitée par le mode sûr « prix figés » (M5 F5.9).
- Le profit d'une position fermée pendant la coupure reste approximé par son dernier instantané connu.
- Validé sur simulateur seulement : voir `docs/RUNBOOK_VALIDATION.md` §9 pour le test sur MT5 démo.
