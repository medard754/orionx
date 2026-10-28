# H-2 – Protocole pré-enregistré (retour à la moyenne, cassure, routeur de stratégies ; EURUSD H1)

**Écrit le 28 oct. 2026, AVANT tout résultat de H-2.** Commité et poussé avant l'outillage et avant la première exécution :
toute modification ultérieure doit apparaître dans l'historique git et être justifiée ici. Suite de `H-1_protocole.md` (NO-GO).

## 0. Avertissement honnête

La probabilité a priori qu'une de ces stratégies passe les critères est **faible** : spread de 1 pip et commission de 7 $/lot
représentent environ 15 % du risque de chaque trade (stop ≈ 1,5 ATR ≈ 12 pips), et l'EURUSD en H1 est un marché très efficient.
**Un NO-GO est le résultat le plus probable.** Ce protocole existe pour que, si un résultat positif apparaît, il soit crédible, et
pour que l'échec éventuel soit documenté proprement, pas pour obtenir un « oui ».

## 1. Question

L'une des stratégies déjà codées dans M4 (`mean_reversion`, `breakout`, ou leur combinaison avec `trend_pullback` par le moteur de
régimes) a-t-elle un avantage mesurable, meilleur que le hasard, sur des données jamais regardées ?

## 2. Données

- **Le même instantané que H-1** : EURUSD H1, 30 000 bougies (29 déc. 2021 → 27 oct. 2026), `sha256=88180049abbeb362`. L'outil refuse de
  s'exécuter si l'empreinte diffère.
