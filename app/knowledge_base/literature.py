"""Knowledge Base — structured literature store access.

Real, verified citations only, keyed by canonical strategy-class and versioned.
The dossier generator reads from here; it never invents references. Loading is
deterministic (a static JSON file), so identical inputs yield identical dossiers.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Dict, List

from app.strategy_research.dossier import _KB_ROOT

_LIT_PATH = os.path.join(_KB_ROOT, "literature", "literature.json")


@lru_cache(maxsize=1)
def _load() -> Dict:
    with open(_LIT_PATH) as f:
        return json.load(f)


def literature_version() -> str:
    return _load().get("version", "lit-unknown")


def resolve_class(strategy_class: str) -> str:
    lit = _load()
    return lit.get("class_aliases", {}).get(strategy_class, strategy_class)


def for_class(strategy_class: str) -> Dict:
    """Return {canonical_class, references:[{key,authors,year,title,venue,url,direction,finding}],
    failure_modes:[...]} for a strategy class. Empty structure if the class is unknown."""
    lit = _load()
    canon = resolve_class(strategy_class)
    entry = lit.get("classes", {}).get(canon)
    if not entry:
        return {"canonical_class": canon, "references": [], "failure_modes": []}
    refs: List[Dict] = []
    for r in entry["refs"]:
        meta = lit["references"][r["key"]]
        refs.append({"key": r["key"], **meta,
                     "direction": r["direction"], "finding": r["finding"]})
    return {"canonical_class": canon, "references": refs,
            "failure_modes": list(entry["failure_modes"])}
