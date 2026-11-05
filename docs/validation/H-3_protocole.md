# H-3 – Protocole pré-enregistré (règles d'un cours de trading, mécanisées ; plusieurs marchés, coûts réels Pepperstone)

**Écrit le 3 nov. 2026, AVANT toute mesure de H-3.** Commité et poussé avant l'outillage, avant le gel des données et avant la première exécution : toute modification
ultérieure doit apparaître dans l'historique git et être justifiée ici. Suite de `H-1_protocole.md` et `H-2_protocole.md` (NO-GO, 7 échecs sur 7). Le catalogue de départ est
`H-3_catalogue_regles_cours.md` (même date, avant toute mesure).

Les champs marqués **[À FIGER]** seront remplis au gel des données (§9, étape 3), dans un commit distinct et daté, **avant** de calculer quoi que ce soit.

## 0. Avertissement honnête

La probabilité a priori qu'une de ces familles passe est **faible** : ce sont des règles de prix connues de tous, sur des marchés très efficients, avec des coûts réels (spread et commission) qui
absorbent une part importante d'un mouvement d'une à quelques heures. **Un NO-GO est le résultat le plus probable.** Ce protocole existe pour que, si un résultat positif apparaît, il soit crédible,
et pour que l'échec éventuel soit documenté proprement. Il n'est pas là pour obtenir un « oui ».

## 1. Question

Une des six familles de règles du catalogue (chandeliers, Fibonacci, figures, divergences, étranglement de Bollinger, plan journalier du cours), écrites sous forme mécanique **avant** de regarder les données,
a-t-elle un avantage mesurable, **net des coûts réels**, meilleur que le hasard, sur des marchés et des périodes jamais regardés ?

## 2. Données

- **Source unique** : historique de bougies du compte démo **Pepperstone** (serveur `Pepperstone-Demo`, société Pepperstone Group Limited, compte en USD, Hedging), lu avec MetaTrader 5.
  **Changement de courtier, décidé le 4 nov. 2026 AVANT toute mesure** : le protocole prévoyait d'abord IC Markets, injoignable depuis ce poste (connexion de trading refusée, nom de serveur d'abord mal saisi, puis VPN actif) ;
  aucune bougie ni aucun résultat n'avait été lu. Le courtier ne change ni les définitions, ni les seuils, ni le budget de ce protocole. Les anciennes données MetaQuotes (sans coûts) **ne servent à aucun verdict**.
- **Symboles de décision** : **GBPUSD, USDJPY, AUDUSD, USDCAD**, jamais regardés dans H-1 ni H-2. **EURUSD** est calculé et rapporté pour information, mais **ne compte pas dans la décision** : cette série a déjà
  servi à 7 hypothèses (un résultat flatteur dessus serait suspect).
- **Unités de temps** : **H1** (familles a à e) et **D1** (famille f). Pas d'autre unité de temps.
- **Étendue** : tout l'historique que le terminal fournit, jusqu'à la dernière bougie complète avant la date de gel (la bougie en formation est écartée). Le terminal plafonne à environ 100 000 bougies H1 par symbole, soit environ 16 ans.

**Gel effectué le 4 nov. 2026 à 21:13 UTC** (heures ci-dessous = heure serveur du courtier ; manifeste `H-3_snapshot_manifest.json`, empreintes sha256 par fichier, vérifiées après écriture) :

| Symbole | H1 : bougies, du -> au | D1 : bougies, du -> au |
|---|---|---|
| GBPUSD | 100 006 : 2010-09-27 -> 2026-11-04 | 4 612 : 2010-01-04 -> 2026-11-03 |
| USDJPY | 100 005 : 2010-09-28 -> 2026-11-04 | 4 613 : 2010-01-04 -> 2026-11-03 |
| AUDUSD | 100 310 : 2010-09-09 -> 2026-11-04 | 4 610 : 2010-01-04 -> 2026-11-03 |
| USDCAD | 100 098 : 2010-09-23 -> 2026-11-04 | 4 616 : 2010-01-04 -> 2026-11-03 |
| EURUSD (information seulement) | 100 152 : 2010-09-20 -> 2026-11-04 | 4 616 : 2010-01-04 -> 2026-11-03 |

  Aucune année intérieure n'est suspecte (contrôle du gel). **Réserve** : les 183 derniers jours de chaque série (à partir du 5 mai 2026 environ), non regardés. **Développement** : tout ce qui précède, en 4 blocs de durée égale (environ 3,9 ans chacun).
