# H-1 / V-3 – Protocole pré-enregistré (stratégie `trend_pullback`, EURUSD H1)

**Écrit le 27 oct. 2026, AVANT tout résultat sur les données de développement.** Ce fichier est commité avant le
premier chiffre : toute modification ultérieure doit apparaître dans l'historique git et être justifiée ici.

## 1. Question

Un des filtres d'entrée (`vol`, `session`, `trend`, `all`) donne-t-il à `trend_pullback` un avantage mesurable, c'est-à-dire
meilleur que le hasard, sur des données jamais regardées ? Référence de départ : `none` (PF 0,90 sur 15 000 bougies,
14 oct. 2026, 60e centile du hasard).

## 2. Données et découpage

- EURUSD, H1, compte MetaQuotes-Demo, **30 000 bougies** demandées à MT5 (≈ 29 déc. 2021 → 27 oct. 2026).
- **Réserve (holdout)** : les bougies des 183 derniers jours avant la dernière bougie. Jamais utilisée pour choisir,
  régler ou comparer. Ouverte au plus UNE fois (§6).
- **Développement** : tout ce qui précède, en **4 blocs consécutifs** de même taille, sans chevauchement.
- Limite reconnue : la référence `none` a déjà été calculée le 14 oct. sur une fenêtre qui contenait la réserve. Aucun
  paramètre n'a été modifié depuis (empreintes §4), mais la réserve n'est pas vierge pour `none`. Elle l'est pour les 4 filtres.

## 3. Hypothèses (4 sur 5 autorisées)

| ID | Filtre | Idée testée |
|---|---|---|
| H-1a | `vol` | Éviter les entrées en régime de forte volatilité améliore les résultats |
| H-1b | `session` | N'entrer qu'à Londres, au chevauchement et à New York améliore les résultats |
| H-1c | `trend` | N'entrer que dans le sens du régime de tendance améliore les résultats |
| H-1d | `all` | La combinaison des trois améliore les résultats |

`none` est la référence, pas une hypothèse. **La 5e place reste libre** : toute idée née de l'analyse des trades perdants
(§7) devra l'occuper, ou attendre. Aucune hypothèse n'est ajoutée ni modifiée en cours de route.

## 4. Ce qui est figé

Aucun paramètre de stratégie, de filtre, d'analyse ou de coûts n'est modifié pendant H-1. Empreintes (`git hash-object`) au
moment de l'écriture, commit parent `12296cc` :

```
8e72cf3be16c  agent/orionx/strategies/trend_pullback.py
0f6d9c09eed4  agent/orionx/strategies/indicators.py
5ce46ee31716  agent/orionx/analysis/filters.py
2e35604b8bd5  agent/orionx/analysis/state.py
f81bee66b662  agent/orionx/backtest/runner.py
d4663b085fd1  agent/orionx/strategies/baseline.py
effef29fabeb  agent/orionx/core/config.py
```

Coûts supposés : spread 10 points, commission 7 $/lot, risque 0,5 % par trade, rapport rendement/risque minimal 1,5, capital
10 000 $. Pas de swap. Le compte démo n'a ni spread ni commission réels : ces coûts sont une hypothèse prudente.

## 5. Critères d'acceptation sur le développement (TOUS requis)

1. Au moins **100 trades** sur l'ensemble du développement (sinon : échantillon insuffisant = échec).
2. **Profit factor > 1,2 dans au moins 3 blocs sur 4.**
3. **Drawdown maximal < 10 %** sur l'ensemble du développement.
4. Résultat net supérieur à celui de **95 % au moins de 40 tirages aléatoires** (même filtre, mêmes coûts).

Un filtre qui échoue à un critère est un **échec** (compté pour la règle « arrêt après 5 échecs »). Les critères ne sont pas
ajustés après coup, y compris s'ils échouent de peu.

## 6. Ouverture de la réserve

- Si aucun filtre ne passe le §5 : la réserve reste **fermée**. Décision : NO-GO.
- Sinon : un seul filtre est présenté, celui qui bat le plus de tirages aléatoires (à égalité : le plus haut PF du développement).
- Critères sur la réserve (TOUS requis) : au moins **20 trades**, PF > 1,2, résultat net > 0, drawdown < 10 %.
  **Moins de 20 trades = non concluant = pas de validation.**
