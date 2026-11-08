"""Promotion gate before paper trading.

A strategy must pass ONE aggregate gate before it may be promoted to paper trading.
Each sub-gate reports PASS / FAIL / NOT_AVAILABLE with the measured value, the
threshold and an explanation. NOT_AVAILABLE is NEVER treated as PASS:

    any mandatory FAIL           -> REJECT
    else any mandatory NA        -> HOLD
    else (all mandatory PASS)    -> PROMOTE

Research gate only: no live trading, no broker, no order execution.
"""
from __future__ import annotations

import json
import os
from enum import Enum
from typing import Dict, List, Optional, Sequence, Set

import numpy as np

from app.paper.schema import resolve_target
from app.strategy_research import EvalRequest, MATRICES, evaluate, get_provider, get_strategy
from app.strategy_research.dossier import _KB_ROOT
from app.strategy_research.providers import DataRequest

from .input_audit import run_audit
from .target_control import compare
from .control_test import VERSION as CONTROL_TEST_VERSION, compare_calibrated
from .trial_ledger import _atomic_write


class GateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class Decision(str, Enum):
    PROMOTE = "PROMOTE"
    HOLD = "HOLD"
    REJECT = "REJECT"


DEFAULT_THRESHOLDS = {"max_drawdown": 0.60, "min_trades": 30, "dsr": 0.95}


def _gate(name: str, status: GateStatus, measured, threshold, explanation: str,
          mandatory: bool = True) -> Dict:
    return {"gate": name, "status": status.value, "measured": measured,
            "threshold": threshold, "explanation": explanation, "mandatory": mandatory}


def decide(gates: List[Dict]) -> Decision:
    """Aggregate the sub-gates. NOT_AVAILABLE never counts as PASS."""
    mand = [g for g in gates if g.get("mandatory", True)]
    if any(g["status"] == GateStatus.FAIL.value for g in mand):
        return Decision.REJECT
    if any(g["status"] == GateStatus.NOT_AVAILABLE.value for g in mand):
        return Decision.HOLD
    return Decision.PROMOTE


def _causal_ok(strategy, df, k: int) -> bool:
    full = strategy.positions(df).to_numpy()
    trunc = strategy.positions(df.iloc[:k].reset_index(drop=True)).to_numpy()
    return bool(np.array_equal(np.nan_to_num(full[:k]), np.nan_to_num(trunc[:k])))


