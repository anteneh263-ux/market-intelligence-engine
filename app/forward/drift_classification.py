"""WP2B-3 — Shadow-Forward Drift Diagnostic (TEST-MODE ONLY).

Deterministic, fail-closed, read-only point-threshold drift diagnostics between a frozen REFERENCE
paper-session journal and a strictly-later FORWARD journal. This module is a NEW, separate component; it
does NOT modify or replace the frozen Phase-3 bootstrap-range evaluator (``shadow/consistency.py``) and it
never claims statistical consistency or profitability.

Authoritative design: PHASE4_WP2B_3_CONTROL_MATRIX_R3 / R3A / R3B (R3B governs > R3A > R3).

Hard safety properties:
  * PRODUCTION classification is mechanically disabled in v1 (empty approval allowlist) — every PRODUCTION
    call returns INVALID / PRODUCTION_POLICY_NOT_CONFIGURED and can never enable release or live execution.
  * ``LIVE_EXECUTION_ENABLED`` is never set here and both journals must carry
    ``live_execution_enabled == False``.
  * Metrics are READ from the exact frozen WP2B-1 / WP2B-2 cells (never recomputed).
  * The dependency contract is internal and immutable; a caller cannot supply or replace it.
"""
from __future__ import annotations

import hashlib
import math
import os
from typing import Dict, List, Optional, Tuple

from app.forward import metrics_v2 as _metrics_v2
from app.forward import trade_cycle_ledger as _tcl
from app.forward import segment_pipeline as _segp
from app.forward import cost_analysis as _cost
from app.forward.shadow import consistency as _consistency
from app.forward.shadow import ranges as _ranges
from app.forward.segment_pipeline import build_segment_report_from_journal, SegmentPipelineError
from app.forward.cost_analysis import build_cost_analysis_from_journal, CostAnalysisError, canonical_bytes

# The ONE canonical serialization is the frozen WP2B-2 serializer (sorted, compact, ensure_ascii=False).
_canonical_bytes = canonical_bytes


def _sha(obj) -> str:
    return hashlib.sha256(_canonical_bytes(obj)).hexdigest()


# --------------------------------------------------------------------------- #
# module constants                                                            #
# --------------------------------------------------------------------------- #
METHOD = "point-threshold-drift"
VERSION = "wp2b3-v1"
METRIC_REGISTRY_VERSION = "wp2b3-metric-registry-v1"

# Internal, immutable dependency contract — frozen SHA-256 of every consumed dependency file, verified at
# baseline a341c3a. The caller can NEVER supply or replace this (R3B block 7).
WP2B3_DEPENDENCY_CONTRACT: Dict[str, str] = {
    "metrics_v2.py": "707622376bd75b9d6630aae99b04f9eb8222d1b5508b52f0862e26006448808b",
    "trade_cycle_ledger.py": "5c36a60418b55eab9b36942101f56fb22a22a03fe4098cbb113200355795f1a2",
    "segment_pipeline.py": "eadbfda4d3fe32b7484433547353230513e3b9e00c4cb27f74cee487afdf5f43",
    "cost_analysis.py": "0aec32bc252842620f660860ba3c08d9c58595b597adcd5278baa93e4ed727cb",
    "shadow_consistency.py": "93dede8123d4fbb8dff90c42f141d6fdacc9c395e5098a7532bd9e42d8958a72",
    "shadow_ranges.py": "7476ee16e88129e7f258f0c9c877f34c006b4a93128ff51d768a16f86b8eb742",
    "segment_policy_version": "segment-policy-v1",
}

_DEP_MODULE_FILES = {
    "metrics_v2.py": _metrics_v2.__file__,
    "trade_cycle_ledger.py": _tcl.__file__,
    "segment_pipeline.py": _segp.__file__,
    "cost_analysis.py": _cost.__file__,
    "shadow_consistency.py": _consistency.__file__,
    "shadow_ranges.py": _ranges.__file__,
}

# Production is mechanically disabled in v1: the approval allowlist is EMPTY. WP2B-3 never mutates it.
APPROVED_PRODUCTION_THRESHOLD_POLICY_HASHES: Tuple[str, ...] = ()

# The exact, mandatory disclaimer (R3 §B3). Emitted verbatim in every report.
WITHIN_THRESHOLD_DISCLAIMER = (
    "WITHIN_THRESHOLD means only that the observed point deviation between the reference and forward "
    "values of a metric did not exceed a pre-approved numeric threshold. It does NOT mean the forward "
    "result is statistically consistent, statistically robust, profitable, validated, or safe to trade. "
    "It is not a statistical test, carries no confidence level, and makes no claim about future "
    "performance."
)

_LIMITATIONS = (
    WITHIN_THRESHOLD_DISCLAIMER,
    "Deterministic point-threshold drift diagnostics only; NOT the frozen bootstrap-range consistency "
    "evaluator (shadow/consistency.py). The two are never merged into one verdict.",
    "TEST-mode only in v1: production classification is mechanically disabled (empty approval allowlist).",
    "Single-segment (whole-journal) reference and forward required; multi-segment is out of scope.",
    "No result enables release eligibility or live execution.",
)

# status vocabularies (closed) ---------------------------------------------------------------------- #
VALID, INVALID = "VALID", "INVALID"
SUFFICIENT, PARTIAL, INSUFFICIENT = "SUFFICIENT", "PARTIAL", "INSUFFICIENT"
WITHIN_THRESHOLD, WARNING, DRIFT, NOT_EVALUATED = "WITHIN_THRESHOLD", "WARNING", "DRIFT", "NOT_EVALUATED"
ACT_INVALID, ACT_DRIFT, ACT_NEEDS_MORE_DATA, ACT_WARNING, ACT_WITHIN = (
    "INVALID", "DRIFT", "NEEDS_MORE_DATA", "WARNING", "WITHIN_THRESHOLD")

