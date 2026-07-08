"""AI Research Assistant — grounded interface to the Knowledge Base.

The assistant answers research questions by querying the corpus through the
Knowledge Base query API and composing an answer from *recorded* results only.
It never invents numbers: every claim traces to a result record or dossier, and
answers carry the citations.

This is deliberately the seam an LLM front-end plugs into: `answer()` returns
structured evidence + a grounded natural-language summary, so a language model
(or a human) consumes the same audited facts. Keeping the retrieval layer
deterministic is what makes the assistant trustworthy rather than fluent-but-wrong.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from app.knowledge_base import KnowledgeBase, query


@dataclass
class Answer:
    question: str
    text: str
    evidence: List[Dict] = field(default_factory=list)
    citations: List[str] = field(default_factory=list)


class ResearchAssistant:
    def __init__(self, kb: KnowledgeBase | None = None):
        self.kb = kb or KnowledgeBase()

    def answer(self, question: str) -> Answer:
        q = question.lower().strip()
        # direct strategy lookup, e.g. "H-010" or "macd"
        for r in query.tested_results(self.kb):
            if r["strategy_id"].lower() in q or str(r.get("name", "")).lower() in q:
                return self._strategy(r)
        if any(w in q for w in ("generali", "across markets", "across assets")):
            return self._generalizers()
        if any(w in q for w in ("graveyard", "died", "failed", "fragile")):
            return self._graveyard()
        if any(w in q for w in ("supported", "what works", "winner", "best")):
            return self._best()
        if any(w in q for w in ("summary", "corpus", "overview", "state", "how many")):
            return self._summary()
        return self._summary(prefix="I answer from the recorded corpus. Try asking about a "
                             "strategy id, generalization, the graveyard, or a summary.\n\n")

    # ---- intents ------------------------------------------------------------ #
    def _summary(self, prefix: str = "") -> Answer:
        vc = query.verdict_counts(self.kb)
        n = sum(vc.values())
        lead = query.leaderboard(self.kb, "dsr", 3)
        text = prefix + (f"Corpus: {n} strategies tested. Verdicts: "
                         + ", ".join(f"{k} {v}" for k, v in sorted(vc.items())) + ".")
        if lead:
            text += " Highest deflated-Sharpe so far: " + ", ".join(
                f"{r['strategy_id']} ({_f(r.get('dsr'))})" for r in lead) + "."
        text += (" No strategy is Supported." if not query.supported(self.kb)
                 else f" Supported: {[r['strategy_id'] for r in query.supported(self.kb)]}.")
        return Answer(text, text, evidence=lead, citations=["INDEX.md", "results/"])

    def _generalizers(self) -> Answer:
        rs = query.generalizers(self.kb, 0.0)
        top = rs[:5]
        text = "Generalization across markets (by generalization score): " + (
            ", ".join(f"{r['strategy_id']} {_f(r.get('generalization'))} "
                      f"({r['verdict']})" for r in top) if top else "none tested yet") + "."
        if top:
            text += (f" Best generalizer: {top[0]['strategy_id']} "
                     f"({top[0].get('class')}).")
        return Answer("generalizers", text, evidence=top,
                      citations=["CROSS_ASSET_GENERALIZATION.md", "results/"])

    def _graveyard(self) -> Answer:
        g = query.graveyard(self.kb)
        text = (f"{len(g)} strategies in the graveyard: "
                + ", ".join(f"{r['strategy_id']} ({r.get('cause_of_death') or 'failed gates'})"
                            for r in g) + ".") if g else "The graveyard is empty."
        return Answer("graveyard", text, evidence=g, citations=["INDEX.md"])

    def _best(self) -> Answer:
        sup = query.supported(self.kb)
        if sup:
            text = "Supported strategies: " + ", ".join(r["strategy_id"] for r in sup) + "."
            return Answer("best", text, evidence=sup, citations=["INDEX.md"])
        lead = query.leaderboard(self.kb, "dsr", 3)
        text = ("Nothing is Supported yet. Closest (highest deflated Sharpe, still below the "
                "0.95 bar): " + ", ".join(f"{r['strategy_id']} {_f(r.get('dsr'))}" for r in lead) + ".")
        return Answer("best", text, evidence=lead, citations=["results/"])

    def _strategy(self, r: Dict) -> Answer:
        cite = self.kb.dossier_path(r["strategy_id"]) or "results/"
        text = (f"{r['strategy_id']} {r.get('name')} [{r.get('class')}]: verdict "
                f"{r['verdict']} (matrix {r.get('matrix_version')}). "
                f"OOS Sharpe(ann) {_f(r.get('sharpe'))}, deflated Sharpe {_f(r.get('dsr'))}, "
                f"generalization {_f(r.get('generalization'))}, "
                f"maxDD {_f(r.get('max_drawdown'))}, trades {r.get('num_trades')}.")
        return Answer(r["strategy_id"], text, evidence=[r], citations=[cite])


def _f(x):
    return "—" if x is None else f"{x:.3f}"
