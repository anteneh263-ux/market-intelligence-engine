"""Research-safety gates & trial auditing.

Four additive, deterministic tools that reduce false positives WITHOUT changing any
existing evaluation result:

  1. target_control  - compare a candidate to a simple target-only control (same asset/period).
  2. trial_ledger    - count EVERY real research trial in the deflated-Sharpe budget.
  3. input_audit     - declared-vs-actual input columns via explicit instrumentation.
  4. promotion_gate  - one aggregate gate a strategy must pass before paper trading.

All artefacts are machine-readable JSON, written atomically. Research only; no live
trading, no broker, no order execution.
"""
from .trial_ledger import TrialLedger
from .input_audit import TrackingFrame, run_audit
from .target_control import TargetOnlyControl, compare
from .promotion_gate import evaluate_promotion, decide, GateStatus, Decision

__all__ = ["TrialLedger", "TrackingFrame", "run_audit", "TargetOnlyControl",
           "compare", "evaluate_promotion", "decide", "GateStatus", "Decision"]
