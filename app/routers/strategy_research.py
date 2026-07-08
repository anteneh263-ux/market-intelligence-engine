"""Strategy Research endpoints — the trading-strategy evaluation module exposed
through the Market Intelligence Engine API.

RESEARCH ONLY. Every evaluation is a falsification test; no signals, no execution.
"""
from __future__ import annotations

from fastapi import APIRouter, Body

from app.strategy_research import (
    DEFAULT_MATRIX,
    EvalRequest,
    available_providers,
    available_strategies,
    evaluate,
)
from app.strategy_research.dossier import apply_report

router = APIRouter(prefix="/api/strategy-research", tags=["strategy-research"])


@router.get("/catalogue")
def catalogue():
    """Strategies, providers, and the frozen-matrix identity."""
    return {
        "strategies": available_strategies(),
        "providers": available_providers(),
        "matrix": {"version": DEFAULT_MATRIX.version, "hash": DEFAULT_MATRIX.hash()},
        "note": "Research only. Verdicts are statements about evidence, not advice.",
    }


@router.post("/evaluate")
def evaluate_endpoint(
    strategy_id: str = Body(...),
    provider: str = Body("csv"),
    assets: list[str] = Body(...),
    timeframe: str = Body("1d"),
    start: str | None = Body(None),
    end: str | None = Body(None),
    limit: int | None = Body(1500),
    csv_root: str = Body("data_csv"),
    write: bool = Body(False),
):
    """Run one strategy through the frozen pipeline. `write=true` records STEP 5
    into the knowledge base (real data only — synthetic runs never set a verdict)."""
    req = EvalRequest(strategy_id=strategy_id, provider=provider, assets=assets,
                      timeframe=timeframe, start=start, end=end, limit=limit)
    pk = {"root": csv_root} if provider == "csv" else {}
    rep = evaluate(req, DEFAULT_MATRIX, provider_kwargs=pk)
    out = rep.to_dict()
    if write:
        out["kb_update"] = apply_report(rep)
    return out
