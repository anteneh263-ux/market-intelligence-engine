"""Generator — enumerate, dedupe, avoid, and prioritise candidate recipes."""
from __future__ import annotations

import itertools
from typing import List, Tuple

from .blocks import DIRECTIONAL, GATES
from .genome import Recipe
from .ledger import DiscoveryLedger
from .traits import TraitStats, is_avoided, score


def enumerate_candidates(max_vote: int = 2) -> List[Recipe]:
    """Candidate space: VOTE over 2-3 directional blocks, and FILTER of a trigger
    block by a trend confirmer or the ADX regime gate. Deterministic ordering."""
    recipes: List[Recipe] = []
    # VOTE over pairs (and optionally triples) of distinct directional blocks
    for combo in itertools.combinations(sorted(DIRECTIONAL), 2):
        recipes.append(Recipe("VOTE", combo))
    if max_vote >= 3:
        for combo in itertools.combinations(sorted(DIRECTIONAL), 3):
            recipes.append(Recipe("VOTE", combo))
    # FILTER: a trigger gated by ADX regime gate, or confirmed by a trend block
    confirmers = GATES + ["supertrend", "price_ma"]
    for trigger in sorted(DIRECTIONAL):
        for gate in confirmers:
            if gate == trigger:
                continue
            recipes.append(Recipe("FILTER", (trigger,), gate=gate))
    return recipes


def prioritize(stats: TraitStats, ledger: DiscoveryLedger,
               k: int, max_vote: int = 2) -> List[Tuple[Recipe, float]]:
    """Return the top-k (recipe, score), skipping already-generated recipes and
    trait patterns that have repeatedly failed."""
    scored = []
    for r in enumerate_candidates(max_vote):
        if ledger.has_recipe(r) or is_avoided(r, stats):
            continue
        scored.append((r, score(r, stats)))
    # Sort by score desc, then recipe hash for deterministic tie-breaking.
    scored.sort(key=lambda rs: (-rs[1], rs[0].hash()))
    return scored[:k]
