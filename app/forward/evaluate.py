"""Forward-test evaluation.

Compares the forward (out-of-sample, post-lock) results against the LOCKED backtest
reference using ONLY the thresholds frozen into the manifest. Returns one of three
verdicts:

    PASSED  — every mandatory gate passes AND enough data to conclude.
    FAILED  — at least one CRITICAL gate fails (a realised drawdown breach, or a
              risk-adjusted collapse on a well-powered sample).
    EXTEND  — not yet enough observations / active days / trades to conclude, or a
              mandatory input is NOT_AVAILABLE.

Two invariants, enforced structurally:
  * A positive return alone NEVER yields PASSED — PASS requires the drawdown gate, the
    Sharpe floor and the reference-retention gate to all pass.
  * NOT_AVAILABLE is NEVER treated as PASS (mirrors the promotion gate) — a mandatory
    NA downgrades the verdict to EXTEND.

Research only: a verdict is a statement about *evidence*, never trading advice.
"""
from __future__ import annotations

from typing import Dict, List, Optional

PASS = "PASS"
FAIL = "FAIL"
NA = "NOT_AVAILABLE"


def _gate(name: str, kind: str, status: str, measured, threshold, explanation: str) -> Dict:
    return {"gate": name, "kind": kind, "status": status, "measured": measured,
            "threshold": threshold, "explanation": explanation}


def evaluate_forward(manifest: Dict, forward_metrics: Dict,
                     reference_metrics: Optional[Dict] = None) -> Dict:
    th = manifest["thresholds"]
    ref = reference_metrics or {}
    fm = forward_metrics or {}

    obs = int(fm.get("observations", 0) or 0)
    active_days = int(fm.get("active_days", 0) or 0)
    trades = int(fm.get("num_trades", 0) or 0)
    dd = float(fm.get("max_drawdown", 0.0) or 0.0)
    sharpe = float(fm.get("sharpe_per_period", 0.0) or 0.0)
    total_return = float(fm.get("total_return", 0.0) or 0.0)

    gates: List[Dict] = []

    # --- sufficiency gates (drive EXTEND) --------------------------------- #
    suff_obs = _gate("min_observations", "sufficiency",
                     PASS if obs >= th["min_observations"] else FAIL,
                     obs, th["min_observations"], "enough forward bars to conclude")
    suff_days = _gate("min_active_days", "sufficiency",
                      PASS if active_days >= th["min_active_days"] else FAIL,
                      active_days, th["min_active_days"], "enough bars with a live position")
    suff_trades = _gate("min_trades", "sufficiency",
                        PASS if trades >= th["min_trades"] else FAIL,
                        trades, th["min_trades"], "enough position changes for power")
    gates += [suff_obs, suff_days, suff_trades]
    sufficient = all(g["status"] == PASS for g in (suff_obs, suff_days, suff_trades))

    # --- critical gates --------------------------------------------------- #
    dd_ok = dd <= th["max_drawdown"]
    g_dd = _gate("max_drawdown", "critical", PASS if dd_ok else FAIL,
                 round(dd, 6), th["max_drawdown"],
                 "forward drawdown must stay within the locked limit")
    gates.append(g_dd)

    sharpe_ok = sharpe >= th["sharpe_floor"]
    g_sharpe = _gate("sharpe_floor", "critical", PASS if sharpe_ok else FAIL,
                     round(sharpe, 6), th["sharpe_floor"],
                     "forward risk-adjusted return must clear the floor")
    gates.append(g_sharpe)

    # reference retention: forward Sharpe vs a fraction of the locked reference Sharpe
    ref_sharpe = ref.get("sharpe_per_period")
    if ref_sharpe is None or float(ref_sharpe) <= 0.0:
        g_ret = _gate("reference_retention", "critical", NA,
                      round(sharpe, 6),
                      f"{th['reference_retention']} x reference Sharpe",
                      "locked reference Sharpe is missing or non-positive — cannot judge retention")
    else:
        need = th["reference_retention"] * float(ref_sharpe)
        g_ret = _gate("reference_retention", "critical",
                      PASS if sharpe >= need else FAIL, round(sharpe, 6), round(need, 6),
                      "forward Sharpe must retain a fraction of the locked reference Sharpe")
    gates.append(g_ret)

    critical = [g for g in gates if g["kind"] == "critical"]
    hard_fail = not dd_ok                                   # realised DD breach: decisive
    soft_fail = [g for g in critical if g["status"] == FAIL and g["gate"] != "max_drawdown"]
    na_mandatory = [g for g in critical if g["status"] == NA]

    reasons: List[str] = []

    # --- decision precedence --------------------------------------------- #
    if hard_fail:
        decision = "FAILED"
        reasons.append(f"max_drawdown {dd:.2%} breached the locked limit {th['max_drawdown']:.2%}")
    elif not sufficient:
        decision = "EXTEND"
        if obs < th["min_observations"]:
            reasons.append(f"only {obs} observations (<{th['min_observations']})")
        if active_days < th["min_active_days"]:
            reasons.append(f"only {active_days} active days (<{th['min_active_days']})")
        if trades < th["min_trades"]:
            reasons.append(f"only {trades} trades (<{th['min_trades']})")
    elif soft_fail:
        decision = "FAILED"
        for g in soft_fail:
            reasons.append(f"{g['gate']} failed: {g['measured']} vs {g['threshold']}")
    elif na_mandatory:
        decision = "EXTEND"                                 # NA never PASSES
        for g in na_mandatory:
            reasons.append(f"{g['gate']} not available: {g['explanation']}")
    else:
        decision = "PASSED"
        reasons.append("all mandatory gates passed on a sufficiently powered forward sample")

    return {
        "kind": "forward_evaluation",
        "forward_test_id": manifest["forward_test_id"],
        "strategy_id": manifest["strategy_id"],
        "strategy_version": manifest["strategy_version"],
        "decision": decision,
        "reasons": reasons,
        "sufficient": sufficient,
        "forward_metrics": fm,
        "reference_metrics": ref,
        "thresholds": th,
        "gates": gates,
        "note": ("Forward test is a SIMULATION on out-of-sample data — no live broker, "
                 "no real orders. A verdict is a statement about evidence, not advice. "
                 "Positive return alone is never PASSED; NOT_AVAILABLE is never PASSED."),
    }
