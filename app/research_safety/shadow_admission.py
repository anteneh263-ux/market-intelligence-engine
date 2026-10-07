"""SHADOW admission — eligibility ONLY, side-effect-free, FAIL-CLOSED.

Classifies a candidate from STRUCTURED evidence already produced by the existing systems.
It NEVER infers the hypothesis from source text, and it NEVER uses permissive defaults:
every required piece of evidence must be explicitly present and valid, or the outcome is
INVALID. It always returns a structured result and never raises on malformed evidence.

It NEVER creates a manifest, directory or ledger; starts SHADOW or forward testing;
selects candidates; changes any status; writes to knowledge_base/research_safety/; or
weakens the existing PROMOTE gate. Family DSR remains mandatory for PROMOTE.

Outcomes: INVALID | REJECT | ELIGIBLE_FOR_SHADOW | ELIGIBLE_FOR_PROMOTE.
Missing required structured evidence -> INVALID (never SHADOW-eligible). Missing
target-control evidence is not "inconclusive". Missing family DSR is not "low DSR".

Research and simulation only — no broker, no real orders, no private keys.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from app.research_safety.promotion_gate import DEFAULT_THRESHOLDS

INVALID = "INVALID"
REJECT = "REJECT"
ELIGIBLE_FOR_SHADOW = "ELIGIBLE_FOR_SHADOW"
ELIGIBLE_FOR_PROMOTE = "ELIGIBLE_FOR_PROMOTE"

_VERDICTS = {"candidate_wins", "inconclusive", "control_wins"}
_GATE_DECISIONS = {"PROMOTE", "HOLD", "REJECT"}

_NOTE = ("Eligibility only — this evaluation started NOTHING: no manifest, directory, "
         "ledger, SHADOW test, forward test, broker, order or private key was created, and "
         "no existing strategy status was changed.")


def _is_true(v) -> bool:
    return v is True


def _is_false(v) -> bool:
    return v is False


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pos_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _nonneg_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _sub(evidence: Dict, key: str) -> Dict:
    v = evidence.get(key)
    return v if isinstance(v, dict) else {}


def evaluate_shadow_admission(evidence: Dict) -> Dict:
    """Classify SHADOW admission from structured evidence. Pure, side-effect-free, never
    raises — malformed evidence yields a structured INVALID."""
    try:
        return _evaluate(evidence if isinstance(evidence, dict) else {})
    except Exception as exc:                             # defensive: never raise
        return {
            "kind": "shadow_admission", "outcome": INVALID, "technical_validity": False,
            "shadow_eligible": False, "promote_eligible": False, "requirements": {},
            "blocking_reasons": [f"internal error while evaluating evidence: {type(exc).__name__}"],
            "unresolved_reasons": [], "family_dsr": None, "family_dsr_mandatory_bar": None,
            "effective_trial_count": None, "trade_count": None, "target_control_verdict": None,
            "input_audit_result": {}, "data_quality_classification": None,
            "data_quality_complete": None, "note": _NOTE,
        }


def _evaluate(evidence: Dict) -> Dict:
    dsr_bar = float(DEFAULT_THRESHOLDS["dsr"])
    min_trades = int(DEFAULT_THRESHOLDS["min_trades"])

    bt = _sub(evidence, "backtest")
    ia = _sub(evidence, "input_audit")
    tc = _sub(evidence, "target_control")
    dq = _sub(evidence, "data_quality")
    gate = _sub(evidence, "promotion_gate")
    ar = _sub(evidence, "absolute_risk")
    rc = _sub(evidence, "regime_coverage")

    fam = evidence.get("family_dsr")
    trades = evidence.get("trade_count")
    eff = evidence.get("effective_trial_count")
    max_exp = evidence.get("max_exposure")
    max_exp_limit = evidence.get("max_exposure_limit")
    verdict = tc.get("verdict")
    dq_class = dq.get("classification")
    dq_complete = dq.get("all_required_checks_passed")

    result_survives = evidence.get("result_survives_costs")
    econ_supported = evidence.get("economically_supported")

    # ---- required evidence: presence + technical validity (fail on any) ------ #
    # NOTE: input-audit is consumed via `promotable`, the run_audit verdict that already
    # encodes declared_present + deviations — so we do NOT separately require declared_present
    # (schema-compatible with the existing run_audit producer).
    requirements = {
        "backtest_real": _is_true(bt.get("is_real")),
        "backtest_causal": _is_true(bt.get("causal")),
        "timing_valid": _is_true(evidence.get("timing_valid")),
        "technical_failure_false": _is_false(evidence.get("technical_failure")),
        "input_audit_pass": _is_true(ia.get("promotable")),
        "data_quality_pass": dq_class == "PASS",
        "data_quality_complete": _is_true(dq_complete),
        "costs_included": _is_true(evidence.get("costs_included")),
        "economic_mechanism_documented": bool(str(evidence.get("economic_mechanism") or "").strip()),
        "preregistered": _is_true(evidence.get("preregistered")),
        "risk_rules_frozen": _is_true(evidence.get("risk_rules_frozen")),
        "max_exposure_valid": _num(max_exp) and max_exp > 0,
        "max_exposure_limit_present": _num(max_exp_limit) and max_exp_limit > 0,
        "absolute_risk_present": isinstance(ar.get("breached"), bool),
        "promotion_gate_present": gate.get("decision") in _GATE_DECISIONS,
        "target_control_present": verdict in _VERDICTS,
        "family_dsr_present": _num(fam),
        "effective_trial_count_valid": _pos_int(eff),
        "trade_count_valid": _nonneg_int(trades),
        # these must be PRESENT booleans (schema); a False VALUE is a REJECT finding, below
        "result_survives_costs_present": isinstance(result_survives, bool),
        "economically_supported_present": isinstance(econ_supported, bool),
    }
    technical_validity = all(requirements.values())

    blocking_reasons: List[str] = []
    unresolved_reasons: List[str] = []

    if not technical_validity:
        blocking_reasons = [k for k, v in requirements.items() if not v]
        if dq_class in ("INVALID", "INVALIDATED"):
            blocking_reasons.append(f"data_quality={dq_class} (corrupt required data)")
        elif dq_class == "NOT_AVAILABLE":
            blocking_reasons.append("data_quality=NOT_AVAILABLE (clean data not established)")
        elif dq_class == "PASS" and not _is_true(dq_complete):
            blocking_reasons.append("data_quality incomplete (all_required_checks_passed is not True)")
        outcome, shadow, promote = INVALID, False, False
    else:
        # ---- REJECT gates: valid negative findings (NOT corrupt experiments) - #
        if _is_false(result_survives):
            blocking_reasons.append("result disappears under locked costs")
        if _is_false(econ_supported):
            blocking_reasons.append("economically unsupported")
        if _num(max_exp) and _num(max_exp_limit) and max_exp > max_exp_limit:
            blocking_reasons.append(f"max exposure {max_exp} above the frozen limit {max_exp_limit}")
        if _is_true(ar.get("breached")):
            blocking_reasons.append("outside absolute risk limits")
        if verdict == "control_wins":
            blocking_reasons.append("clearly dominated by the target-only control")
        if blocking_reasons:
            outcome, shadow, promote = REJECT, False, False
        else:
            gate_promote = gate.get("decision") == "PROMOTE"
            family_ok = float(fam) >= dsr_bar
            if gate_promote and family_ok:
                outcome, shadow, promote = ELIGIBLE_FOR_PROMOTE, False, True
            else:
                if not family_ok:
                    unresolved_reasons.append(f"family DSR {fam} below the mandatory bar {dsr_bar}")
                if int(trades) < min_trades:
                    unresolved_reasons.append(f"too few trades ({trades} < {min_trades})")
                if verdict == "inconclusive":
                    unresolved_reasons.append("target-control verdict is inconclusive")
                missing_reg = sorted(set(rc.get("required") or []) - set(rc.get("observed") or []))
                if missing_reg:
                    unresolved_reasons.append(f"missing regime exposure: {missing_reg}")
                dur, mindur = evidence.get("duration_days"), evidence.get("min_duration_days")
                if _num(dur) and _num(mindur) and float(dur) < float(mindur):
                    unresolved_reasons.append(f"insufficient duration ({dur} < {mindur} days)")
                if not gate_promote:
                    unresolved_reasons.append(
                        f"promotion-gate decision {gate.get('decision')!r} is not PROMOTE")
                if not unresolved_reasons:
                    unresolved_reasons.append("promotion evidence unresolved")
                outcome, shadow, promote = ELIGIBLE_FOR_SHADOW, True, False

    return {
        "kind": "shadow_admission",
        "outcome": outcome,
        "technical_validity": technical_validity,
        "shadow_eligible": shadow,
        "promote_eligible": promote,
        "requirements": requirements,
        "blocking_reasons": blocking_reasons,
        "unresolved_reasons": unresolved_reasons,
        "family_dsr": fam if _num(fam) else None,
        "family_dsr_mandatory_bar": dsr_bar,
        "effective_trial_count": eff if _pos_int(eff) else None,
        "trade_count": trades if _nonneg_int(trades) else None,
        "target_control_verdict": verdict if verdict in _VERDICTS else None,
        "input_audit_result": ia,
        "data_quality_classification": dq_class,
        "data_quality_complete": dq_complete if isinstance(dq_complete, bool) else None,
        "note": _NOTE,
    }
