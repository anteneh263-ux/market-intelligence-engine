"""Research Core — the orchestrator.

Runs the scientific-method flow end to end:

    request → [module.evaluate under a frozen matrix] → EvaluationReport
            → [Knowledge Base records it]            → dossier + INDEX + results + meta
            → [Meta-learning refreshes the corpus]   → learning report

This is the engine's brain. Every other layer is invoked from here in a fixed,
auditable order. The orchestrator itself holds no science — it wires the modules.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from app.knowledge_base import KnowledgeBase
from app.meta_learning import mine, render_report

from . import module_registry, versioning


@dataclass
class RunOutcome:
    target: str
    module: str
    matrix_version: str
    matrix_hash: str
    verdict: str
    is_real: bool
    recorded: Dict = field(default_factory=dict)
    report: Dict = field(default_factory=dict)


class Orchestrator:
    def __init__(self, kb: Optional[KnowledgeBase] = None):
        self.kb = kb or KnowledgeBase()

    def run(self, target: str, assets: List[str], *, module: str = "strategy_evaluation",
            matrix_version: Optional[str] = None, provider: str = "csv",
            timeframe: str = "1d", limit: Optional[int] = None,
            provider_kwargs: Optional[Dict] = None, write: bool = True) -> RunOutcome:
        mod = module_registry.get_module(module)
        matrix = versioning.resolve(matrix_version)
        if matrix.version == "srp-matrix-v5" and write:
            raise ValueError("srp-matrix-v5 is evaluation-only until its persistence and dossier "
                             "format are reviewed; run with write=False / --no-write.")
        # Pre-registered corpus strategies (H-###) get a complete, reproducible dossier
        # BEFORE STEP 5. Generated (G-) and user-imported (U-) strategies are tracked in
        # their own namespaces (discovery ledger / workflow reports), not the H corpus.
        dossier = {}
        if module == "strategy_evaluation" and write and str(target).upper().startswith("H-"):
            from .dossier_generator import ensure_dossier
            try:
                dossier = ensure_dossier(target, kb_root=self.kb.root, matrix=matrix)
            except Exception:
                dossier = {"written": False}
        req = mod.make_request(target=target, assets=assets, provider=provider,
                               timeframe=timeframe, limit=limit,
                               matrix_version=matrix.version)
        rep = mod.evaluate(req, matrix, provider_kwargs=provider_kwargs or {})
        recorded = self.kb.record(rep) if write else {}
        if dossier:
            recorded = {**recorded, "dossier": dossier}
        return RunOutcome(
            target=target, module=module, matrix_version=matrix.version,
            matrix_hash=matrix.hash(), verdict=rep.verdict, is_real=rep.is_real,
            recorded=recorded, report=rep.to_dict(),
        )

    def run_batch(self, targets: List[str], assets: List[str], **kw) -> List[RunOutcome]:
        outcomes = [self.run(t, assets, **kw) for t in targets]
        if kw.get("write", True):
            self.refresh_meta_learning()
        return outcomes

    def refresh_meta_learning(self) -> str:
        """Re-mine the corpus and persist the learning report into the KB."""
        report = mine(self.kb)
        md = render_report(report)
        return self.kb.write_artifact("LEARNING_REPORT.md", md)
