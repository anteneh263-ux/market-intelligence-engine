"""Strategy Research — a module inside the Market Intelligence Engine.

Treats every trading strategy as a falsifiable scientific hypothesis and runs it
through ONE frozen, reproducible evaluation pipeline:

    provider (swappable) -> walk-forward OOS -> costs/slippage stress ->
    benchmarks -> deflated-Sharpe / robustness stats -> mechanical verdict ->
    auto-updated dossier + INDEX + meta-hypotheses.

Public surface:
    from app.strategy_research import evaluate, EvalRequest
    from app.strategy_research import available_strategies, available_providers
"""
from .evaluation import (
    DEFAULT_MATRIX,
    MATRIX_V1,
    MATRIX_V2,
    MATRIX_V3,
    MATRIX_V4,
    EvalRequest,
    EvaluationReport,
    FrozenMatrix,
    evaluate,
)
from .providers import available_providers, get_provider
from .strategies import available_strategies, get_strategy

MATRICES = {"srp-matrix-v1": MATRIX_V1, "srp-matrix-v2": MATRIX_V2,
            "srp-matrix-v3": MATRIX_V3, "v1": MATRIX_V1, "v2": MATRIX_V2, "v3": MATRIX_V3,
            "srp-matrix-v4": MATRIX_V4, "v4": MATRIX_V4}

__all__ = [
    "evaluate", "EvalRequest", "EvaluationReport", "FrozenMatrix",
    "DEFAULT_MATRIX", "MATRIX_V1", "MATRIX_V2", "MATRIX_V3", "MATRIX_V4", "MATRICES",
    "available_strategies", "get_strategy",
    "available_providers", "get_provider",
]