OK, NA, ST_INVALID = "OK", "NOT_AVAILABLE", "INVALID"


class DriftClassificationError(Exception):
    """Structural misuse or verification failure (non-path input, report/event bundle, journal that fails
    hash-chain verification). Semantic invalidity is reported as ``validity_status = INVALID`` instead."""


# --------------------------------------------------------------------------- #
# metric registry (closed) — exact frozen source cells, per R3A §1/§4          #
# --------------------------------------------------------------------------- #
_REGISTRY = [
    ("net_return", "performance_equity", ("seg", "net_return"), True, "bars"),
    ("realized_net_return", "performance_realized", ("cost_seg", "realized_net_return"), True,
     "completed_cycles"),
    ("friction_free_gross_return", "performance_gross",
     ("cost_seg", "completed_cycle_friction_free_gross_return"), True, "completed_cycles"),
    ("max_drawdown", "risk", ("seg", "max_drawdown"), True, "bars"),
    ("realized_friction_drag_return", "execution_cost", ("cost_seg", "realized_friction_drag_return"),
     True, "fills"),
    ("total_friction_cost_ratio", "execution_cost", ("cost_ratio", None), True, "fills"),
    ("win_rate", "performance_realized", ("seg", "win_rate"), True, "completed_cycles"),
    ("turnover", "context", ("seg", "turnover"), False, "fills"),
    ("exposure_time", "context", ("seg", "exposure_time"), False, "bars"),
]
_DRIVER_NAMES = [r[0] for r in _REGISTRY if r[3]]
# closed-registry contract: name -> (family, sample_unit, verdict_driver)
_REGISTRY_BY_NAME = {name: {"family": fam, "unit": unit, "verdict_driver": drv}
                     for name, fam, _src, drv, unit in _REGISTRY}


# --------------------------------------------------------------------------- #
# small helpers                                                               #
# --------------------------------------------------------------------------- #
def _is_pathlike(x) -> bool:
    return isinstance(x, (str, bytes, os.PathLike))


def _is_real_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v) -> bool:
    return _is_real_number(v) and math.isfinite(float(v))


def _manifest_hash(manifest: Dict) -> str:
    mh = manifest.get("manifest_hash") if isinstance(manifest, dict) else None
    if not (isinstance(mh, str) and mh):
        raise DriftClassificationError("manifest is missing a valid 'manifest_hash'")
    return mh


def _dependency_contract_hash() -> str:
    return _sha(WP2B3_DEPENDENCY_CONTRACT)