- **Développement** : 26 855 bougies (jusqu'au 27 avr. 2026), 4 blocs consécutifs. **Réserve** : 3 145 bougies (27 avr. → 27 oct. 2026),
  **jamais évaluée à ce jour** (H-1 l'a laissée fermée).
- La réserve peut être ouverte **au plus une fois** dans H-2. **Après cela elle est consommée** : tout cycle ultérieur (H-3…) devra
  utiliser de nouvelles données (≈ 3 100 bougies de plus tous les 6 mois) ou d'autres symboles, jamais cette période.
- Limite reconnue : les 4 hypothèses de H-1 ont été regardées sur ces mêmes données de développement. Leur résultat a pu influencer
  le choix des idées de H-2. Pour limiter ce risque : les stratégies de H-2 existaient dans le dépôt **avant** les résultats de H-1
  (commit `a331cb5`), leurs paramètres n'ont pas été touchés depuis, et aucune hypothèse de H-2 ne vient de l'analyse des perdants de H-1.

## 3. Hypothèses (3 ; budget de ce cycle : 5)

| ID | Stratégie (moteur M4, régimes du registre) | Idée testée |
|---|---|---|
| H-2a | `engine:mean_reversion` : clôture hors bande de Bollinger (20, 2σ) et RSI 14 < 30 ou > 70, **en régime `range` seulement** ; stop 1,5 ATR, objectif = moyenne | Après un excès en marché sans tendance, le prix revient à sa moyenne |
| H-2b | `engine:breakout` : clôture au-delà du plus haut/bas des 20 bougies précédentes, bougie d'amplitude ≥ 1 ATR, régimes tendance ou range ; stop 1,5 ATR, objectif 2 × le stop | Une cassure de canal avec élan se prolonge |
| H-2c | `engine:trend_pullback,mean_reversion,breakout` : les trois stratégies routées par régime, conflits et consensus du moteur | Le routeur complet, tel que l'agent le ferait tourner, est meilleur que ses parties |

Aucun filtre d'entrée (`none`). Les comptes d'échecs **cumulés** du projet sont : H-1 = 4 échecs (`vol`, `session`, `trend`, `all`).
H-2 ajoutera jusqu'à 3 hypothèses : **7 testées au total** sur les mêmes données. Pour compenser ce nombre de tests, le critère « hasard » est
**durci** (§5). H-2 est une nouvelle famille (mécanismes différents), pas une 5e hypothèse de H-1 ; sa règle d'arrêt est : à 5 échecs dans
H-2, la famille est abandonnée.

## 4. Ce qui est figé

Aucun paramètre de stratégie, de moteur, d'analyse ou de coûts n'est modifié pendant H-2. Empreintes (`git hash-object`), commit parent
`3d90824` :

```
7c9463c32f97  agent/orionx/strategies/mean_reversion.py
dfb7c5f6c4ff  agent/orionx/strategies/breakout.py
8e72cf3be16c  agent/orionx/strategies/trend_pullback.py
0f6d9c09eed4  agent/orionx/strategies/indicators.py
d4663b085fd1  agent/orionx/strategies/baseline.py
52c5b44840b0  agent/orionx/decision/engine.py
0b53205d393c  agent/orionx/decision/registry.py
2e35604b8bd5  agent/orionx/analysis/state.py
cc486eca6e0a  agent/orionx/analysis/tzutil.py
ac286650411a  agent/orionx/backtest/runner.py
cbe96c11b6c9  agent/orionx/backtest/walkforward.py
eab58595a3e3  agent/orionx/core/config.py
```

`runner.py` et `config.py` ont changé depuis H-1 (`f81bee66b662`, `effef29fabeb`) à cause du chantier T-10 (types et style, aucune
logique). **Vérification** : avant H-2, H-1 est relancé et ses résultats doivent être identiques au chiffre près (§9.1). Sinon, H-2 ne démarre pas.

Coûts et paramètres de backtest : identiques à H-1 (spread 10 points, commission 7 $/lot, risque 0,5 %, rapport rendement/risque minimal
1,5, capital 10 000 $, pas de swap), tirages aléatoires avec les graines 0 à 39.

## 5. Critères d'acceptation sur le développement (TOUS requis)

1. Au moins **100 trades** sur l'ensemble du développement.
2. **Profit factor > 1,2 dans au moins 3 blocs sur 4.**
3. **Drawdown maximal < 10 %** sur l'ensemble du développement.
4. Résultat net supérieur à celui d'**au moins 97,5 % de 40 tirages aléatoires** (39 sur 40). *Durci par rapport à H-1 (95 %) car 7 hypothèses
   au total auront été testées sur les mêmes données.*

Aucune tolérance, y compris si un critère échoue de peu.

## 6. Ouverture de la réserve

- Aucune hypothèse ne passe le §5 : la réserve reste **fermée** (donc réutilisable). Décision : NO-GO.
- Sinon : une seule hypothèse est présentée, celle qui bat le plus de tirages aléatoires (à égalité : le plus haut PF du développement).
- Critères sur la réserve (TOUS requis) : au moins **20 trades**, PF > 1,2, résultat net > 0, drawdown < 10 %. **Moins de 20 trades =
  non concluant = pas de validation.**
- Ouverture unique, tracée dans le journal d'audit (marqueur `h2_holdout_opened.json`, distinct de celui de H-1).

## 7. Analyse des trades perdants

Descriptive seulement (sens, séance, jour, motif de sortie). Elle ne règle **jamais** un paramètre (principe n°10) ; un schéma repéré ne peut
être testé qu'avec de NOUVELLES données.

## 8. Décision

- **GO** (pour V-4 seulement) : une hypothèse passe §5 puis §6. Elle ouvre une confirmation sur un autre symbole (GBPUSD, USDJPY) sous un
  protocole écrit à l'avance, puis une semaine de signaux seuls. Cela ne déverrouille pas l'exécution automatique.
- **NO-GO** : tout le reste. Après un NO-GO complet de H-2 (7 échecs cumulés), **ne pas continuer à chercher sur ces données** : la suite
  doit venir d'ailleurs (nouvelles données, autres symboles ou unités de temps, autre nature de signal), ou de l'acceptation que des règles
  de prix seules en H1 n'ont pas d'avantage net des coûts pour ce marché.

## 9. Résultats

*(à compléter après exécution)*
