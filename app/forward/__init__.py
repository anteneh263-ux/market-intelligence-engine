"""Locked forward-testing lifecycle.

A strategy that receives PROMOTE from the research promotion gate can be *locked* and
tested on new market data without any change to its code, parameters or risk rules.
The public surface:

    build_manifest        — deterministic locked manifest (code/param hashes, thresholds)
    ForwardStore          — atomic, append-only filesystem store for a forward test
    ForwardEngine         — start / run / verify / evaluate / status lifecycle
    ForwardError          — lifecycle violation
    evaluate_forward      — PASSED | FAILED | EXTEND against the locked reference
    simulate/simulate_forward — the single causal simulator shared by backtest + forward

Research only: no live broker, no real orders, no private keys — this is simulation.
"""
from __future__ import annotations

from .evaluate import evaluate_forward
from .engine import ForwardEngine, ForwardError
from .manifest import (
    DEFAULT_THRESHOLDS,
    MANIFEST_VERSION,
    build_manifest,
    canonical_json,
    code_hash,
    param_hash,
)
from .simulate import (
    executed_positions,
    metrics_from_observations,
    simulate,
    simulate_forward,
    window_metrics,
)
from .store import ForwardStore

__all__ = [
    "MANIFEST_VERSION", "DEFAULT_THRESHOLDS", "build_manifest", "canonical_json",
    "code_hash", "param_hash",
    "ForwardStore", "ForwardEngine", "ForwardError",
    "evaluate_forward",
    "simulate", "simulate_forward", "executed_positions", "window_metrics",
    "metrics_from_observations",
]
