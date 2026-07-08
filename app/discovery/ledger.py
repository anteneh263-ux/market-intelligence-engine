"""Discovery Ledger — the SEPARATE record of generated hypotheses.

Kept apart from the pre-registered TradingView catalogue (INDEX.md / meta_ledger):
generated strategies live under `knowledge_base/discovery/`. Tracks each G-####'s
frozen recipe, traits, generation round, priority, verdict, and survival — plus
the discovery search budget (total generated / tested) for honest multiplicity.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from app.strategy_research.dossier import _KB_ROOT
from .genome import GeneratedStrategy, Recipe, materialize
from .traits import recipe_traits


class DiscoveryLedger:
    def __init__(self, root: Optional[str] = None):
        self.dir = os.path.join(root or _KB_ROOT, "discovery")
        self.results_dir = os.path.join(self.dir, "results")
        os.makedirs(self.results_dir, exist_ok=True)
        self.path = os.path.join(self.dir, "ledger.json")
        self.data = json.load(open(self.path)) if os.path.exists(self.path) else \
            {"next_id": 1, "entries": {}, "rounds": []}

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2, sort_keys=True)

    def has_recipe(self, recipe: Recipe) -> bool:
        h = recipe.hash()
        return any(e["recipe"]["hash"] == h for e in self.data["entries"].values())

    def next_gid(self) -> str:
        return f"G-{self.data['next_id']:04d}"

    def record_generated(self, recipe: Recipe, round_no: int, rationale: Dict) -> str:
        """Freeze a proposal into the ledger with its FULL rationale — recipe hash,
        priority score + component breakdown, traits, the discovery-policy version
        and hash, and the corpus size at proposal time. This is the audit trail
        answering 'why was this hypothesis proposed?'."""
        gid = self.next_gid(); self.data["next_id"] += 1
        self.data["entries"][gid] = {
            "id": gid, "recipe": recipe.to_dict(), "traits": recipe_traits(recipe),
            "round": round_no, "priority": round(rationale.get("score", 0.0), 4),
            "rationale": rationale, "status": "generated",
            "verdict": None, "survived": None, "result": None,
        }
        self._save()
        return gid

    def record_result(self, gid: str, report: Dict) -> None:
        e = self.data["entries"][gid]
        h = report.get("headline", {})
        e["status"] = "tested"; e["verdict"] = report.get("verdict")
        e["survived"] = report.get("verdict") not in ("Graveyard", "Not Supported")
        e["result"] = {"dsr": h.get("dsr"), "oos_sharpe_annual": h.get("oos_sharpe_annual"),
                       "max_drawdown": h.get("max_drawdown"),
                       "generalization": report.get("generalization_score"),
                       "is_real": report.get("is_real"), "matrix_version": report.get("matrix_version")}
        with open(os.path.join(self.results_dir, f"{gid}.json"), "w") as f:
            json.dump({**e, "full_report": report}, f, indent=2, sort_keys=True, default=float)
        self._save()

    def close_round(self, round_no: int, gids: List[str]) -> Dict:
        tested = [self.data["entries"][g] for g in gids if self.data["entries"][g]["status"] == "tested"]
        survived = [e for e in tested if e["survived"]]
        summary = {"round": round_no, "generated": len(gids), "tested": len(tested),
                   "survived": len(survived),
                   "survival_rate": (len(survived) / len(tested)) if tested else 0.0,
                   "best_dsr": max([(e["result"] or {}).get("dsr") or 0 for e in tested], default=0.0),
                   "ids": gids}
        self.data["rounds"].append(summary)
        self._save()
        return summary

    # ---- reads -------------------------------------------------------------- #
    def entries(self) -> List[Dict]:
        return list(self.data["entries"].values())

    def tested(self) -> List[Dict]:
        return [e for e in self.entries() if e["status"] == "tested"]

    def budget(self) -> Dict:
        ents = self.entries()
        return {"generated": len(ents), "tested": len([e for e in ents if e["status"] == "tested"])}

    def rehydrate(self, gid: str) -> GeneratedStrategy:
        return materialize(Recipe.from_dict(self.data["entries"][gid]["recipe"]), gid)
