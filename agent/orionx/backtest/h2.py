"""Protocole H-2 (docs/validation/H-2_protocole.md) : trois hypothèses, mêmes données et mêmes coûts que H-1, critère « hasard » durci.

Tout ce qui est figé par le protocole est ici : les hypothèses, le seuil de 97,5 %, l'empreinte des données, le marqueur de la
réserve. La configuration est CELLE PAR DÉFAUT (jamais config.yaml) : un résultat ne doit pas dépendre d'un fichier local.
"""

from __future__ import annotations

from orionx.backtest.h1 import Hypothesis
from orionx.core.config import AppConfig
from orionx.decision.registry import build_engine

BEAT_RANDOM_MIN = 0.975  # 39 tirages sur 40 (H-1 : 95 %) : 7 hypothèses testées au total sur les mêmes données
EXPECTED_SNAPSHOT_SHA = "88180049abbeb362"  # le même instantané que H-1
MARKER = "h2_holdout_opened.json"
ENGINES = {"H-2a": ("mean_reversion",), "H-2b": ("breakout",), "H-2c": ("trend_pullback", "mean_reversion", "breakout")}


def hypotheses() -> list[Hypothesis]:
    cfg = AppConfig()  # configuration par défaut, figée par le protocole

    def factory(names: tuple[str, ...]):  # type: ignore[no-untyped-def]
        return lambda: build_engine(names, cfg.decision, cfg.analysis).as_strategy()

    return [Hypothesis(hid, factory(names), "none") for hid, names in ENGINES.items()]


def check_snapshot(digest: str, expected: str = EXPECTED_SNAPSHOT_SHA) -> str | None:
    """Message d'erreur si les données ne sont pas celles du protocole, sinon None."""
    if digest == expected:
        return None
    return (f"Les données ne sont pas celles du protocole H-2 (empreinte {digest}, attendue {expected}) : refus de calculer. "
            "Restaurez l'instantané d'origine (data/h1_EURUSD_H1.csv) au lieu d'en créer un autre.")
