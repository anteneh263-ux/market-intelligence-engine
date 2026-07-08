"""Discovery Policy — the versioned, content-addressed generation ruleset.

Everything that determines *what gets proposed and why* lives here: the block
catalogue, the composition rules, the scoring weights, and the avoidance rules.
It is hashed, so every generated hypothesis can be traced to the exact policy
that produced it (auditability). A new policy is a new version — never an edit.

  discovery-v1  vote/filter composition; family+turnover trait priors; price-action
                and all-fragile families avoided.
"""
from __future__ import annotations

import hashlib
import json
from typing import Dict

from .blocks import BLOCKS

DISCOVERY_POLICY_VERSION = "discovery-v1"

# Scoring weights (used by traits.score). Part of the hashed policy.
WEIGHTS: Dict[str, float] = {
    "trait_rate": 0.5,        # weight on historical non-fragility of family+turnover
    "trait_dsr": 0.25,        # weight on historical mean DSR of family+turnover
    "gate_bonus": 0.05,       # regime-gate awareness bonus
    "multi_block_bonus": 0.03,
}

# Avoidance rules (used by traits.is_avoided). Part of the hashed policy.
# A family is banned only if it is a confirmed null, OR it is BOTH all-fragile over
# enough tries AND low-DSR — so a promising-but-not-yet-surviving family (e.g. a
# high-DSR breakout) stays explorable rather than being killed prematurely.
AVOIDANCE = {
    "ban_families": ["price-action"],   # confirmed null (H-002 dead everywhere)
    "min_seen_all_fragile": 5,          # need >=N all-fragile results before considering a ban
    "keep_if_mean_dsr_above": 0.35,     # ...and never ban a family whose mean DSR clears this
}

COMPOSITION_RULES = ["VOTE", "FILTER"]


def policy_descriptor() -> Dict:
    return {
        "version": DISCOVERY_POLICY_VERSION,
        "blocks": sorted(BLOCKS.keys()),
        "composition_rules": COMPOSITION_RULES,
        "weights": WEIGHTS,
        "avoidance": AVOIDANCE,
    }


def policy_hash() -> str:
    payload = json.dumps(policy_descriptor(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]