def evaluate_promotion(strategy_id: str, assets: Sequence[str], *,
                       control_id: Optional[str] = None, timeframe: str = "1d",
                       matrix: str = "srp-matrix-v2", provider: str = "csv",
                       provider_kwargs: Optional[Dict] = None,
                       declared: Optional[Set[str]] = None,
                       central: Optional[Set[str]] = None,
                       thresholds: Optional[Dict] = None, root: Optional[str] = None,
                       write: bool = True, dsr_override: Optional[float] = None,
                       control_test: str = "legacy") -> Dict:
    """``control_test``: "legacy" (default, unchanged 0.05 per-bar rule) or "target-control-test-v1"
    (opt-in calibrated test, docs/specs/SPEC-target-control-calibrated-v1.md)."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    m = MATRICES.get(matrix, MATRICES["v2"])
    pk = provider_kwargs or {}
    gates: List[Dict] = []

    rep = evaluate(EvalRequest(strategy_id=strategy_id, provider=provider, assets=list(assets),
                   timeframe=timeframe, matrix_version=m.version), m, pk)
    h = rep.headline
    n_bars = int(h.get("n_bars", 0) or 0)

    # 1) valid backtest
    vb = GateStatus.PASS if (rep.is_real and n_bars > 0 and rep.assets_evaluated) else GateStatus.FAIL
    gates.append(_gate("valid_backtest", vb,
                       {"is_real": rep.is_real, "n_bars": n_bars, "assets": rep.assets_evaluated},
                       "is_real and n_bars > 0", "Backtest produced a real, non-empty result."))
    # 2) out-of-sample / walk-forward
    oos = GateStatus.PASS if rep.condition_rows else GateStatus.NOT_AVAILABLE
    gates.append(_gate("out_of_sample_walkforward", oos, {"condition_rows": len(rep.condition_rows)},
                       ">= 1 walk-forward OOS row", "5-split walk-forward OOS present."))
    # 3) max drawdown
    dd = float(h.get("max_drawdown", 1.0) or 1.0)
    gates.append(_gate("max_drawdown", GateStatus.PASS if dd <= th["max_drawdown"] else GateStatus.FAIL,
                       round(dd, 4), f"<= {th['max_drawdown']}", "OOS max drawdown within the limit."))
    # 4) minimum trades / active days
    nt = int(h.get("num_trades", 0) or 0)
    gates.append(_gate("min_trades", GateStatus.PASS if nt >= th["min_trades"] else GateStatus.FAIL,
                       nt, f">= {th['min_trades']}", "Enough OOS trades for statistical power."))
    # 5) costs & slippage applied
    has_cost = any(r.get("cost_bps") == m.headline_cost_bps and r.get("slippage_bps") == m.headline_slippage_bps
                   for r in rep.condition_rows)
    gates.append(_gate("costs_slippage", GateStatus.PASS if has_cost else GateStatus.NOT_AVAILABLE,
                       {"headline_cost_bps": m.headline_cost_bps, "headline_slippage_bps": m.headline_slippage_bps},
                       "evaluated at the headline cost + slippage", "Frozen cost/slippage ladder applied."))
    # 6) DSR / overfitting
    strategy_dsr = float(h.get("dsr", 0.0) or 0.0)
    dsr = float(dsr_override) if dsr_override is not None else strategy_dsr
    gates.append(_gate("dsr_overfitting", GateStatus.PASS if dsr >= th["dsr"] else GateStatus.FAIL,
                       {"family_dsr": round(dsr, 4), "strategy_dsr": round(strategy_dsr, 4),
                        "source": "trial_ledger" if dsr_override is not None else "strategy_grid"},
                       f">= {th['dsr']}", "Deflated Sharpe clears the overfitting bar."))

    # data frame for audit + causality checks
    asset0 = (rep.assets_evaluated or list(assets))[0]
    df0 = get_provider(provider, **pk).get(DataRequest(asset=asset0, timeframe=timeframe)).df
    strat = get_strategy(strategy_id)

    # 7) input audit
    dec = declared if declared is not None else getattr(strat, "declared_inputs", None)
    if dec is None:
        gates.append(_gate("input_audit", GateStatus.NOT_AVAILABLE, {"declared_present": False},
                           "no undeclared use; central inputs read",
                           "Strategy did not declare its inputs (declared_inputs is None)."))
        audit: Optional[Dict] = None
    else:
        audit = run_audit(strat, df0, declared=dec, central=central)
        gates.append(_gate("input_audit", GateStatus.PASS if audit["promotable"] else GateStatus.FAIL,
                           audit["deviations"], "no undeclared use; central inputs read",
                           "Declared vs actual input columns match."))
    # 8) target-only control
    if control_id is None:
        gates.append(_gate("target_control", GateStatus.NOT_AVAILABLE, None, "candidate_wins",
                           "No control provided; supply control_id to enable this gate."))
        tc: Optional[Dict] = None
    else:
        if control_test == "legacy":
            tc = compare(strategy_id, control_id, assets, timeframe=timeframe, matrix=matrix,
                         provider=provider, provider_kwargs=pk)
        elif control_test == CONTROL_TEST_VERSION:
            tc = compare_calibrated(strategy_id, control_id, assets, timeframe=timeframe, matrix=matrix,
                                    provider=provider, provider_kwargs=pk)
            tc["sharpe_tolerance"], tc["sharpe_unit"] = None, "annual_net_delta_test"
            tc["delta"] = {"sharpe_annual": tc["delta_sharpe_annual"], "interval": tc["interval"]}
        else:
            raise ValueError(f"unknown control_test: {control_test}")
        tc_status = (GateStatus.PASS if tc["verdict"] == "candidate_wins" else
                     GateStatus.NOT_AVAILABLE if tc["verdict"] == "inconclusive" else
                     GateStatus.FAIL)
        gates.append(_gate("target_control", tc_status,
                           {"verdict": tc["verdict"], "delta": tc["delta"],
                            "tolerance": tc["sharpe_tolerance"],
                            "unit": tc.get("sharpe_unit", "per_bar")},
                           "candidate_wins", "Candidate must beat a target-only control."))
    # 9) paper vs backtest signal match on a historical tail
    k_tail = max(len(df0) - 3, 50)
    causal_tail = _causal_ok(strat, df0, k_tail)
    pos = strat.positions(df0).to_numpy()
    paper_sig = np.array([resolve_target(strat.direction, float(x)) for x in pos], dtype=float)
    clamp = np.array([(0.0 if (strat.direction == "long_only" and x < 0) else x) for x in pos], dtype=float)
    mapping_match = bool(np.array_equal(np.nan_to_num(paper_sig), np.nan_to_num(clamp)))
    gates.append(_gate("paper_backtest_signal_match",
                       GateStatus.PASS if (causal_tail and mapping_match) else GateStatus.FAIL,
                       {"causal_tail": causal_tail, "mapping_match": mapping_match},
                       "paper signal == backtest signal on the same tail window",
                       "Paper and backtest agree on the historical tail (no look-ahead/repaint)."))
    # 10) signal at close, execute next bar (causal)
    k_mid = max(len(df0) // 2, 50)
    causal_mid = _causal_ok(strat, df0, k_mid)
    gates.append(_gate("signal_timing_close_next_bar", GateStatus.PASS if causal_mid else GateStatus.FAIL,
                       {"truncation_invariant": causal_mid},
                       "decide on the completed candle; execute no earlier than the next bar",
                       "Causal: positions depend only on past bars; the engine shifts them forward one bar."))

    decision = decide(gates)
    report = {
        "kind": "promotion_gate", "strategy_id": strategy_id, "control_id": control_id,
        "assets": list(assets), "timeframe": timeframe, "matrix": m.version,
        "decision": decision.value, "gates": gates,
        "note": "Research gate only - no live trading, no broker, no order execution.",
    }
    if write:
        base = os.path.join(root or _KB_ROOT, "research_safety", "promotions")
        _atomic_write(os.path.join(base, f"{strategy_id}.json"),
                      json.dumps(report, indent=2, sort_keys=True, default=str))
    return report