- **Gel** : les bougies (avec leur champ de spread historique) sont enregistrées dans des fichiers dont l'empreinte `sha256` est inscrite dans `H-3_snapshot_manifest.json`. L'outil **refuse de s'exécuter** si une empreinte diffère.
- **Développement et réserve** : la **réserve** est la **dernière période de 6 mois** (par symbole), **jamais évaluée**. Le **développement** est tout ce qui précède, coupé en **4 blocs consécutifs de durée égale**
  (par symbole). La réserve ne peut être ouverte qu'**une seule fois** (§6) ; après cela, elle est consommée.
- **Limites reconnues** : démo, pas du réel (le spread d'un compte démo peut différer du compte réel) ; bougies en prix bid ; la profondeur d'historique du courtier détermine le nombre de blocs ; l'idée même de H-3 vient
  d'un cours lu **après** H-1 et H-2 (les échecs de ces cycles ont pu influencer le choix des familles, d'où le durcissement du §5).

## 3. Coûts

- **Spread** : celui **enregistré par le courtier pour chaque bougie** (pas un spread fixe). À l'entrée comme à la sortie, un trade paie le spread de la bougie concernée.
- **Commission** : **3,50 USD par lot et par côté, soit 7,00 USD par lot et par aller-retour**, mesurée le 4 nov. 2026 sur un vrai trade démo de 1 lot (EURUSD, tickets de transaction 324694148 et 324694155 : « commission -3,5 » à l'entrée
  et à la sortie ; une mesure à 0,01 lot donnait 0,04 USD par côté, arrondi au centime du même 3,50). Elle est lue dans l'historique des transactions du compte (champ « commission »),
  pas supposée. Si elle n'a pas été lue avant le gel, le gel n'a pas lieu.
- **Pas de swap** (positions de quelques heures ; pour la famille f, les nuits sont négligées : limite reconnue).
- **Rendements mesurés en unités d'ATR(14)** du moment, pour comparer des symboles différents.

## 4. Hypothèses (6 familles ; budget de ce cycle : 6, aucune de plus)

Les définitions mécaniques exactes (§4 du catalogue) sont codées dans `agent/orionx/backtest/h3.py` (créé **après** ce protocole) et figées par empreinte (§8).

| ID | Famille | Mesure principale |
|---|---|---|
| H-3a | Chandeliers : doji, marteau/pendu, étoile filante/marteau inversé, marobozu, avalement, harami, étoile du matin/soir, trois soldats/corbeaux | Rendement net moyen à 4, 12, 24 bougies H1 dans le sens annoncé |
| H-3b | Retracements de Fibonacci (38,2 / 50 / 61,8 %) après une jambe d'impulsion ≥ 3 ATR | Reprise du sens de la jambe (course ±1 ATR) **comparée aux niveaux témoins** 30 / 45 / 55 / 70 % |
| H-3c | Figures : double sommet / creux, tête-épaules (cassure confirmée par une clôture) | Rendement net à 12 et 24 bougies après la cassure |
| H-3d | Divergences RSI(14) / prix, directes et cachées | Rendement net à 12 et 24 bougies |
| H-3e | Étranglement de Bollinger (20, 2) : largeur dans les 10 % les plus bas des 250 dernières bougies | Volatilité réalisée des 12 bougies suivantes, rapportée à la volatilité habituelle (non directionnel) |
| H-3f | « Plan de trading » du cours en D1 : pente de la MMS 100, RSI(14) de même sens, inversion du parabolique SAR ; sortie à l'inversion inverse | Profit factor, drawdown, nombre de trades, 4 symboles agrégés |

