# Catalogue des règles d'un cours de trading (Forexagone, 2011) : ce qu'on peut en tirer pour ORIONX

**Statut :** brouillon préparatoire de H-3 · **Date :** 2026-11-03 · **Rien n'est testé ni codé.** Ce document ne change aucun comportement de l'agent.

## 1. Verdict

1. **On n'intègre pas ces règles « telles quelles » pour renforcer l'agent.** Ce sont des méthodes discrétionnaires, présentées avec des exemples choisis après coup (« chaque prise de position est
   couronnée par un succès », p. 93) et sans aucune statistique. Les ajouter à l'agent, c'est ajouter des hypothèses non testées : le contraire du principe n°10.
2. **On peut les transformer en hypothèses mesurables.** Chaque règle utile est réécrite comme une définition mécanique, figée avant de regarder les données, puis testée contre le hasard, avec les vrais coûts
   (spread et commission du compte IC Markets). Seules celles qui passent le protocole deviennent des stratégies.
3. **Beaucoup de ces idées sont déjà dans ORIONX, et ont déjà échoué** (H-1 et H-2 : moyennes mobiles et rebonds, oscillateurs en range, cassures). Les redemander sur les mêmes données, c'est « fouiller le hasard » (H-2 §9.4).
4. **Une partie du cours est précieuse sans être un signal** : gestion du risque, plan écrit, discipline. ORIONX les applique déjà, en plus strict.
5. **Une règle du cours est à exclure** : la martingale.

## 2. Le document source : précautions

- **Droit d'auteur.** Le PDF indique « © Forexagone 2011, ne peut être vendu ». Les méthodes de trading ne sont pas protégées, mais les textes et les images le sont. ORIONX pouvant devenir un produit, on ne copie
  ni phrase ni schéma : on réécrit nos propres définitions et notre propre glossaire.
- **Ton promotionnel.** « Extrêmement lucratif », « efficacité non contestable », « belles performances » : aucune preuve chiffrée. Le cours reconnaît lui-même que l'objectif est « de réussir à prévoir un peu mieux que
  le hasard » (p. 23).
