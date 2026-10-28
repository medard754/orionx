# ORIONX

Agent de trading IA pour MetaTrader 5. **Version 0.1 : démo uniquement.**

> Le trading comporte un risque élevé de perte en capital. Ce dépôt ne fait aucune promesse de rendement.
> Le mode réel est volontairement désactivé (`ConfigError` si `mode: live`).

## Principe

**L'IA propose, le code déterministe décide.** Une proposition (`TradeProposal`) passe par le gestionnaire de
risques, qui produit un `ApprovedOrder` signé. Seul `Executor` envoie des ordres, et seulement s'ils sont signés.
Aucun ordre sans stop-loss. Voir `docs/risk_rules.md` et `docs/adr/`.

## Prérequis (poste de développement Windows 10/11)

| Outil | Pourquoi |
|---|---|
| Python **3.12** (python.org, cocher « Add to PATH ») | langage de l'agent |
| Git | versionnage |
| Visual Studio Code + extensions *Python*, *Ruff* | édition |
| MetaTrader 5 (terminal de votre courtier) + **compte DÉMO** | connexion réelle (le package `MetaTrader5` ne fonctionne que sous Windows) |
| Compte GitHub | dépôt privé, revues de code, CI |

Pour développer sans MT5 (Linux/macOS ou sans terminal) : tout fonctionne avec le courtier simulé.

## Installation

```powershell
git clone <URL-du-depot> orionx ; cd orionx\agent
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"           # sous Windows, ajouter l'extra MT5 : pip install -e ".[dev,mt5]"
copy config.example.yaml config.yaml
```

## Commandes utiles (depuis `agent/`)

```powershell
python -m unittest discover -s tests -t . -v   # 192 tests, sans MT5
python -m orionx.main simulate                 # scénario complet sur courtier simulé
ruff check . ; mypy                            # qualité (voir PROJECT_STATUS T-10)
python -m orionx.main run                      # boucle de l'agent (n'envoie AUCUN ordre par défaut)
python -m orionx.main kill --level 3           # arrêt d'urgence depuis un autre terminal
python -m orionx.main clear-kill               # lève le drapeau d'arrêt d'urgence
python -m orionx.main audit-verify             # intégrité du journal (data/audit.db)
python -m orionx.main backtest --bars 15000 --filters none,vol,session,trend  # backtest comparé au hasard (lecture seule)
python -m orionx.main risk-set --profile moderate  # hausse du risque : appliquée après 24 h
python -m orionx.main risk-status              # configuration de risque active ou en attente
python -m orionx.main risk-check --symbol EURUSD --side buy  # que déciderait le risque ? (aucun ordre envoyé)
python -m orionx.main state                    # état du marché et annonces à venir (analysis.calendar_path)
```

Exécution automatique des ordres : il faut **deux** réglages explicites, `autonomy: auto` dans `config.yaml`
**et** l'option `--allow-auto`. Sans l'un des deux, l'agent ne fait que journaliser les signaux.
Procédure de validation sur MT5 : `docs/RUNBOOK_VALIDATION.md`.

### Premier contact avec MT5 (lecture seule, aucun ordre)

1. Installer MetaTrader 5, ouvrir un **compte démo**, se connecter une fois dans le terminal.
2. Copier `.env.example` en `.env` et renseigner login / mot de passe / serveur du compte démo.
3. Charger les variables, puis : `python -m orionx.main mt5-check`
   La commande refuse de continuer si le compte n'est pas un compte démo.

## Structure

```
agent/orionx/
  core/           modèles, configuration (bornes absolues), bus d'événements
  connector_mt5/  Gateway (contrat), SimulatedGateway, Mt5Gateway (réel, à valider)
  risk/           sizing, approbation signée, gestionnaire de risques (M5)
  execution/      Executor (M6) : ordres signés uniquement, idempotent, vérifie le stop-loss
  audit/          journal d'audit SQLite chaîné par hachage (T-03)
  monitoring/     arrêt d'urgence à 3 niveaux + drapeau sur disque (T-05)
  data/           ticks, bougies, données périmées, stockage SQLite (T-04)
  decision/       moteur de décision M4 : stratégies par régime, conflits, classement par devise
  analysis/       état du marché, calendrier d'annonces, surveillance de la thèse, filtres (M3 v1)
  positions/      breakeven, stop suiveur, sortie partielle (T-07)
  strategies/     indicateurs + stratégie tendance/repli H1 (T-12)
  backtest/       backtest avec coûts (T-12)
  agent.py        boucle principale (T-06)
  main.py         commandes : simulate, mt5-check, demo-trade, run, kill, clear-kill, audit-*, backtest
agent/tests/      tests unittest (compatibles pytest)
docs/             PROJECT_STATUS.md, risk_rules.md, adr/
```

## Publier sur Git

```powershell
git init -b main
git add . ; git commit -m "chore: socle ORIONX v0.1 (noyau, risque, exécution, simulateur)"
git remote add origin <URL-du-depot-prive>
git push -u origin main
```
Puis sur GitHub : protéger `main` (pull request obligatoire, CI verte). Un seul développeur : pas
d'approbation obligatoire, la relecture suit l'ADR 0004.

## Règles d'or

1. Jamais d'identifiants dans Git (`.env` et `config.yaml` sont ignorés).
2. Jamais de compte réel pendant la phase de développement.
3. Toute modification de `risk/`, `execution/`, `connector_mt5/` ou `core/config.py` passe par une revue `/code-review`
   puis une relecture personnelle au moins 24 h plus tard (ADR 0004).
4. Pas de fusion si un test échoue ; un test de règle de risque n'est jamais assoupli pour « passer ».