**Une famille = une hypothèse.** Dans les familles a à d, la statistique est le **meilleur** de la famille (règle × horizon) : on compare ce maximum à celui obtenu sur des dates aléatoires (§5), ce qui rend
la comparaison équitable malgré le nombre de variantes. Les variantes avec moins de 100 signaux dans le développement (tous symboles de décision cumulés) sont exclues avant le calcul.

## 5. Critères d'acceptation sur le développement (TOUS requis, par famille)

**Tirages aléatoires** : 1 000 tirages. Pour chaque tirage, autant de dates qu'il y a de signaux, tirées au hasard **avec la même heure de la journée et la même classe d'ATR** (terciles), sens aléatoire
aux mêmes proportions. Graines 0 à 999.

**Familles a, c, d** (directionnelles) :
1. Au moins **100 signaux** pour la variante retenue (symboles de décision cumulés).
2. Rendement net moyen **> 0**.
3. Le maximum observé de la famille est **meilleur que 97,5 % des maxima aléatoires** (p ≤ 0,025).
4. Rendement net moyen positif dans **au moins 3 blocs sur 4** et sur **au moins 3 symboles de décision sur 4**.

**Famille b (Fibonacci)** : critères 1, 3 et 4 ci-dessus, la statistique étant la différence entre le taux de réussite aux niveaux de Fibonacci et aux niveaux témoins, **et** un taux de réussite supérieur à 50 % net de coûts.

**Famille e (volatilité)** : rapport de volatilité ≥ **1,20**, p ≤ 0,025 contre des dates aléatoires de même heure, dans **au moins 3 symboles sur 4**. Passer ne donne **pas** une stratégie : cela donne un
filtre de risque (réduire ou différer les entrées), à valider séparément.

**Famille f (stratégie complète)** : au moins **100 trades** agrégés ; **profit factor > 1,2 dans au moins 3 blocs sur 4** ; **drawdown maximal < 10 %** à 0,5 % de risque par trade ;
résultat net supérieur à celui d'**au moins 97,5 % de 200 tirages aléatoires** (au moins 195 sur 200).

Aucune tolérance, y compris si un critère échoue de peu. **Les seuils sont au moins aussi stricts que ceux de H-2.**

## 6. Ouverture de la réserve

