"""Research Core — research-module registry.

A *research module* is a self-contained capability the engine can run. Strategy
Evaluation is the first such module; others (e.g. a future data-quality auditor,
a portfolio constructor, a causal-inference module) register here and are driven
by the same orchestrator. This is what makes Strategy Evaluation "one module
inside the engine" rather than the whole system.

Each module exposes:
  name, description,
  catalogue()            -> what it can evaluate (ids)
  evaluate(request, matrix, provider_kwargs) -> a report object with .to_dict()
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

from app.strategy_research import (
    EvalRequest,
    available_providers,
    available_strategies,
    evaluate as _evaluate_strategy,
)


@dataclass
class ResearchModule:
    name: str
    description: str
    catalogue: Callable[[], List[str]]
    evaluate: Callable
    make_request: Callable


# ---- Strategy Evaluation module (adapter over app.strategy_research) ---------- #
def _strategy_request(**kw) -> EvalRequest:
    return EvalRequest(
        strategy_id=kw["target"], provider=kw.get("provider", "csv"),
        assets=kw["assets"], timeframe=kw.get("timeframe", "1d"),
        start=kw.get("start"), end=kw.get("end"), limit=kw.get("limit"),
        matrix_version=kw["matrix_version"],
    )


STRATEGY_EVALUATION = ResearchModule(
    name="strategy_evaluation",
    description="Falsification testing of trading strategies via the frozen pipeline.",
    catalogue=available_strategies,
    evaluate=_evaluate_strategy,
    make_request=_strategy_request,
)

_MODULES: Dict[str, ResearchModule] = {STRATEGY_EVALUATION.name: STRATEGY_EVALUATION}


def get_module(name: str) -> ResearchModule:
    if name not in _MODULES:
        raise KeyError(f"Unknown module '{name}'. Registered: {list(_MODULES)}")
    return _MODULES[name]


def list_modules() -> List[Dict]:
    return [{"name": m.name, "description": m.description, "catalogue": m.catalogue()}
            for m in _MODULES.values()]


def register_module(module: ResearchModule) -> None:
    _MODULES[module.name] = module


def providers() -> List[str]:
    return available_providers()