def _verify_dependency_contract() -> Optional[str]:
    """Recompute the SHA-256 of every consumed dependency file on disk and require equality with the
    pinned constant. Returns a reason_code on mismatch, else None (fail-closed on identity mismatch)."""
    for key, path in _DEP_MODULE_FILES.items():
        try:
            with open(path, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return "DEPENDENCY_CONTRACT_MISMATCH"
        if digest != WP2B3_DEPENDENCY_CONTRACT[key]:
            return "DEPENDENCY_CONTRACT_MISMATCH"
    return None


def _read_events(path, manifest_hash: str) -> List[Dict]:
    from app.paper.session.journal import read_verified_events, JournalError
    try:
        return read_verified_events(path, manifest_hash)
    except JournalError as exc:
        raise DriftClassificationError(f"journal failed verification: {exc}")


def _bar_open_times(events: List[Dict]) -> List[int]:
    return sorted(int(e["payload"]["open_time"]) for e in events if e["event_type"] == "bar_ingested")


def _fill_count(events: List[Dict]) -> int:
    n = 0
    for e in events:
        if e["event_type"] == "order":
            u = e["payload"].get("filled_units")
            if _is_real_number(u) and abs(float(u)) > 0:
                n += 1
    return n


def _live_execution_false(events: List[Dict]) -> bool:
    for e in events:
        if e["event_type"] == "session_created":
            return e["payload"].get("live_execution_enabled") is False
    return False


# --------------------------------------------------------------------------- #
# frozen-report rebuild (public production entries only)                       #
# --------------------------------------------------------------------------- #
def _rebuild_reports(journal_path, manifest, segment_policy):
    if not _is_pathlike(journal_path):
        raise DriftClassificationError(
            "journal_path must be a filesystem path; a report/event bundle cannot enter the public API")
    try:
        seg = build_segment_report_from_journal(journal_path, segment_policy, manifest)
    except SegmentPipelineError as exc:
        raise DriftClassificationError(f"segment report rebuild failed: {exc}")
    try:
        cost = build_cost_analysis_from_journal(journal_path, segment_policy, manifest)
    except CostAnalysisError as exc:
        raise DriftClassificationError(f"cost analysis rebuild failed: {exc}")
    return seg, cost


def _segment_policy_hash(seg_report: Dict) -> str:
    return seg_report.get("policy_hash", "policy_hash:None")


# --------------------------------------------------------------------------- #
# baseline / forward provenance hashes                                         #
# --------------------------------------------------------------------------- #
def _dataset_hash_token(manifest: Dict) -> str:
    dh = manifest.get("dataset_hash")
    return f"dataset_hash:{'None' if dh is None else dh}"


def _provenance_fields(manifest: Dict, seg: Dict, cost: Dict) -> Dict:
    strat = manifest.get("strategy", {}) if isinstance(manifest.get("strategy"), dict) else {}
    return {
        "strategy_hash": strat.get("strategy_hash", "strategy_hash:None"),
        "param_hash": manifest.get("param_hash", "param_hash:None"),
        "asset": manifest.get("asset", "asset:None"),
        "instrument_type": manifest.get("instrument_type", "instrument_type:None"),
        "timeframe": manifest.get("timeframe", "timeframe:None"),
        "data_source": manifest.get("data_source", "data_source:None"),
        "dataset_hash_token": _dataset_hash_token(manifest),
        "execution_profile_hash": manifest.get("execution_profile_hash",
                                               cost.get("execution_profile_hash", "exec_profile_hash:None")),
        "absolute_risk_limits_hash": manifest.get("absolute_risk_limits_hash", "risk_hash:None"),
        "segment_policy_hash": _segment_policy_hash(seg),
        "segment_policy_version": seg.get("policy_version", "segment-policy-v1"),
        "metric_registry_version": METRIC_REGISTRY_VERSION,
        "dependency_contract_hash": _dependency_contract_hash(),
        "initial_capital": float(manifest.get("initial_capital", 0.0)),
    }


def _reference_baseline_hash(manifest, seg, cost, period_start_ms, cutoff_ms) -> str:
    payload = dict(_provenance_fields(manifest, seg, cost))
    payload.update({
        "reference_source_journal_final_hash": seg.get("source_journal_final_hash", "src:None"),
        "reference_wp2b1_pipeline_hash": seg.get("pipeline_hash", "pipeline:None"),
        "reference_wp2b2_result_hash": cost.get("result_hash", "result:None"),
        "reference_wp2b2_session_result_hash": cost.get("session", {}).get("session_result_hash",
                                                                           "session_result:None"),
        "reference_period_start_ms": int(period_start_ms),
        "reference_cutoff_ms": int(cutoff_ms),
    })
    return _sha(payload)


def _forward_hash(manifest, seg, cost, period_start_ms, period_end_ms) -> str:
    payload = dict(_provenance_fields(manifest, seg, cost))
    payload.update({
        "forward_source_journal_final_hash": seg.get("source_journal_final_hash", "src:None"),
        "forward_wp2b1_pipeline_hash": seg.get("pipeline_hash", "pipeline:None"),
        "forward_wp2b2_result_hash": cost.get("result_hash", "result:None"),
        "forward_wp2b2_session_result_hash": cost.get("session", {}).get("session_result_hash",
                                                                         "session_result:None"),
        "forward_period_start_ms": int(period_start_ms),
        "forward_period_end_ms": int(period_end_ms),
    })
    return _sha(payload)


# --------------------------------------------------------------------------- #
# PUBLIC read-only candidate operations                                        #
# --------------------------------------------------------------------------- #
def build_threshold_policy_candidate_hash(threshold_policy) -> Dict:
    """Pure function of the policy: report the canonical hash a reviewer would pin as
    ``expected_threshold_policy_hash``. Performs NO approval."""
    if not isinstance(threshold_policy, dict):
        raise DriftClassificationError("threshold_policy must be a dict")
    return {
        "candidate_threshold_policy_hash": _sha(threshold_policy),
        "policy_class": threshold_policy.get("policy_class"),
        "purpose": threshold_policy.get("purpose"),
        "production_approved": threshold_policy.get("production_approved"),
    }


def build_baseline_candidate_from_journal(journal_path, manifest, segment_policy) -> Dict:
    """Read-only: verify the journal, rebuild the frozen WP2B-1/WP2B-2 reports through their public
    entries, and report a candidate baseline hash + provenance. NEVER approves it, and NEVER accepts a
    caller-supplied dependency contract (the internal WP2B3_DEPENDENCY_CONTRACT is used)."""
    seg, cost = _rebuild_reports(journal_path, manifest, segment_policy)
    events = _read_events(journal_path, _manifest_hash(manifest))
    ots = _bar_open_times(events)
    if len(seg.get("segments", [])) != 1:
        raise DriftClassificationError("baseline candidate requires exactly one whole-journal segment")
    tf_ms = int(segment_policy.get("timeframe_ms")) if isinstance(segment_policy, dict) else None
    if not ots or tf_ms is None:
        raise DriftClassificationError("baseline candidate requires a non-empty, single-timeframe journal")
    period_start_ms = ots[0]
    cutoff_ms = ots[-1] + tf_ms
    return {
        "candidate_baseline_hash": _reference_baseline_hash(manifest, seg, cost, period_start_ms,
                                                            cutoff_ms),
        "dependency_contract_hash": _dependency_contract_hash(),
        "provenance": _provenance_fields(manifest, seg, cost),
        "period_start_ms": int(period_start_ms),
        "cutoff_ms": int(cutoff_ms),
        "approved": False,   # never self-approves
        "note": "candidate only — requires independent external review before use as expected_baseline_hash",
    }


# --------------------------------------------------------------------------- #
# threshold-policy validation (TEST mode)                                      #
# --------------------------------------------------------------------------- #
_REQUIRED_METRIC_FIELDS = ("family", "verdict_driver", "comparison_method", "deterioration_direction",
                           "warn_threshold", "drift_threshold", "favourable_threshold",
                           "zero_baseline_epsilon", "absolute_fallback", "sample_unit",
                           "reference_min_sample", "forward_min_sample")


def _nonneg_finite(x) -> bool:
    """True only for a non-bool real number that is finite and >= 0 (no int()/float() coercion)."""
    return (not isinstance(x, bool)) and isinstance(x, (int, float)) and math.isfinite(float(x)) \
        and float(x) >= 0.0


def _nonneg_int(x) -> bool:
    """True only for a non-bool int that is >= 0 (floats, NaN, Inf, strings all rejected)."""
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def _thresholds_wellformed(threshold_policy: Dict) -> Optional[str]:
    """Strict, closed-registry validation of the full threshold policy. Returns THRESHOLDS_MALFORMED on
    ANY malformed field/relationship; never silently coerces strings/bools/floats."""
    # tol_recon: non-bool real, finite, >= 0
    if not _nonneg_finite(threshold_policy.get("tol_recon")):
        return "THRESHOLDS_MALFORMED"
    metrics = threshold_policy.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        return "THRESHOLDS_MALFORMED"
    # exact closed registry: the metrics map must be EXACTLY the registered verdict drivers
    if set(metrics.keys()) != set(_DRIVER_NAMES):
        return "THRESHOLDS_MALFORMED"
    for name in _DRIVER_NAMES:
        spec = metrics.get(name)
        reg = _REGISTRY_BY_NAME[name]
        if not isinstance(spec, dict):
            return "THRESHOLDS_MALFORMED"
        for f in _REQUIRED_METRIC_FIELDS:
            if f not in spec:
                return "THRESHOLDS_MALFORMED"
        # family must be a string equal to the registered family
        if not isinstance(spec["family"], str) or spec["family"] != reg["family"]:
            return "THRESHOLDS_MALFORMED"
        # verdict_driver must be EXACTLY True for every registered driver
        if spec["verdict_driver"] is not True:
            return "THRESHOLDS_MALFORMED"
        # sample_unit must equal the registry's required unit for this metric
        if spec["sample_unit"] != reg["unit"]:
            return "THRESHOLDS_MALFORMED"
        if spec["comparison_method"] not in ("relative", "absolute"):
            return "THRESHOLDS_MALFORMED"
        if spec["deterioration_direction"] not in ("lower", "higher"):
            return "THRESHOLDS_MALFORMED"
        # thresholds + epsilon: non-bool real, finite, >= 0
        for k in ("warn_threshold", "drift_threshold", "favourable_threshold", "zero_baseline_epsilon"):
            if not _nonneg_finite(spec[k]):
                return "THRESHOLDS_MALFORMED"
        if float(spec["warn_threshold"]) > float(spec["drift_threshold"]):
            return "THRESHOLDS_MALFORMED"
        # sample minimums: non-bool ints, >= 0
        if not _nonneg_int(spec["reference_min_sample"]) or not _nonneg_int(spec["forward_min_sample"]):
            return "THRESHOLDS_MALFORMED"
        # absolute_fallback: dict with non-bool real, finite, >= 0 warn/drift/favourable; warn <= drift
        af = spec["absolute_fallback"]
        if not isinstance(af, dict):
            return "THRESHOLDS_MALFORMED"
        for k in ("warn", "drift", "favourable"):
            if k not in af or not _nonneg_finite(af[k]):
                return "THRESHOLDS_MALFORMED"
        if float(af["warn"]) > float(af["drift"]):
            return "THRESHOLDS_MALFORMED"
    return None


def _validate_test_policy(threshold_policy: Dict, expected_threshold_policy_hash) -> Optional[str]:
    """Returns a reason_code if the TEST policy is not acceptable, else None."""
    if threshold_policy.get("purpose") == "PRODUCTION":
        return "PRODUCTION_POLICY_IN_TEST"
    if (threshold_policy.get("policy_class") != "synthetic_test_policy"
            or threshold_policy.get("purpose") != "TEST_ONLY"
            or threshold_policy.get("production_approved") is not False):
        return "TEST_POLICY_MALFORMED"
    malformed = _thresholds_wellformed(threshold_policy)
    if malformed:
        return malformed
    if threshold_policy.get("dependency_contract_hash") != _dependency_contract_hash():
        return "POLICY_DEPENDENCY_MISMATCH"
    if not (isinstance(expected_threshold_policy_hash, str) and expected_threshold_policy_hash):
        return "THRESHOLD_POLICY_NOT_APPROVED"
    if _sha(threshold_policy) != expected_threshold_policy_hash:
        return "THRESHOLD_POLICY_NOT_APPROVED"
    return None


# --------------------------------------------------------------------------- #
# metric extraction + per-metric classification                                #
# --------------------------------------------------------------------------- #
def _extract_cell(source, seg: Dict, cost: Dict, initial_capital: float) -> Tuple[str, Optional[float]]:
    kind, field = source
    if kind == "seg":
        cell = seg["segments"][0]["metrics"].get(field, {"status": NA, "value": None})
        return cell.get("status", NA), cell.get("value")
    if kind == "cost_seg":
        cell = cost["segments"][0].get(field, {"status": NA, "value": None})
        return cell.get("status", NA), cell.get("value")
    if kind == "cost_ratio":
        sess = cost["session"]
        tsc = sess.get("total_session_fill_cost", {})
        if tsc.get("status") == OK and sess.get("fill_coverage_proven") is True and initial_capital > 0:
            tfc = tsc.get("total_friction_cost")
            if _finite(tfc):
                return OK, float(tfc) / float(initial_capital)
        return NA, None
    return NA, None


def _cell_reason(cell) -> Optional[str]:
    """Validate one frozen source-cell shape. Returns METRIC_INVALID for any unknown/missing/inconsistent
    state; None if the cell is a valid OK+finite-numeric or NOT_AVAILABLE+None. Never coerces silently."""
    if not isinstance(cell, dict) or "status" not in cell:
        return "METRIC_INVALID"
    st = cell["status"]
    if st not in (OK, NA, ST_INVALID) or st == ST_INVALID:
        return "METRIC_INVALID"
    if st == OK:
        if "value" not in cell:
            return "METRIC_INVALID"
        v = cell["value"]
        if isinstance(v, bool) or not _finite(v):
            return "METRIC_INVALID"
    else:  # NOT_AVAILABLE must carry a null value
        if cell.get("value") is not None:
            return "METRIC_INVALID"
    return None


def _derived_total_friction_reason(session: Dict) -> Optional[str]:
    """Validate the COMPLETE derived total-friction source contract (session level), for every status.
    Returns METRIC_INVALID on any malformed/unknown/inconsistent shape, else None. Never coerces."""
    tsc = session.get("total_session_fill_cost")
    if not isinstance(tsc, dict) or "status" not in tsc:
        return "METRIC_INVALID"
    st = tsc["status"]
    if st not in (OK, NA, ST_INVALID) or st == ST_INVALID:
        return "METRIC_INVALID"
    fcp = session.get("fill_coverage_proven", "__MISSING__")
    if st == OK:
        tfc = tsc.get("total_friction_cost", "__MISSING__")
        if tfc == "__MISSING__" or isinstance(tfc, bool) or not _finite(tfc) or float(tfc) < 0.0:
            return "METRIC_INVALID"
        if fcp is not True:                       # OK requires coverage proven EXACTLY True
            return "METRIC_INVALID"
    else:  # NOT_AVAILABLE
        if "total_friction_cost" not in tsc or tsc["total_friction_cost"] is not None:
            return "METRIC_INVALID"               # must be present and exactly None
        if not isinstance(fcp, bool) or fcp is not False:  # bool, and must NOT imply proven coverage
            return "METRIC_INVALID"
    return None


def _validate_source_cells(ref_seg: Dict, ref_cost: Dict, fwd_seg: Dict, fwd_cost: Dict) -> Optional[str]:
    """Fail closed on ANY malformed/unknown/internally-inconsistent consumed source cell (both sides).
    Returns METRIC_INVALID on the first offending cell, else None."""
    for seg, cost in ((ref_seg, ref_cost), (fwd_seg, fwd_cost)):
        smetrics = seg["segments"][0]["metrics"]
        cseg = cost["segments"][0]
        for name, _family, source, _drv, _unit in _REGISTRY:
            kind, field = source
            if kind == "seg":
                r = _cell_reason(smetrics.get(field))
                if r:
                    return r
            elif kind == "cost_seg":
                r = _cell_reason(cseg.get(field))
                if r:
                    return r
            elif kind == "cost_ratio":
                r = _derived_total_friction_reason(cost["session"])
                if r:
                    return r
    return None


def _signed_deviations(direction: str, ref: float, fwd: float) -> Tuple[float, float]:
    if direction == "lower":       # lower is worse
        return ref - fwd, fwd - ref
    return fwd - ref, ref - fwd     # higher is worse


def _classify_driver(spec: Dict, ref: float, fwd: float) -> Tuple[str, str, float, float]:
    direction = spec["deterioration_direction"]
    method = spec["comparison_method"]
    eps = float(spec["zero_baseline_epsilon"])
    adv_raw, fav_raw = _signed_deviations(direction, ref, fwd)
    use_rel = (method == "relative") and abs(ref) > eps and ref > 0
    if use_rel:
        adv, fav = adv_raw / abs(ref), fav_raw / abs(ref)
        warn, drift, favth = (float(spec["warn_threshold"]), float(spec["drift_threshold"]),
                              float(spec["favourable_threshold"]))
        guard = False
    else:
        af = spec["absolute_fallback"]
        adv, fav = adv_raw, fav_raw
        warn, drift, favth = float(af["warn"]), float(af["drift"]), float(af["favourable"])
        guard = (method == "relative")   # relative form requested but base guard forced fallback
    if adv > drift:
        return DRIFT, "DRIFT_BAND", adv, fav
    if adv > warn:
        return WARNING, "WARN_BAND", adv, fav
    if fav > favth:
        return WARNING, "UNEXPECTED_FAVOURABLE_SHIFT", adv, fav
    return WITHIN_THRESHOLD, ("ZERO_OR_NEGATIVE_BASELINE_GUARD" if guard else "WITHIN_BAND"), adv, fav


def _sample_count(unit: str, bars: int, cycles: int, fills: int) -> int:
    return {"bars": bars, "completed_cycles": cycles, "fills": fills}.get(unit, 0)


# --------------------------------------------------------------------------- #
# report assembly helpers                                                      #
# --------------------------------------------------------------------------- #
def _invalid_report(reason_code: str, classification_mode: str, policy: Dict, *, extra=None) -> Dict:
    rep = {
        "method": METHOD, "version": VERSION,
        "classification_mode": classification_mode,
        "policy_class": (policy or {}).get("policy_class"),
        "policy_purpose": (policy or {}).get("purpose"),
        "production_approved": bool((policy or {}).get("production_approved", False)),
        "validity_status": INVALID,
        "evidence_status": INSUFFICIENT,
        "drift_status": NOT_EVALUATED,
        "action_status": ACT_INVALID,
        "reason_code": reason_code,
        "release_eligible": False,
        "live_execution_eligible": False,
        "metric_results": [],
        "coverage": {},
        "limitations": list(_LIMITATIONS),
        "dependency_contract_hash": _dependency_contract_hash(),
    }
    if extra:
        rep.update(extra)
    rep["economic_content_hash"] = _sha({"validity_status": INVALID, "reason_code": reason_code})
    rep["report_hash"] = _sha({k: rep[k] for k in sorted(rep) if k != "report_hash"})
    return rep


# --------------------------------------------------------------------------- #
# classification CORE (post-verification). PUBLIC entry rebuilds then calls    #
# this; a private test-only wrapper may pass crafted reports.                  #
# --------------------------------------------------------------------------- #
def _classify_core(ref_events, fwd_events, ref_seg, ref_cost, fwd_seg, fwd_cost,
                   ref_manifest, fwd_manifest, ref_policy, fwd_policy,
                   threshold_policy, expected_baseline_hash, expected_threshold_policy_hash,
                   ref_path_str, fwd_path_str) -> Dict:

    def _inv(reason):
        return _invalid_report(reason, "TEST", threshold_policy)

    recomputed_threshold_policy_hash = _sha(threshold_policy)

    if not (_live_execution_false(ref_events) and _live_execution_false(fwd_events)):
        return _inv("LIVE_EXECUTION_ENABLED")

    for rep in (ref_seg, fwd_seg):
        if len(rep.get("segments", [])) != 1:
            return _inv("MULTI_SEGMENT_UNSUPPORTED")
        if rep["segments"][0].get("status") != "OK":
            return _inv("SEGMENT_NOT_OK")

    ref_tf = ref_policy.get("timeframe_ms")
    fwd_tf = fwd_policy.get("timeframe_ms")
    if ref_tf != fwd_tf or ref_manifest.get("timeframe") != fwd_manifest.get("timeframe"):
        return _inv("OBSERVATION_HORIZON_MISMATCH")
    tf_ms = int(ref_tf)
    ref_ots = _bar_open_times(ref_events)
    fwd_ots = _bar_open_times(fwd_events)
    if not ref_ots or not fwd_ots:
        return _inv("OBSERVATION_HORIZON_MISMATCH")
    for ots in (ref_ots, fwd_ots):
        for a, b in zip(ots, ots[1:]):
            if b - a != tf_ms:
                return _inv("OBSERVATION_HORIZON_MISMATCH")
    if len(ref_ots) != len(fwd_ots):
        return _inv("OBSERVATION_HORIZON_MISMATCH")
    ref_start, ref_cutoff = ref_ots[0], ref_ots[-1] + tf_ms
    fwd_start, fwd_end = fwd_ots[0], fwd_ots[-1] + tf_ms
    if (ref_cutoff - ref_start) != (fwd_end - fwd_start):
        return _inv("OBSERVATION_HORIZON_MISMATCH")
    for policy, s, e in ((ref_policy, ref_start, ref_cutoff), (fwd_policy, fwd_start, fwd_end)):
        segs = policy.get("segments", [])
        if len(segs) != 1 or int(segs[0]["start_ms"]) != s or int(segs[0]["end_ms"]) != e:
            return _inv("OBSERVATION_HORIZON_MISMATCH")

    ref_final = ref_seg.get("source_journal_final_hash")
    fwd_final = fwd_seg.get("source_journal_final_hash")
    if ref_final == fwd_final or ref_path_str == fwd_path_str:
        return _inv("JOURNAL_IDENTITY_COLLISION")
    if fwd_start < ref_cutoff:
        return _inv("FORWARD_BEFORE_REFERENCE")

    ref_prov = _provenance_fields(ref_manifest, ref_seg, ref_cost)
    fwd_prov = _provenance_fields(fwd_manifest, fwd_seg, fwd_cost)
    _bind_keys = ("strategy_hash", "param_hash", "asset", "instrument_type", "timeframe", "data_source",
                  "execution_profile_hash", "absolute_risk_limits_hash", "segment_policy_version",
                  "metric_registry_version", "dependency_contract_hash", "initial_capital")
    if any(ref_prov[k] != fwd_prov[k] for k in _bind_keys):
        return _inv("BINDING_MISMATCH")

    E0 = float(ref_manifest.get("initial_capital", 0.0))

    recomputed_reference_baseline_hash = _reference_baseline_hash(ref_manifest, ref_seg, ref_cost,
                                                                  ref_start, ref_cutoff)
    if not (isinstance(expected_baseline_hash, str) and expected_baseline_hash):
        return _inv("BASELINE_NOT_APPROVED")
    if recomputed_reference_baseline_hash != expected_baseline_hash:
        return _inv("BASELINE_NOT_APPROVED")
    forward_hash = _forward_hash(fwd_manifest, fwd_seg, fwd_cost, fwd_start, fwd_end)

    # fail closed on any malformed / unknown / internally-inconsistent source cell (never coerced to
    # missing evidence): whole report becomes INVALID / METRIC_INVALID.
    cell_reason = _validate_source_cells(ref_seg, ref_cost, fwd_seg, fwd_cost)
    if cell_reason:
        return _inv(cell_reason)

    tol = float(threshold_policy.get("tol_recon", 1e-9))
    for seg, cost in ((ref_seg, ref_cost), (fwd_seg, fwd_cost)):
        nr = seg["segments"][0]["metrics"].get("net_return", {})
        enr = cost["segments"][0].get("equity_net_return", {})
        if nr.get("status") == OK and enr.get("status") == OK:
            if not _finite(nr.get("value")) or not _finite(enr.get("value")):
                return _inv("NON_FINITE_METRIC")
            if abs(float(nr["value"]) - float(enr["value"])) > tol:
                return _inv("NET_RETURN_RECONCILE_MISMATCH")

    bars_ref, bars_fwd = len(ref_ots), len(fwd_ots)
    fills_ref, fills_fwd = _fill_count(ref_events), _fill_count(fwd_events)
    cyc_ref = int(ref_cost["segments"][0]["cycle_coverage"]["total_completed"])
    cyc_fwd = int(fwd_cost["segments"][0]["cycle_coverage"]["total_completed"])

    metrics_spec = threshold_policy["metrics"]
    metric_results: List[Dict] = []
    for name, family, source, is_driver, unit in _REGISTRY:
        rstat, rval = _extract_cell(source, ref_seg, ref_cost, E0)
        fstat, fval = _extract_cell(source, fwd_seg, fwd_cost, E0)
        for stat, val in ((rstat, rval), (fstat, fval)):
            if stat == ST_INVALID:
                return _inv("METRIC_INVALID")
            if stat == OK and (isinstance(val, bool) or not _finite(val)):
                return _inv("NON_FINITE_METRIC")
        entry = {
            "metric_name": name, "family": family, "verdict_driver": is_driver,
            "reference_status": rstat, "forward_status": fstat,
            "reference_value": (float(rval) if rstat == OK else None),
            "forward_value": (float(fval) if fstat == OK else None),
            "sample_unit": unit,
            "reference_sample": _sample_count(unit, bars_ref, cyc_ref, fills_ref),
            "forward_sample": _sample_count(unit, bars_fwd, cyc_fwd, fills_fwd),
            "adverse_deviation": None, "favourable_deviation": None,
            "comparison_method": None, "reason_code": None, "drift_status": NOT_EVALUATED,
            "sample_gate_met": None,
        }
        if not is_driver:
            entry["reason_code"] = "CONTEXT_METRIC"
            metric_results.append(entry)
            continue
        spec = metrics_spec[name]
        entry["comparison_method"] = spec["comparison_method"]
        gate = (entry["reference_sample"] >= int(spec["reference_min_sample"])
                and entry["forward_sample"] >= int(spec["forward_min_sample"]))
        entry["sample_gate_met"] = gate
        if rstat != OK or fstat != OK:
            entry["drift_status"] = NOT_EVALUATED
            entry["reason_code"] = "SOURCE_CELL_NOT_AVAILABLE"
        elif not gate:
            entry["drift_status"] = NOT_EVALUATED
            entry["reason_code"] = "MIN_SAMPLE_NOT_MET"
        else:
            ds, rc, adv, fav = _classify_driver(spec, float(rval), float(fval))
            entry["drift_status"] = ds
            entry["reason_code"] = rc
            entry["adverse_deviation"] = adv
            entry["favourable_deviation"] = fav
        metric_results.append(entry)

    driver_statuses = [e["drift_status"] for e in metric_results if e["verdict_driver"]]
    n_eval = sum(1 for s in driver_statuses if s != NOT_EVALUATED)
    n_not = sum(1 for s in driver_statuses if s == NOT_EVALUATED)
    if n_eval == len(driver_statuses):
        evidence_status = SUFFICIENT
    elif n_eval == 0:
        evidence_status = INSUFFICIENT
    else:
        evidence_status = PARTIAL

    if any(s == DRIFT for s in driver_statuses):
        action_status, agg_drift = ACT_DRIFT, DRIFT
    elif n_not > 0:
        action_status = ACT_NEEDS_MORE_DATA
        agg_drift = WARNING if any(s == WARNING for s in driver_statuses) else NOT_EVALUATED
    elif any(s == WARNING for s in driver_statuses):
        action_status, agg_drift = ACT_WARNING, WARNING
    else:
        action_status, agg_drift = ACT_WITHIN, WITHIN_THRESHOLD

    coverage = {"bars_ref": bars_ref, "bars_fwd": bars_fwd, "fills_ref": fills_ref,
                "fills_fwd": fills_fwd, "completed_cycles_ref": cyc_ref, "completed_cycles_fwd": cyc_fwd}

    econ = {
        "metric_results": [{k: e[k] for k in ("metric_name", "reference_value", "forward_value",
                                              "adverse_deviation", "favourable_deviation", "drift_status",
                                              "reason_code")} for e in metric_results],
        "coverage": coverage,
        "aggregate_drift_status": agg_drift,
    }
    economic_content_hash = _sha(econ)

    report = {
        "method": METHOD, "version": VERSION,
        "classification_mode": "TEST",
        "policy_class": threshold_policy.get("policy_class"),
        "policy_purpose": threshold_policy.get("purpose"),
        "production_approved": bool(threshold_policy.get("production_approved", False)),
        "validity_status": VALID,
        "evidence_status": evidence_status,
        "drift_status": agg_drift,
        "action_status": action_status,
        "reason_code": None,
        "release_eligible": False,
        "live_execution_eligible": False,
        "metric_results": metric_results,
        "coverage": coverage,
        "expected_baseline_hash": expected_baseline_hash,
        "recomputed_reference_baseline_hash": recomputed_reference_baseline_hash,
        "forward_hash": forward_hash,
        "expected_threshold_policy_hash": expected_threshold_policy_hash,
        "recomputed_threshold_policy_hash": recomputed_threshold_policy_hash,
        "dependency_contract_hash": _dependency_contract_hash(),
        "reference_period_start_ms": ref_start, "reference_cutoff_ms": ref_cutoff,
        "forward_period_start_ms": fwd_start, "forward_period_end_ms": fwd_end,
        "reference_source_journal_final_hash": ref_final,
        "forward_source_journal_final_hash": fwd_final,
        "limitations": list(_LIMITATIONS),
        "economic_content_hash": economic_content_hash,
    }
    report["report_hash"] = _sha({
        "economic_content_hash": economic_content_hash,
        "expected_baseline_hash": expected_baseline_hash,
        "recomputed_reference_baseline_hash": recomputed_reference_baseline_hash,
        "forward_hash": forward_hash,
        "expected_threshold_policy_hash": expected_threshold_policy_hash,
        "recomputed_threshold_policy_hash": recomputed_threshold_policy_hash,
        "dependency_contract_hash": _dependency_contract_hash(),
        "classification_mode": "TEST",
        "policy_class": threshold_policy.get("policy_class"),
        "policy_purpose": threshold_policy.get("purpose"),
        "method": METHOD, "version": VERSION,
        "reference_period_start_ms": ref_start, "reference_cutoff_ms": ref_cutoff,
        "forward_period_start_ms": fwd_start, "forward_period_end_ms": fwd_end,
        "reference_source_journal_final_hash": ref_final,
        "forward_source_journal_final_hash": fwd_final,
    })
    return report


def _classify_from_reports(ref_events, fwd_events, ref_seg, ref_cost, fwd_seg, fwd_cost,
                           ref_manifest, fwd_manifest, ref_policy, fwd_policy,
                           threshold_policy, expected_baseline_hash, expected_threshold_policy_hash,
                           ref_path_str="ref://reports", fwd_path_str="fwd://reports") -> Dict:
    """PRIVATE, TEST-ONLY. Runs the classification core over already-built (or crafted) reports without a
    journal file. Not part of the public API — production callers must use classify_drift_from_journals."""
    return _classify_core(ref_events, fwd_events, ref_seg, ref_cost, fwd_seg, fwd_cost,
                          ref_manifest, fwd_manifest, ref_policy, fwd_policy,
                          threshold_policy, expected_baseline_hash, expected_threshold_policy_hash,
                          ref_path_str, fwd_path_str)


# --------------------------------------------------------------------------- #
# PUBLIC classification entry                                                  #
# --------------------------------------------------------------------------- #
def classify_drift_from_journals(reference_journal_path, forward_journal_path,
                                 reference_manifest, forward_manifest,
                                 reference_segment_policy, forward_segment_policy,
                                 threshold_policy, expected_baseline_hash,
                                 expected_threshold_policy_hash, classification_mode) -> Dict:
    """Public production entry. Accepts journal PATHS, manifests and policies only — never caller-supplied
    reports or event lists. TEST mode performs the full point-threshold drift diagnostic; PRODUCTION mode
    is mechanically disabled in v1 (empty approval allowlist)."""
    for p in (reference_journal_path, forward_journal_path):
        if not _is_pathlike(p):
            raise DriftClassificationError(
                "journal paths must be filesystem paths; report/event bundles are rejected")
    if not isinstance(threshold_policy, dict):
        raise DriftClassificationError("threshold_policy must be a dict")
    if classification_mode not in ("TEST", "PRODUCTION"):
        raise DriftClassificationError("classification_mode must be 'TEST' or 'PRODUCTION'")

    recomputed_threshold_policy_hash = _sha(threshold_policy)

    # --- PRODUCTION hard block (empty allowlist) — no self-approval possible ---------------------- #
    if classification_mode == "PRODUCTION":
        if recomputed_threshold_policy_hash not in APPROVED_PRODUCTION_THRESHOLD_POLICY_HASHES:
            return _invalid_report("PRODUCTION_POLICY_NOT_CONFIGURED", "PRODUCTION", threshold_policy,
                                   extra={"expected_threshold_policy_hash": expected_threshold_policy_hash,
                                          "recomputed_threshold_policy_hash": recomputed_threshold_policy_hash})
        return _invalid_report("PRODUCTION_POLICY_NOT_CONFIGURED", "PRODUCTION", threshold_policy)

    # =========================== TEST MODE ======================================================== #
    dep_reason = _verify_dependency_contract()
    if dep_reason:
        return _invalid_report(dep_reason, "TEST", threshold_policy)

    policy_reason = _validate_test_policy(threshold_policy, expected_threshold_policy_hash)
    if policy_reason:
        extra = None
        if policy_reason == "THRESHOLD_POLICY_NOT_APPROVED":
            extra = {"expected_threshold_policy_hash": expected_threshold_policy_hash,
                     "recomputed_threshold_policy_hash": recomputed_threshold_policy_hash}
        return _invalid_report(policy_reason, "TEST", threshold_policy, extra=extra)

    ref_events = _read_events(reference_journal_path, _manifest_hash(reference_manifest))
    fwd_events = _read_events(forward_journal_path, _manifest_hash(forward_manifest))
    ref_seg, ref_cost = _rebuild_reports(reference_journal_path, reference_manifest,
                                         reference_segment_policy)
    fwd_seg, fwd_cost = _rebuild_reports(forward_journal_path, forward_manifest, forward_segment_policy)

    return _classify_core(ref_events, fwd_events, ref_seg, ref_cost, fwd_seg, fwd_cost,
                          reference_manifest, forward_manifest, reference_segment_policy,
                          forward_segment_policy, threshold_policy, expected_baseline_hash,
                          expected_threshold_policy_hash,
                          str(reference_journal_path), str(forward_journal_path))