- Aucune famille ne passe le §5 : la réserve reste **fermée**. Décision : NO-GO.
- Sinon, **une seule famille** (et une seule variante) est présentée : celle dont le p est le plus faible (à égalité : la plus ancienne dans l'ordre a à f).
- Critères sur la réserve (TOUS requis) : au moins **30 signaux** (20 trades pour la famille f), résultat net moyen > 0 sur les symboles agrégés **et** sur au moins 3 symboles sur 4, et pour f : PF > 1,2 et drawdown
  < 10 %. **Moins de signaux = non concluant = pas de validation.**
- Ouverture unique, tracée dans le journal d'audit (marqueur `h3_holdout_opened.json`).

## 7. Analyse des perdants

Descriptive seulement. Elle ne règle **jamais** un paramètre (principe n°10) ; un schéma repéré ne peut être testé qu'avec de NOUVELLES données.

## 8. Ce qui est figé

Aucun paramètre n'est modifié après le gel : seuils de bougies, définitions de pivots (zigzag à 3 ATR), niveaux, horizons, coûts, graines. Empreintes (`git hash-object`) **figées le 5 nov. 2026, avant toute exécution du développement** :

| Fichier | Empreinte |
|---|---|
| `agent/orionx/backtest/h3.py` (détecteurs, SAR) | `dae5a355d0d8a55f933f754b7e9a7be23a8cbc52` |
| `agent/orionx/backtest/h3_bench.py` (banc, jugements) | `401aed2d46146bbc98a3da0d416ce67888fa86f0` |
| `agent/orionx/backtest/h3_run.py` (exécution unique) | `0e33416d810e28650351d42d1ad357f324c5f74a` |
| `agent/orionx/backtest/h3_freeze.py` (gel) | `339c9832d54183417029e815efbcc7d5cdd786dd` |
| `agent/orionx/strategies/indicators.py` | `0f6d9c09eed41d1d31ceda065543f98b4e38dbec` |
| `docs/validation/H-3_catalogue_regles_cours.md` | `111a0988280a3fdb5a4a5feac7e0719526bb2481` |
| `docs/validation/H-3_snapshot_manifest.json` | `5b85043f674f02f78a2b1d869de5b4d2e41d22f8` |

Configuration par défaut codée dans `h3.py`, jamais `config.yaml`. Toute modification de l'un de ces fichiers après l'exécution invalide le verdict.

**Précisions de mise en œuvre** (écrites avant toute mesure ; elles complètent §4 et §5 sans en changer les seuils) :
- Famille e : un étranglement qui dure compte **une seule fois** (on ne garde que sa première bougie ; aucun autre signal dans les 12 bougies précédentes). Rapport de volatilité >= 1,20 sur au moins 100 épisodes par symbole.
- Famille b : une entrée = un contact avec un niveau (Fibonacci ou témoin) après une jambe >= 3 ATR ; ordre limite rempli au niveau ; les niveaux déjà franchis entre le pivot et sa confirmation sont exclus (pas d'anticipation).
- Famille f : SAR de Wilder (pas 0,02, maximum 0,20) ; entrée à l'ouverture suivant une inversion du SAR si la pente de la MMS 100 (sur 10 jours) et le RSI(14) (> 50 ou < 50) vont dans le même sens ; stop = SAR en vigueur ; sortie au SAR à l'inversion suivante (au plus défavorable entre le SAR et l'ouverture en cas d'écart) ; une position à la fois par symbole ;
  distance de risque plancher 0,5 ATR pour le dimensionnement ; tirages aléatoires : mêmes symboles, sens, durées et distances de risque, dates tirées au hasard.
- Une seule exécution : un marqueur `h3_dev_run.json` interdit de relancer. Un refus (empreinte différente) n'épuise pas l'exécution.

## 9. Ordre des opérations

1. [x] **Écrire ce protocole** et le commiter (ce document, avant toute donnée et tout code d'essai).
2. [x] **Lire la commission réelle** : fait le 4 nov. 2026 (3,50 USD par lot et par côté, §3), par un trade démo de 0,01 lot (`demo-trade`) puis de 1 lot.
3. [x] **Geler les données** : fait le 4 nov. 2026 (commande `h3-freeze`, 10 séries, aucune année suspecte), manifeste commité dans un commit distinct, **avant** toute mesure.
4. [x] **Coder** les détecteurs et le banc d'essai (`h3.py`) : fonctions pures, testées sur des données synthétiques (par exemple : un avalement construit à la main est détecté, un hasard pur ne passe pas).
   **Sans toucher** aux stratégies existantes ni aux garde-fous du risque.
5. [ ] (code figé le 5 nov. 2026) **Vérifier** que le banc refuse des données dont l'empreinte diffère, puis **exécuter le développement une seule fois**, publier la sortie complète quel qu'elle soit.
6. [ ] Appliquer §6 (réserve) seulement si une famille a passé §5.

## 10. Décision

- **GO** (pour une étape suivante seulement) : une famille passe §5 puis §6. Elle ouvre l'écriture d'un protocole de **stratégie** (règles d'entrée, de sortie et de risque complètes), puis une semaine de signaux seuls (V-4).
  Cela **ne déverrouille pas** l'exécution automatique ni le mode réel (ADR 0009).
- **NO-GO** : tout le reste. Après H-3, **ne pas continuer à chercher dans le même cours** : la prochaine piste vient d'ailleurs (autre nature de signal, par exemple le coût de portage, le calendrier
  économique ou plusieurs marchés liés), ou de l'acceptation que des règles de prix seules n'ont pas d'avantage net des coûts.
