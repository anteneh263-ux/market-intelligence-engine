"""Platform endpoints — HTTP surface over the Research Core, Knowledge Base,
Meta-learning, and AI Research Assistant. RESEARCH ONLY.
"""
from __future__ import annotations

from fastapi import APIRouter, Body

from app.assistant import ResearchAssistant
from app.knowledge_base import KnowledgeBase, query
from app.meta_learning import mine
from app.research_core import Orchestrator, module_registry, versioning

router = APIRouter(prefix="/api/engine", tags=["engine"])
_kb = KnowledgeBase()


@router.get("/matrices")
def matrices():
    return {"active": versioning.ACTIVE_VERSION, "versions": versioning.list_versions()}


@router.get("/modules")
def modules():
    return {"modules": module_registry.list_modules(), "providers": module_registry.providers()}


@router.post("/evaluate")
def evaluate(target: str = Body(...), assets: list[str] = Body(...),
             module: str = Body("strategy_evaluation"), matrix: str = Body("v2"),
             provider: str = Body("csv"), timeframe: str = Body("1d"),
             csv_root: str = Body("data_csv"), write: bool = Body(True)):
    pk = {"root": csv_root} if provider == "csv" else {}
    out = Orchestrator(_kb).run(target, assets, module=module, matrix_version=matrix,
                                provider=provider, timeframe=timeframe,
                                provider_kwargs=pk, write=write)
    return out.__dict__


@router.get("/knowledge-base")
def kb_summary():
    return {"verdicts": query.verdict_counts(_kb), "results": _kb.all_results()}


@router.get("/knowledge-base/{strategy_id}")
def kb_strategy(strategy_id: str):
    return _kb.result(strategy_id)


@router.get("/learn")
def learn():
    return mine(_kb)


@router.get("/ask")
def ask(q: str):
    a = ResearchAssistant(_kb).answer(q)
    return {"question": q, "answer": a.text, "citations": a.citations, "evidence": a.evidence}
