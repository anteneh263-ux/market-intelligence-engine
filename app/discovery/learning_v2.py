"""Default v2 learning; v1 and historical evidence retain their semantics.

Only conclusive results on explicitly real data inform priors. Inconclusive
outcomes are neither successes nor confirmed failures. All attempts still
count for deduplication and the ledger's trial budget.
"""
from __future__ import annotations

import hashlib
import json

from .policy import policy_descriptor
from .traits import TraitStats

VERSION = "discovery-v2"
CONCLUSIVE = {"Supported", "Not Supported", "Graveyard"}


def descriptor():
    return {
        **policy_descriptor(), "version": VERSION,
        "learning": {
            "real_data": "is_real must be boolean true",
            "eligible_verdicts": sorted(CONCLUSIVE),
            "success": "Supported",
            "inconclusive": "excluded from rates, DSR means and avoidance counts",
        },
    }


def policy_hash():
    payload = json.dumps(descriptor(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


class LearningStatsV2(TraitStats):
    """Use the v1 score formula with conclusive, real evidence only."""

    def _add(self, family, turnover, verdict, dsr):
        if verdict in CONCLUSIVE:
            super()._add(family, turnover, verdict, dsr)

    @classmethod
    def from_corpus(cls, kb, generated=None):
        class RealEvidence:
            def all_results(self):
                return [r for r in kb.all_results() if r.get("is_real") is True]

        eligible = [g for g in (generated or [])
                    if (g.get("result") or {}).get("is_real") is True]
        return super().from_corpus(RealEvidence(), eligible)
