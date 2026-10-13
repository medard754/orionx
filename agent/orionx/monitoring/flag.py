"""Drapeau d'arrêt d'urgence sur disque : permet de déclencher l'arrêt depuis un AUTRE processus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orionx.core.models import utcnow


def write_kill_flag(path: str | Path, level: int, reason: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"level": level, "reason": reason, "at": utcnow().isoformat()}), encoding="utf-8")


def read_kill_flag(path: str | Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if int(data.get("level", 0)) in (1, 2, 3) else None
    except (ValueError, OSError):
        # Fichier illisible : par prudence on traite comme un arrêt de niveau 1.
        return {"level": 1, "reason": "drapeau illisible", "at": ""}


def clear_kill_flag(path: str | Path) -> None:
    Path(path).unlink(missing_ok=True)
