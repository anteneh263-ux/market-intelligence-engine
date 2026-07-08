"""Traits — the learning memory of the Discovery Engine.

Extracts a trait profile from a recipe, mines a trait-success memory from the
whole corpus (pre-registered H results + generated G results), and provides the
prior score (prioritise) and avoidance rule (avoid repeatedly-failed patterns).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

import numpy as np

from app.knowledge_base import KnowledgeBase, query
from .blocks import BLOCKS
from .genome import Recipe
from .policy import AVOIDANCE, WEIGHTS

# Map recorded strategy classes → trait family, so H-corpus evidence informs priors.
CLASS_TO_FAMILY = {
    "trend-crossover": "trend", "trend-following": "trend",
    "mean-reversion-oscillator": "mean-reversion", "mean-reversion-bands": "mean-reversion",
    "breakout": "breakout", "price-action-momentum": "price-action",
    "bar-pattern": "bar-pattern", "composite": "composite", "generated": "generated",
}


def recipe_traits(recipe: Recipe) -> Dict:
    fams = [BLOCKS[b].family for b in recipe.blocks]
    turns = [BLOCKS[b].turnover for b in recipe.blocks]
    rank = {"low": 0, "med": 1, "high": 2}
    dominant = max(set(fams), key=fams.count)
    turnover = ["low", "med", "high"][max(rank[t] for t in turns)]
    return {"family": dominant, "turnover": turnover, "n_blocks": len(recipe.blocks),
            "uses_gate": bool(recipe.gate and BLOCKS.get(recipe.gate) and BLOCKS[recipe.gate].is_gate),
            "families": sorted(set(fams))}


class TraitStats:
    """Per-trait-value success memory learned from all recorded results."""

    def __init__(self):
        self.by_family: Dict[str, Dict] = defaultdict(lambda: {"n": 0, "non_fragile": 0, "dsr": []})
        self.by_turnover: Dict[str, Dict] = defaultdict(lambda: {"n": 0, "non_fragile": 0, "dsr": []})
        self.n = 0

    def _add(self, family: str, turnover: str, verdict: str, dsr):
        non_fragile = 1 if verdict not in ("Graveyard", "Not Supported") else 0
        for bucket, key in ((self.by_family, family), (self.by_turnover, turnover)):
            b = bucket[key]; b["n"] += 1; b["non_fragile"] += non_fragile
            if dsr is not None:
                b["dsr"].append(dsr)
        self.n += 1

    @classmethod
    def from_corpus(cls, kb: KnowledgeBase, generated: List[Dict] | None = None) -> "TraitStats":
        st = cls()
        for r in query.tested_results(kb):        # pre-registered H evidence
            fam = CLASS_TO_FAMILY.get(r.get("class", ""), "unknown")
            turn = "high" if (r.get("num_trades") or 0) > 800 else "med"
            st._add(fam, turn, r["verdict"], r.get("dsr"))
        for g in (generated or []):               # generated G evidence
            if g.get("verdict") in ("Supported", "Not Supported", "Inconclusive", "Graveyard"):
                t = g.get("traits", {})
                st._add(t.get("family", "generated"), t.get("turnover", "med"),
                        g["verdict"], (g.get("result") or {}).get("dsr"))
        return st

    def _rate(self, bucket, key):
        b = bucket.get(key)
        if not b or b["n"] == 0:
            return 0.5, 0.05     # neutral prior when unseen
        return b["non_fragile"] / b["n"], (float(np.mean(b["dsr"])) if b["dsr"] else 0.0)


def score_breakdown(recipe: Recipe, stats: TraitStats) -> Dict:
    """Return the score AND every component that produced it — the audit trail for
    'why was this hypothesis proposed'."""
    t = recipe_traits(recipe)
    f_rate, f_dsr = stats._rate(stats.by_family, t["family"])
    u_rate, u_dsr = stats._rate(stats.by_turnover, t["turnover"])
    comp = {
        "trait_rate": WEIGHTS["trait_rate"] * (f_rate + u_rate) / 2.0 * 2.0,  # avg → keep scale
        "family_non_fragile_rate": f_rate, "turnover_non_fragile_rate": u_rate,
        "family_mean_dsr": f_dsr, "turnover_mean_dsr": u_dsr,
        "gate_bonus": WEIGHTS["gate_bonus"] if t["uses_gate"] else 0.0,
        "multi_block_bonus": WEIGHTS["multi_block_bonus"] if t["n_blocks"] >= 2 else 0.0,
    }
    raw = (WEIGHTS["trait_rate"] * (f_rate + u_rate)
           + WEIGHTS["trait_dsr"] * max(f_dsr, 0) + WEIGHTS["trait_dsr"] * max(u_dsr, 0)
           + comp["gate_bonus"] + comp["multi_block_bonus"])
    return {"score": float(np.clip(raw, 0.0, 1.0)), "traits": t, "components": comp,
            "corpus_n": stats.n}


def score(recipe: Recipe, stats: TraitStats) -> float:
    return score_breakdown(recipe, stats)["score"]


def avoid_reason(recipe: Recipe, stats: TraitStats) -> str | None:
    """Why (if at all) this candidate is rejected — recorded for auditability.
    Only confirmed nulls, or families that are BOTH all-fragile over enough tries
    AND low-DSR, are banned; a promising high-DSR family stays explorable."""
    t = recipe_traits(recipe)
    if t["family"] in AVOIDANCE["ban_families"]:
        return f"family '{t['family']}' is a banned/confirmed-null trait"
    m = AVOIDANCE["min_seen_all_fragile"]
    keep = AVOIDANCE["keep_if_mean_dsr_above"]
    fb = stats.by_family.get(t["family"])
    if fb and fb["n"] >= m and fb["non_fragile"] == 0:
        mean_dsr = float(np.mean(fb["dsr"])) if fb["dsr"] else 0.0
        if mean_dsr < keep:
            return (f"family '{t['family']}' has {fb['n']} tested, 0 survivors, "
                    f"mean DSR {mean_dsr:.2f} < {keep}")
    return None


def is_avoided(recipe: Recipe, stats: TraitStats) -> bool:
    return avoid_reason(recipe, stats) is not None