- L'ouverture est unique : la commande refuse une seconde ouverture et l'écrit dans le journal d'audit.

## 7. Analyse des trades perdants (V-3)

Descriptive seulement : répartition par sens, séance, jour de la semaine et motif de sortie. Elle ne sert **jamais** à
régler un paramètre (principe n°10). Un schéma repéré devient, au mieux, la 5e hypothèse d'un nouveau cycle.

## 8. Décision

- **GO** (au sens de V-4 uniquement) : un filtre passe §5 puis §6. Autorise une semaine de `run` en signaux seuls sur ce filtre.
  Cela ne déverrouille PAS l'exécution automatique : il faudra aussi des spreads réels et plusieurs semaines de signaux.
- **NO-GO** : tout le reste. Les échecs sont consignés ci-dessous. À 5 échecs cumulés, on arrête la stratégie et on la repense.

## 9. Résultats

### 9.1 Développement (27 oct. 2026) – NO-GO

Exécuté avec `python -m orionx.main backtest --h1` (outillage commité en `1baa521`, après ce protocole `9b059fc`).
Sortie complète : `H-1_developpement_2026-10-27.txt`. Instantané des données : 30 000 bougies, 29 déc. 2021 → 27 oct. 2026,
`sha256=88180049abbeb362`. Développement : 26 855 bougies (jusqu'au 27 avr. 2026). Réserve : 3 145 bougies, **non évaluée**.
Les 7 empreintes du §4 étaient identiques au moment de l'exécution.

| Filtre | Trades | PF | Net ($) | DD % | Blocs PF > 1,2 | Bat le hasard | Verdict |
|---|---|---|---|---|---|---|---|
| `none` (référence) | 1109 | 0,86 | -3 859 | 44,7 | 0/4 | 12 % | échec |
| H-1a `vol` | 1106 | 0,86 | -3 801 | 44,1 | 0/4 | 18 % | échec |
| H-1b `session` | 971 | 0,89 | -2 764 | 34,6 | 0/4 | 28 % | échec |
| H-1c `trend` | 414 | 1,00 | +37 | 15,7 | 1/4 | 88 % | échec |
| H-1d `all` | 323 | 0,95 | -464 | 19,7 | 2/4 | 52 % | échec |

Aucune hypothèse ne passe le §5 (chacune échoue aux trois critères de blocs, de drawdown et de hasard). Conformément au §6,
**la réserve reste fermée** : elle n'a jamais été lue et reste disponible pour un futur cycle.

**Échecs cumulés : 4 sur 5.** Une seule hypothèse peut encore être testée avant l'arrêt prévu par la règle.

### 9.2 Ce que le résultat dit, et ce qu'il ne dit pas

- La stratégie de base ne montre aucun avantage sur 4,3 ans : PF 0,86, drawdown 44,7 %, pire que 88 % des entrées aléatoires.
  Le PF 0,90 du 14 oct. portait sur une période plus récente et plus favorable. Seul le dernier bloc (≈ 2025-2026) est
  proche de l'équilibre (PF 1,03).
- Les filtres `vol` et `session` ne changent presque rien. Seul `trend` s'approche de l'équilibre (PF 1,00, 88 % du hasard),
  mais en perdant 2/3 des trades ; ce n'est pas un avantage démontré, et ce serait une erreur de le retenir parce qu'il est « moins mauvais ».
- Analyse des perdants (`none`, descriptive) : les sorties au stop sont 757 pour 351 objectifs (gain moyen ≈ 69 $, perte moyenne ≈ 37 $ :
  il faut ≈ 35 % de gagnants, il y en a 32 %). Seule la séance New York est positive (+773 $ sur 207 trades) ; le reste est négatif.
  Ce schéma vient des données de développement : s'en servir comme filtre serait de l'ajustement a posteriori (principe n°10).
- Limites : un symbole, une UT, spread fixe de 1 pip et commission supposée (le compte démo n'en a pas), pas de swap.
  Un NO-GO ici dit « pas d'avantage démontré pour CETTE stratégie sur CES données », pas « aucune stratégie ne marche ».

### 9.3 Décision

**NO-GO.** L'exécution automatique reste verrouillée. V-4 (une semaine de signaux seuls) n'est PAS déclenchée par H-1 : elle ne
vaudrait que comme test de plomberie (signaux conformes au graphique), pas comme preuve d'un avantage.
