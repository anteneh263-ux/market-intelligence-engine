"""Knowledge Base — persistence layer (the corpus).

The single source of truth. Every evaluation result, dossier, meta-hypothesis
tally, and learning artifact lives under `trading_research/knowledge_base/`. All
other layers (meta-learning, the AI assistant, the API) read the corpus *only*
through this class — they never touch files directly.

Writing wraps the audited `strategy_research.dossier` routines (versioned STEP-5
dossier sections, INDEX rows, per-version results JSON, meta ledger) so recording
stays consistent with the data-integrity rules (synthetic never sets a verdict).
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from app.strategy_research.dossier import _KB_ROOT, apply_report
from app.strategy_research.evaluation.pipeline import EvaluationReport


class KnowledgeBase:
    def __init__(self, root: Optional[str] = None):
        self.root = root or _KB_ROOT
        self.results_dir = os.path.join(self.root, "results")

    # ---- write -------------------------------------------------------------- #
    def record(self, rep: EvaluationReport) -> Dict:
        """Record a report: dossier STEP-5 (versioned) + INDEX + results + meta."""
        return apply_report(rep, kb_root=self.root)

    # ---- read --------------------------------------------------------------- #
    def result(self, strategy_id: str, version: Optional[str] = None) -> Dict:
        sid = strategy_id.upper()
        fn = f"{sid}.{version}.json" if version else f"{sid}.json"
        path = os.path.join(self.results_dir, fn)
        if not os.path.exists(path):
            return {"strategy_id": sid, "verdict": "Not Yet Tested"}
        return json.load(open(path))

    def all_results(self) -> List[Dict]:
        """Latest result per strategy (the <id>.json pointers), sorted by id."""
        if not os.path.isdir(self.results_dir):
            return []
        out = []
        for fn in sorted(os.listdir(self.results_dir)):
            # skip versioned archives (<id>.<version>.json), keep <id>.json
            if fn.endswith(".json") and fn.count(".") == 1:
                out.append(json.load(open(os.path.join(self.results_dir, fn))))
        return out

    def versions(self, strategy_id: str) -> List[str]:
        sid = strategy_id.upper()
        if not os.path.isdir(self.results_dir):
            return []
        return sorted(fn[len(sid) + 1:-5] for fn in os.listdir(self.results_dir)
                      if fn.startswith(sid + ".") and fn.count(".") == 2)

    def meta_ledger(self) -> Dict:
        path = os.path.join(self.root, "meta_ledger.json")
        return json.load(open(path)) if os.path.exists(path) else {}

    def dossier_path(self, strategy_id: str) -> Optional[str]:
        sid = strategy_id.upper()
        if not os.path.isdir(self.root):
            return None
        for fn in os.listdir(self.root):
            if fn.startswith(sid) and fn.endswith(".md"):
                return os.path.join(self.root, fn)
        return None

    def write_artifact(self, name: str, content: str) -> str:
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path
