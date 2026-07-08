"""Knowledge Base — query API.

Read-only, structured questions over the corpus. This is the ONLY surface the
AI assistant and meta-learning use to interrogate the knowledge base, so every
answer is grounded in a recorded result and can be cited.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List

from .store import KnowledgeBase

TESTED = {"Supported", "Not Supported", "Inconclusive", "Graveyard"}


def tested_results(kb: KnowledgeBase) -> List[Dict]:
    """Only REAL-data results count as evidence. Synthetic (is_real=False) runs are
    excluded here so they can never enter meta-learning or an assistant answer."""
    return [r for r in kb.all_results()
            if r.get("verdict") in TESTED and r.get("is_real", False)]


def verdict_counts(kb: KnowledgeBase) -> Dict[str, int]:
    return dict(Counter(r["verdict"] for r in tested_results(kb)))


def by_class(kb: KnowledgeBase) -> Dict[str, List[Dict]]:
    d: Dict[str, List[Dict]] = defaultdict(list)
    for r in tested_results(kb):
        d[r.get("class", "unknown")].append(r)
    return dict(d)


def generalizers(kb: KnowledgeBase, min_score: float = 0.4) -> List[Dict]:
    """Strategies whose generalization score clears a bar, best first."""
    rs = [r for r in tested_results(kb) if (r.get("generalization") or 0) >= min_score]
    return sorted(rs, key=lambda r: r.get("generalization") or 0, reverse=True)


def leaderboard(kb: KnowledgeBase, key: str = "dsr", n: int = 10) -> List[Dict]:
    rs = [r for r in tested_results(kb) if r.get(key) is not None]
    return sorted(rs, key=lambda r: r.get(key) or 0, reverse=True)[:n]


def graveyard(kb: KnowledgeBase) -> List[Dict]:
    return [r for r in tested_results(kb) if r["verdict"] == "Graveyard"]


def supported(kb: KnowledgeBase) -> List[Dict]:
    return [r for r in tested_results(kb) if r["verdict"] == "Supported"]


def strategy(kb: KnowledgeBase, strategy_id: str) -> Dict:
    return kb.result(strategy_id)