- **Passages faux ou dépassés** à ne pas reprendre : « sans stratégie on gagne une fois sur deux, aucun intérêt » (p. 5, oublie que le spread fait perdre à tout système sans avantage : H-2 §9.3) ; levier « jusqu'à 500 »
  (limité à 30 pour un particulier en Europe) ; « vous ne pouvez pas perdre plus que votre mise » (dépend du courtier) ; niveau de Fibonacci « 31,2 % » dans le lexique (c'est 38,2 %).

## 3. Règle du cours -> situation dans ORIONX -> décision

| Règle du cours | Déjà dans ORIONX ? | Décision |
|---|---|---|
| Moyennes mobiles, rebonds en tendance, croisements | `trend_pullback` : **H-1 NO-GO** | Pas de nouveau test sur les mêmes données |
| RSI / stochastique en range (surachat, survente) | `mean_reversion` : **H-2 NO-GO** | Idem |
| Cassure de support / résistance | `breakout` : **H-2 NO-GO** | Idem |
| Bandes de Bollinger, ATR, volatilité | Descriptif dans l'analyse (régime de volatilité) | Candidat H-3e (volatilité, non directionnel) |
| Sessions (Londres/New York les plus actives) | Descriptif et filtre (échec comme filtre en H-1) | Garder descriptif |
| Chandeliers (avalement, harami, étoiles, marteau, doji, soldats/corbeaux) | Non | Candidat H-3a |
| Figures chartistes (double sommet, tête-épaules, biseaux, triangles, drapeaux) | Non | Candidat H-3c (version limitée) |
| Retracements de Fibonacci | Non | Candidat H-3b, **avec niveaux témoins** |
| Divergences RSI / cours | Non | Candidat H-3d |
| « Plan de trading » d'exemple (MMS 100, RSI, SAR, journalier) | Non | Candidat H-3f : tester le plan du cours tel quel |
| Money management (risque ≤ 3 % par trade, tableau de drawdown) | **Oui, en plus strict** (0,5 % par trade, arrêts, délai de 24 h) | Rien à ajouter |
| Plan de trading écrit, backtest avant de faire confiance, un seul timeframe | **Oui** (protocoles pré-enregistrés, H1) | Rien à ajouter |
| Psychologie (aversion aux pertes, excès de confiance) | L'agent n'a pas ces biais ; **l'utilisateur oui** | Argument pour garder l'approbation manuelle au début et le risque plafonné |

## 3 bis. Règles à exclure ou à utiliser autrement

- **Martingale (augmenter le risque après une perte) : exclue.** Elle fait monter le risque après les pertes, à l'inverse de `RiskParamGuard` (toute hausse de risque est retardée de 24 h) et des arrêts de drawdown. Le cours
  lui-même dit qu'elle « n'est pas viable à long terme » (p. 115).
- **Anti-martingale / TSSF (taille liée à la performance) : pas pour dimensionner.** Augmenter la taille quand ça va bien est aussi une hausse du risque. **En revanche, la courbe « gain moyen / perte moyenne
  nécessaire selon le taux de réussite » (p. 116 à 118) peut devenir un indicateur de santé** dans `signals-report` : savoir si le système est au-dessus ou en dessous du seuil de rentabilité. Il ne servirait qu'à
  alerter ou à réduire, jamais à augmenter.
- **Scalping et « trader les news » : exclus.** Le spread et le glissement les rendent peu viables (le cours le dit aussi) ; l'agent évite déjà les fenêtres d'annonce.
- **Levier élevé : exclu.** Les limites d'ORIONX portent sur le risque, pas sur le levier.

## 4. Candidats H-3 : définitions mécaniques (brouillon, à figer avant de regarder les données)

Notation : bougie (O, H, L, C), amplitude R = H - L, corps B = |C - O|, ombre haute U, ombre basse D. « Contexte baissier » : C < SMA(50) et SMA(50) plus basse qu'il y a 10 bougies ; « haussier » : l'inverse.
Aucun paramètre ne sera ajusté après avoir vu un résultat.

| Famille | Règle mécanique (exemples) | Mesure |
|---|---|---|
| **H-3a Chandeliers** | Doji : B ≤ 0,1 R. Marteau : B ≤ 0,3 R, D ≥ 2B, U ≤ 0,1 R, contexte baissier (pendu : haussier). Avalement haussier : t-1 baissière, t haussière, O(t) ≤ C(t-1), C(t) ≥ O(t-1). Harami : corps de t contenu dans le corps de t-1, sens opposé. Étoile du matin/soir : t-2 corps ≥ 0,6 R, t-1 corps ≤ 0,3 R, t clôture au-delà du milieu du corps de t-2. Trois soldats/corbeaux : 3 corps ≥ 0,6 R de même sens, clôtures croissantes. | Rendement net de coûts à 4, 12 et 24 bougies après le signal, dans le sens annoncé |
| **H-3b Fibonacci** | Jambe d'impulsion = mouvement ≥ 3 ATR entre deux pivots (zigzag à 3 ATR). Après la jambe, le prix revient dans la zone 38,2 / 50 / 61,8 % : reprend-il le sens de la jambe avant d'effacer 100 % ? | Taux de reprise sur les niveaux de Fibonacci **comparé à des niveaux témoins** (30, 45, 55, 70 %) : sans cette comparaison, le test ne prouve rien |
| **H-3c Figures** | Version limitée : double sommet / double creux (deux pivots à ± 0,5 ATR, creux intermédiaire ≥ 2 ATR) et tête-épaules, cassure confirmée par une clôture au-delà de la ligne de cou. Triangles, biseaux, drapeaux, diamant : trop subjectifs, hors test | Rendement net après la cassure, et atteinte de l'objectif théorique du cours |
| **H-3d Divergences** | Prix : deux plus bas successifs plus bas ; RSI(14) : plus bas successifs plus hauts (et inverse). Pivots du même zigzag | Rendement net à 12 et 24 bougies |
| **H-3e Volatilité** | Étranglement de Bollinger : largeur des bandes (20, 2) au plus bas 10 % des 250 dernières bougies. **Prédit-il une volatilité plus forte ensuite ?** Non directionnel | Volatilité réalisée des 12 bougies suivantes contre la volatilité habituelle |
| **H-3f Plan du cours** | Journalier : pente de la MMS 100 + RSI(14) > ou < 50 + inversion du parabolique SAR ; sortie à l'inversion inverse | Profit factor, drawdown, nombre de trades (peu de trades par symbole : agréger plusieurs symboles) |

**Observation utile** : la volatilité et les sessions sont des phénomènes bien documentés (la volatilité se regroupe dans le temps), alors que la **direction** des prix est ce qui a échoué jusqu'ici. H-3e est donc la piste
la plus plausible, et elle servirait à *dimensionner et filtrer*, pas à choisir le sens d'un trade.

## 5. Règles du protocole H-3 (à pré-enregistrer avant toute mesure)

- **Données.** Historique et spreads du compte démo IC Markets (la mémoire de marché les enregistre depuis le 31 oct.), **plusieurs symboles** (au moins 4) et plusieurs unités de temps ; **coûts réels** (spread
  mesuré + commission). Les anciennes données MetaQuotes (sans coûts) ne servent pas aux verdicts.
- **Réserve vierge** : les 6 derniers mois, ouverte **au plus une fois**, après le choix du candidat. Aucune règle n'est choisie en regardant la réserve.
- **Une famille = une hypothèse.** Dans une famille (par exemple tous les chandeliers), la statistique retenue est le **meilleur** de la famille, comparée à la même statistique obtenue sur des dates aléatoires
  (test de permutation du maximum). Cela évite de « trouver » un motif qui marche par hasard parmi vingt.
- **Budget** : 6 hypothèses au plus (H-3a à H-3f), arrêt si aucune ne passe. Seuils au moins aussi stricts que H-2 : meilleure que 97,5 % des tirages aléatoires, ≥ 100 signaux, résultat net de coûts positif dans
  au moins 3 blocs sur 4 **et** sur au moins 3 symboles sur 4, drawdown < 10 % pour les règles de trading.
- **Attendu honnêtement** : la probabilité qu'une de ces familles passe est faible. Un NO-GO sera un résultat utile, pas un échec.

## 6. Ce qui peut entrer dans ORIONX sans attendre H-3

- **Pédagogie (mode débutant du dashboard)** : un glossaire et des explications de chandeliers, de figures et de niveaux, **réécrits avec nos mots**, présentés comme de la description et jamais comme un conseil
  ni une prévision. C'est la partie la plus utile du cours pour des utilisateurs qui découvrent le marché.
- **Indicateur de santé du système** (§3 bis) dans `signals-report`.
- **Détection descriptive** (« une bougie d'avalement haussier vient de se former ») dans l'onglet d'analyse, sans aucun lien avec la décision de l'agent.

## 7. Étapes proposées

1. Valider ce catalogue (et retirer ce qui ne vous convient pas).
2. Laisser la mémoire de marché s'enrichir (plusieurs semaines de spreads IC Markets) et récupérer l'historique du courtier.
3. Écrire `H-3_protocole.md` : définitions figées, symboles, périodes, tirages aléatoires, seuils, hachage des fichiers (comme H-1 et H-2).
4. Coder les détecteurs (fonctions pures, testées) et le banc d'essai, **sans toucher** aux stratégies ni au risque.
5. Lancer, publier le résultat quel qu'il soit.
