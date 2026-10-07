"""Behavioural-consistency classification for shadow-forward (evidence only, no side effects).

Given (a) observed shadow behaviour and (b) a FROZEN reference-range object, this module
classifies whether the observed behaviour is consistent with the pre-registered expectation.
It is a pure evaluator: it never changes any lifecycle state, never approves a transition,
never starts a test, and never writes to any production or evidence directory. Its only
output is a structured verdict, and it mutates none of its inputs.

Outcomes and their precedence. This is an HONEST two-phase contract that matches the
implementation — it does NOT claim INVALIDATED precedes a structural INVALID:

  Phase 1 — structural validity gate. Malformed inputs are INVALID *before* integrity can even
  be evaluated (you cannot trust a hash recomputed over a malformed object). This covers: a
  malformed policy/observed/reference, a structurally invalid embedded range policy, an
  observed ``metrics`` container that is not a dict, a malformed observed required-metric
  datatype, malformed comparability metadata, malformed expected-hash types, a malformed
  absolute-risk record, or an off-schedule checkpoint.

  Phase 2 — for STRUCTURALLY VALID inputs, the remaining order is:
    INVALIDATED  the frozen comparison basis is untrusted — the stored range content hash no
                 longer reproduces, the embedded policy no longer reproduces its policy_hash,
                 an expected hash does not match, or the observed comparability basis differs
                 from the reference basis. Checked before any statistical verdict.
    FAILED       an absolute, frozen risk limit was breached. Safety stops win immediately and
                 may fire between checkpoints.
    NOT_AVAILABLE required evidence is missing/non-finite: absent absolute-risk record, absent
                 counts, missing required integrity hashes, missing observed comparability
                 metadata, an incomplete set of required metric ranges, or a missing/non-finite
                 observed required metric. Never silently CONSISTENT.
    EXTEND       optional-stopping gate: continuous monitoring, or a formal checkpoint whose
                 exact trade count has not been reached. No CONSISTENT/DRIFT drawn early.
    DRIFT        (formal, exact checkpoint, complete evidence) a single metric is persistently
                 outside its range, or several metrics are coherently outside at once.
    CONSISTENT   (formal, exact checkpoint, complete evidence) every required metric sits
                 inside the frozen distribution and no absolute-risk breach. Negative PnL can
                 still be CONSISTENT; a good PnL never hides drift.

Optional-stopping protection: a formal CONSISTENT/DRIFT verdict is allowed ONLY from an
immutable checkpoint snapshot whose ``trade_count`` exactly equals the frozen checkpoint. A
snapshot past the checkpoint (trade_count > checkpoint) does not authorise a new verdict for
that checkpoint — this prevents repeated/delayed checkpoint selection from becoming optional
stopping. Continuous monitoring (checkpoint=None) may return FAILED/INVALID/INVALIDATED (and
NOT_AVAILABLE/EXTEND) at any time but never CONSISTENT or DRIFT.

Research and simulation only — no broker, no real orders, no private keys.
"""
from __future__ import annotations

import math
import numbers
from typing import Dict, List, Optional, Tuple

from app.forward.manifest import canonical_json, sha256_text
from app.forward.shadow.dataquality import INVALID, NOT_AVAILABLE, PASS
from app.forward.shadow.ranges import (
    COMPARABILITY_BASIS_KEYS,
    range_policy_hash,
    validate_range_policy,
)

CONSISTENCY_METHOD = "frozen-range-consistency"
CONSISTENCY_VERSION = "consistency-v2"

CONSISTENT = "CONSISTENT"
DRIFT = "DRIFT"
FAILED = "FAILED"
EXTEND = "EXTEND"
INVALIDATED = "INVALIDATED"
# NOT_AVAILABLE and INVALID are imported (shared vocabulary with data-quality)

# Honest, implementation-matching order: a structural INVALID is decided in phase 1, BEFORE
# integrity (INVALIDATED) can be trusted; among structurally-valid inputs INVALIDATED then
# precedes FAILED and any statistical verdict.
PRECEDENCE_ORDER = (INVALID, INVALIDATED, FAILED, NOT_AVAILABLE, EXTEND, DRIFT, CONSISTENT)

_ALL_GROUP = "__ALL__"

_NOTE = ("Evidence only. This evaluation changed NOTHING: it did not alter any lifecycle "
         "state, approve any transition, start any test, or write to any production or "
         "evidence directory, and it mutated none of its inputs. It only classifies observed "
         "behaviour against a frozen range.")


def freeze_consistency_policy(
    *,
    checkpoints: Tuple[int, ...] = (30, 60, 90),
    min_observations: int = 20,
    min_trades: int = 30,
    persistence_min_periods: int = 2,
    multi_metric_min: int = 2,
) -> Dict:
    """Freeze the consistency / optional-stopping policy. The checkpoint schedule is part of
    the frozen config and therefore part of the hash."""
    cps = list(checkpoints)
    if not cps:
        raise ValueError("at least one checkpoint is required")
    for c in cps:
        if not (isinstance(c, int) and not isinstance(c, bool) and c > 0):
            raise ValueError("checkpoints must be positive ints")
    if list(cps) != sorted(set(cps)):
        raise ValueError("checkpoints must be strictly increasing and unique")
    for name, v in (("min_observations", min_observations), ("min_trades", min_trades),
                    ("persistence_min_periods", persistence_min_periods),
                    ("multi_metric_min", multi_metric_min)):
        if not (isinstance(v, int) and not isinstance(v, bool) and v >= 1):
            raise ValueError(f"{name} must be an int >= 1")
    return {
        "method": CONSISTENCY_METHOD,
        "version": CONSISTENCY_VERSION,
        "checkpoints": cps,
        "min_observations": int(min_observations),
        "min_trades": int(min_trades),
        "persistence_min_periods": int(persistence_min_periods),
        "multi_metric_min": int(multi_metric_min),
        "precedence_order": list(PRECEDENCE_ORDER),
        "optional_stopping": ("formal CONSISTENT/DRIFT only from an immutable checkpoint "
                              "snapshot whose trade_count exactly equals a frozen checkpoint; "
                              "safety stops (FAILED/INVALID/INVALIDATED) may fire continuously"),
    }


DEFAULT_CONSISTENCY_POLICY: Dict = freeze_consistency_policy()


def consistency_policy_hash(policy: Dict) -> str:
    """Deterministic hash of the frozen consistency policy (schedule + thresholds)."""
    return sha256_text(canonical_json(policy))


def validate_consistency_policy(policy) -> Tuple[bool, str]:
    if not isinstance(policy, dict):
        return False, "policy is not a dict"
    if policy.get("method") != CONSISTENCY_METHOD or policy.get("version") != CONSISTENCY_VERSION:
        return False, "unsupported consistency method/version"
    cps = policy.get("checkpoints")
    if not (isinstance(cps, list) and cps and cps == sorted(set(cps))
            and all(isinstance(c, int) and not isinstance(c, bool) and c > 0 for c in cps)):
        return False, "checkpoints must be a strictly increasing list of positive ints"
    for k in ("min_observations", "min_trades", "persistence_min_periods", "multi_metric_min"):
        v = policy.get(k)
        if not (isinstance(v, int) and not isinstance(v, bool) and v >= 1):
            return False, f"{k} must be an int >= 1"
    return True, ""


# --------------------------------------------------------------------------- #
# numeric helpers                                                             #
# --------------------------------------------------------------------------- #
def _is_bool(v) -> bool:
    return isinstance(v, bool)


def _is_real(v) -> bool:
    return (not _is_bool(v)) and isinstance(v, numbers.Real)


def _is_finite(v) -> bool:
    return _is_real(v) and math.isfinite(float(v))


def _nonneg_int(v) -> bool:
    # accepts python int and numpy integer, rejects bool
    return isinstance(v, numbers.Integral) and not isinstance(v, bool) and int(v) >= 0


def _is_hash(v) -> bool:
    return isinstance(v, str) and len(v) > 0


def _observed_value(cell):
    return cell.get("observed") if isinstance(cell, dict) else cell


def _value_kind(v) -> str:
    """Classify an observed metric value:
      'missing'   -> absent / None                          (-> NOT_AVAILABLE)
      'nonfinite' -> a real number that is NaN / +/-inf      (-> NOT_AVAILABLE)
      'malformed' -> wrong datatype: str/bool/list/dict/... (-> INVALID)
      'ok'        -> a finite python/numpy int or float scalar
    """
    if v is None:
        return "missing"
    if isinstance(v, bool):  # bool is a wrong datatype for a metric value
        return "malformed"
    if isinstance(v, numbers.Real):  # int/float and numpy integer/floating (np.bool_ excluded)
        return "ok" if math.isfinite(float(v)) else "nonfinite"
    return "malformed"


def _comparability_status(observed: Dict, reference: Dict) -> str:
    """Compare the observed calculation basis with the reference's frozen basis.

    Returns one of:
      'ref_missing' -> the reference has no concrete comparability_basis (malformed reference)
      'missing'     -> the observation did not declare its comparability basis
      'malformed'   -> the declared basis is the wrong shape / types
      'mismatch'    -> a well-formed basis that differs from the reference basis
      'ok'          -> an exact field-for-field match
    Declaring the basis is mandatory: copying the reference's expected hash is NOT enough.
    """
    ref_basis = reference.get("comparability_basis")
    if not isinstance(ref_basis, dict):
        return "ref_missing"
    oc = observed.get("comparability")
    if oc is None:
        return "missing"
    if not isinstance(oc, dict):
        return "malformed"
    for k in COMPARABILITY_BASIS_KEYS:
        if k not in oc:
            return "malformed"
        v = oc[k]
        if k == "grouping_basis":
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                return "malformed"
        elif not (isinstance(v, str) and v):
            return "malformed"
    observed_basis = {k: oc[k] for k in COMPARABILITY_BASIS_KEYS}
    frozen_basis = {k: ref_basis.get(k) for k in COMPARABILITY_BASIS_KEYS}
    return "ok" if observed_basis == frozen_basis else "mismatch"


# --------------------------------------------------------------------------- #
# result builder                                                              #
# --------------------------------------------------------------------------- #
def _result(classification: str, *, policy, mode: str, checkpoint, observed, reference,
            regime_policy_hash: Optional[str], metric_findings: Optional[Dict] = None,
            reached=None, next_cp=None, required_metrics=None, available_required=None,
            unavailable_required=None, all_available: Optional[bool] = None,
            reason: str = "", limitations: Optional[List[str]] = None,
            explanation: str = "") -> Dict:
    metric_findings = metric_findings or {}
    drifted = sorted(k for k, f in metric_findings.items()
                     if f.get("status") == "PERSISTENT_DEVIATION"
                     or (f.get("status") == "DEVIATION" and classification == DRIFT))
    failed_metrics = sorted(k for k, f in metric_findings.items() if f.get("status") == "RISK_BREACH")
    counts_as_evidence = classification in (CONSISTENT, DRIFT, FAILED)

    ref_range_hash = reference.get("range_hash") if isinstance(reference, dict) else None
    ref_policy_hash = reference.get("policy_hash") if isinstance(reference, dict) else None
    try:
        cpol_hash = consistency_policy_hash(policy) if isinstance(policy, dict) and policy.get("method") else None
    except Exception:
        cpol_hash = None

    core = {
        "kind": "behavioral_consistency",
        "classification": classification,
        "mode": mode,
        "checkpoint": checkpoint,
        "reached_checkpoint": reached,
        "next_checkpoint": next_cp,
        "counts_as_evidence": counts_as_evidence,
        "required_metrics": sorted(required_metrics or []),
        "available_required_metrics": sorted(available_required or []),
        "unavailable_required_metrics": sorted(unavailable_required or []),
        "all_required_checks_available": bool(all_available) if all_available is not None else None,
        "trade_count": observed.get("trade_count") if isinstance(observed, dict) else None,
        "observation_count": observed.get("observation_count") if isinstance(observed, dict) else None,
        "metric_findings": metric_findings,
        "failed_metrics": failed_metrics,
        "drifted_metrics": drifted,
        "consistency_policy_hash": cpol_hash,
        "range_hash": ref_range_hash,
        "range_policy_hash": ref_policy_hash,
        "regime_policy_hash": regime_policy_hash,
        "precedence_order": list(PRECEDENCE_ORDER),
        "reason": reason,
        "limitations": limitations or [],
        "explanation": explanation or reason,
        "note": _NOTE,
    }
    core["evidence_hash"] = sha256_text(canonical_json(core))
    return core


def _recompute_range_hash(reference: Dict) -> Optional[str]:
    """Recompute the reference content hash exactly as build_reference_ranges did — over every
    field except range_hash itself — without mutating the reference."""
    try:
        without = {k: v for k, v in reference.items() if k != "range_hash"}
        return sha256_text(canonical_json(without))
    except Exception:
        return None


def _group_metrics(reference: Dict, group_key: str) -> Optional[Dict]:
    groups = reference.get("groups")
    if not isinstance(groups, dict):
        return None
    g = groups.get(group_key)
    if g is None and group_key == _ALL_GROUP and len(groups) == 1:
        g = next(iter(groups.values()))
    if not isinstance(g, dict):
        return None
    m = g.get("metrics")
    return m if isinstance(m, dict) else None


# --------------------------------------------------------------------------- #
# public entry point                                                          #
# --------------------------------------------------------------------------- #
def evaluate_consistency(
    observed,
    reference,
    *,
    policy: Dict = DEFAULT_CONSISTENCY_POLICY,
    checkpoint: Optional[int] = None,
    regime_policy_hash: Optional[str] = None,
    require_regime: bool = False,
    group_key: str = _ALL_GROUP,
) -> Dict:
    """Classify behavioural consistency. Pure, deterministic, fail-closed, side-effect-free."""
    try:
        return _evaluate(observed, reference, policy=policy, checkpoint=checkpoint,
                         regime_policy_hash=regime_policy_hash, require_regime=require_regime,
                         group_key=group_key)
    except Exception as exc:  # defensive: never raise
        return _result(INVALID, policy=policy if isinstance(policy, dict) else {},
                       mode="formal" if checkpoint is not None else "continuous",
                       checkpoint=checkpoint, observed=observed if isinstance(observed, dict) else {},
                       reference=reference if isinstance(reference, dict) else {},
                       regime_policy_hash=regime_policy_hash,
                       reason=f"consistency evaluation error: {type(exc).__name__}",
                       limitations=["evaluation failed; failing closed to INVALID"])


def _evaluate(observed, reference, *, policy, checkpoint, regime_policy_hash, require_regime,
              group_key) -> Dict:
    mode = "formal" if checkpoint is not None else "continuous"

    # ---------- 1. structural INVALID ------------------------------------------------ #
    okp, whyp = validate_consistency_policy(policy)
    if not okp:
        return _result(INVALID, policy=policy if isinstance(policy, dict) else {}, mode=mode,
                       checkpoint=checkpoint, observed=observed if isinstance(observed, dict) else {},
                       reference=reference if isinstance(reference, dict) else {},
                       regime_policy_hash=regime_policy_hash, reason=f"invalid consistency policy: {whyp}")
    if not isinstance(observed, dict):
        return _result(INVALID, policy=policy, mode=mode, checkpoint=checkpoint, observed={},
                       reference=reference if isinstance(reference, dict) else {},
                       regime_policy_hash=regime_policy_hash, reason="observed evidence is not a dict")
    if not isinstance(reference, dict):
        return _result(INVALID, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference={}, regime_policy_hash=regime_policy_hash,
                       reason="reference ranges object is not a dict")
    ref_policy = reference.get("policy")
    if not (_is_hash(reference.get("range_hash")) and _is_hash(reference.get("policy_hash"))
            and isinstance(ref_policy, dict) and isinstance(reference.get("groups"), dict)
            and isinstance(reference.get("comparability_basis"), dict)):
        return _result(INVALID, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash,
                       reason="reference is missing required integrity fields "
                              "(range_hash/policy_hash/policy/groups/comparability_basis)")
    # a valid hash alone is NOT proof the embedded policy satisfies the contract — validate it.
    ok_ref, why_ref = validate_range_policy(ref_policy)
    if not ok_ref:
        return _result(INVALID, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash,
                       reason=f"embedded range policy is structurally invalid: {why_ref}")
    required_metrics = list(ref_policy["required_metrics"])  # guaranteed non-empty by validation
    # observed 'metrics' container: if present it MUST be a dict — a malformed container is
    # INVALID, never silently coerced to empty-and-unavailable.
    if "metrics" in observed and not isinstance(observed["metrics"], dict):
        return _result(INVALID, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash,
                       reason="observed['metrics'] must be a dict when present")
    # observed comparability basis, if present, must be well-formed (compared below in phase 2)
    comp_status = _comparability_status(observed, reference)
    if comp_status == "malformed":
        return _result(INVALID, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash,
                       reason="observed 'comparability' metadata is malformed "
                              f"(must declare {list(COMPARABILITY_BASIS_KEYS)})")
    # a malformed-datatype observed value for a required metric is INVALID (distinct from a
    # missing / non-finite value, which is NOT_AVAILABLE and handled later). Checked here, before
    # FAILED, so a corrupt input is never masked by a safety stop.
    obs_metrics_early = observed.get("metrics")
    obs_metrics_early = obs_metrics_early if isinstance(obs_metrics_early, dict) else {}
    for _m in required_metrics:
        if _value_kind(_observed_value(obs_metrics_early.get(_m))) == "malformed":
            return _result(INVALID, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                           reference=reference, regime_policy_hash=regime_policy_hash,
                           reason=f"observed required metric {_m!r} has a malformed datatype "
                                  f"(expected a finite int/float scalar)")
    # expected hashes, if present, must be well-formed strings
    for hk in ("expected_range_hash", "expected_range_policy_hash", "expected_consistency_policy_hash",
               "expected_regime_policy_hash"):
        if hk in observed and observed[hk] is not None and not _is_hash(observed[hk]):
            return _result(INVALID, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                           reference=reference, regime_policy_hash=regime_policy_hash,
                           reason=f"{hk} is present but malformed (must be a non-empty string)")
    # absolute-risk record, if present, must be well-formed
    ar = observed.get("absolute_risk")
    if ar is not None and (not isinstance(ar, dict) or not isinstance(ar.get("breached"), bool)):
        return _result(INVALID, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash,
                       reason="absolute_risk must be a dict whose 'breached' is explicitly True/False")
    # checkpoint schedule integrity (reject arbitrary off-checkpoint conclusions)
    checkpoints = list(policy["checkpoints"])
    if checkpoint is not None:
        if not (isinstance(checkpoint, int) and not isinstance(checkpoint, bool)) or checkpoint not in checkpoints:
            return _result(INVALID, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                           reference=reference, regime_policy_hash=regime_policy_hash,
                           reason=f"checkpoint {checkpoint!r} is not in the frozen schedule {checkpoints}; "
                                  "arbitrary off-checkpoint conclusions are rejected")
    if require_regime and not _is_hash(regime_policy_hash):
        return _result(INVALID, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash,
                       reason="require_regime is set but no valid regime_policy_hash was supplied")

    # ---------- 2. INVALIDATED (integrity) — highest actionable precedence ----------- #
    recomputed = _recompute_range_hash(reference)
    if recomputed != reference["range_hash"]:
        return _result(INVALIDATED, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash,
                       reason="reference content hash does not reproduce range_hash (tampered range object)",
                       limitations=["frozen comparison basis changed; verdict withheld"])
    if range_policy_hash(ref_policy) != reference["policy_hash"]:
        return _result(INVALIDATED, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash,
                       reason="embedded range policy does not reproduce policy_hash (tampered policy)",
                       limitations=["frozen comparison basis changed; verdict withheld"])
    mismatches = []
    er = observed.get("expected_range_hash")
    if _is_hash(er) and er != reference["range_hash"]:
        mismatches.append("expected_range_hash")
    erp = observed.get("expected_range_policy_hash")
    if _is_hash(erp) and erp != reference["policy_hash"]:
        mismatches.append("expected_range_policy_hash")
    ecp = observed.get("expected_consistency_policy_hash")
    if _is_hash(ecp) and ecp != consistency_policy_hash(policy):
        mismatches.append("expected_consistency_policy_hash")
    erg = observed.get("expected_regime_policy_hash")
    if _is_hash(erg) and regime_policy_hash is not None and erg != regime_policy_hash:
        mismatches.append("expected_regime_policy_hash")
    if mismatches:
        return _result(INVALIDATED, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash,
                       reason=f"frozen hash drift on: {mismatches}",
                       limitations=["observation prepared against a different frozen basis"])
    # a well-formed but DIFFERENT calculation basis is a basis drift -> INVALIDATED
    if comp_status == "mismatch":
        return _result(INVALIDATED, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash,
                       reason="observed comparability basis differs from the frozen reference basis "
                              "(e.g. different cost basis, segment horizon, aggregation or grouping)",
                       limitations=["observed metrics were not constructed on the reference basis"])

    # ---------- 3. FAILED (absolute risk breach; safety fires anytime) --------------- #
    if isinstance(ar, dict) and ar.get("breached") is True:
        detail = ar.get("detail") or "absolute frozen risk limit breached"
        return _result(FAILED, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash,
                       metric_findings={"__absolute_risk__": {"status": "RISK_BREACH", "available": True,
                                                             "explanation": str(detail)}},
                       reason=f"absolute risk breach: {detail}",
                       explanation="Hard safety stop: an absolute frozen risk limit was breached.")

    # ---------- 4. NOT_AVAILABLE (required evidence missing/non-finite) --------------- #
    if ar is None:
        return _result(NOT_AVAILABLE, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash,
                       reason="absolute_risk evidence absent; cannot confirm safety",
                       limitations=["supply an explicit absolute_risk record with breached True/False"])
    trades = observed.get("trade_count")
    obs_count = observed.get("observation_count")
    if not _nonneg_int(trades) or not _nonneg_int(obs_count):
        return _result(NOT_AVAILABLE, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash,
                       reason="trade_count and observation_count must both be present non-negative ints")
    trades = int(trades)
    obs_count = int(obs_count)
    reached = max([c for c in checkpoints if c <= trades], default=None)
    next_cp = min([c for c in checkpoints if c > trades], default=None)

    # required integrity hashes + comparability basis must be PRESENT for a formal verdict
    # (missing != matching; copying a hash is not enough — the basis must be declared)
    if mode == "formal":
        required_hashes = ["expected_range_hash", "expected_range_policy_hash",
                           "expected_consistency_policy_hash"]
        if require_regime:
            required_hashes.append("expected_regime_policy_hash")
        missing_hashes = [h for h in required_hashes if not _is_hash(observed.get(h))]
        if missing_hashes:
            return _result(NOT_AVAILABLE, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                           reference=reference, regime_policy_hash=regime_policy_hash, reached=reached,
                           next_cp=next_cp,
                           reason=f"missing required integrity hash(es): {missing_hashes}",
                           limitations=["a formal verdict requires the observation's frozen expected hashes"])
        if comp_status == "missing":
            return _result(NOT_AVAILABLE, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                           reference=reference, regime_policy_hash=regime_policy_hash, reached=reached,
                           next_cp=next_cp,
                           reason="observed 'comparability' basis is not declared; comparability "
                                  "cannot be established by copying the expected hash alone",
                           limitations=["declare how the observed metrics were constructed "
                                        f"({list(COMPARABILITY_BASIS_KEYS)})"])

    # required metrics come from the FROZEN RANGE POLICY (already validated + captured above),
    # not from the PASS subset
    group_metrics = _group_metrics(reference, group_key)
    if group_metrics is None:
        return _result(NOT_AVAILABLE, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash, reached=reached,
                       next_cp=next_cp, required_metrics=required_metrics, unavailable_required=required_metrics,
                       all_available=False, reason=f"reference group {group_key!r} not available")

    obs_metrics = observed.get("metrics")
    obs_metrics = obs_metrics if isinstance(obs_metrics, dict) else {}

    findings: Dict[str, Dict] = {}
    available_required: List[str] = []
    unavailable_required: List[str] = []
    persistence_min = int(policy["persistence_min_periods"])
    n_deviating = 0
    n_persistent = 0

    for metric in required_metrics:
        rng = group_metrics.get(metric)
        range_ok = (isinstance(rng, dict) and rng.get("classification") == PASS
                    and _is_finite(rng.get("lower")) and _is_finite(rng.get("upper")))
        cell = obs_metrics.get(metric)
        value = _observed_value(cell)
        # malformed datatypes already returned INVALID above; here only ok/missing/nonfinite remain
        value_ok = _value_kind(value) == "ok"
        if not (range_ok and value_ok):
            unavailable_required.append(metric)
            findings[metric] = {
                "metric": metric, "required": True, "available": False,
                "observed": float(value) if value_ok else None,
                "expected_lower": float(rng["lower"]) if range_ok else None,
                "expected_upper": float(rng["upper"]) if range_ok else None,
                "status": NOT_AVAILABLE,
                "explanation": ("no valid frozen range" if not range_ok else "observed value missing/non-finite"),
            }
            continue
        available_required.append(metric)
        lower, upper, value = float(rng["lower"]), float(rng["upper"]), float(value)
        excursion = cell.get("excursion_periods") if isinstance(cell, dict) else None
        n_periods = cell.get("n_periods") if isinstance(cell, dict) else None
        in_range = lower <= value <= upper
        if value < lower:
            direction, magnitude = "below", lower - value
        elif value > upper:
            direction, magnitude = "above", value - upper
        else:
            direction, magnitude = "none", 0.0
        persistent = False
        if not in_range:
            n_deviating += 1
            if _nonneg_int(excursion) and int(excursion) >= persistence_min:
                persistent = True
                n_persistent += 1
            status = "PERSISTENT_DEVIATION" if persistent else "DEVIATION"
        else:
            status = "CONSISTENT"
        findings[metric] = {
            "metric": metric, "required": True, "available": True, "observed": value,
            "expected_lower": lower, "expected_upper": upper, "in_range": in_range,
            "deviation_direction": direction, "deviation_magnitude": float(magnitude),
            "excursion_periods": int(excursion) if _nonneg_int(excursion) else None,
            "n_periods": int(n_periods) if _nonneg_int(n_periods) else None,
            "persistent": persistent, "status": status,
            "explanation": _metric_explanation(metric, value, lower, upper, direction, persistent),
        }

    all_available = (len(unavailable_required) == 0)

    # continuous monitoring never concludes CONSISTENT/DRIFT
    if mode == "continuous":
        return _result(EXTEND, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash, reached=reached,
                       next_cp=next_cp, required_metrics=required_metrics,
                       available_required=available_required, unavailable_required=unavailable_required,
                       all_available=all_available, metric_findings=findings,
                       reason="continuous monitoring: no breach/integrity failure; a formal verdict is "
                              "only drawn from an exact checkpoint snapshot",
                       limitations=[EXTEND],
                       explanation="Monitoring only — safety and integrity are clear; the test continues.")

    # completeness before ANY formal statistical conclusion (DRIFT or CONSISTENT)
    if not all_available:
        return _result(NOT_AVAILABLE, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash, reached=reached,
                       next_cp=next_cp, required_metrics=required_metrics,
                       available_required=available_required, unavailable_required=unavailable_required,
                       all_available=False, metric_findings=findings,
                       reason=f"required evidence incomplete; unavailable: {sorted(unavailable_required)}",
                       explanation="Consistency cannot be judged while required metrics/ranges are unavailable.")

    # ---------- 5. EXTEND (exact-checkpoint optional-stopping gate) ------------------- #
    if trades != int(checkpoint):
        if trades < int(checkpoint):
            why = f"checkpoint {checkpoint} not reached ({trades} < {checkpoint} trades)"
            expl = "Not enough trades to evaluate this pre-registered checkpoint yet."
        else:
            why = (f"snapshot trade_count {trades} exceeds checkpoint {checkpoint}; a formal verdict "
                   f"requires an immutable snapshot at exactly {checkpoint} trades")
            expl = ("Past the checkpoint without an exact snapshot — issuing a new verdict here would be "
                    "optional stopping, so we EXTEND.")
        return _result(EXTEND, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash, reached=reached,
                       next_cp=next_cp, required_metrics=required_metrics,
                       available_required=available_required, unavailable_required=unavailable_required,
                       all_available=True, metric_findings=findings, reason=why,
                       limitations=[EXTEND], explanation=expl)
    if trades < int(policy["min_trades"]) or obs_count < int(policy["min_observations"]):
        return _result(EXTEND, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash, reached=reached,
                       next_cp=next_cp, required_metrics=required_metrics,
                       available_required=available_required, unavailable_required=unavailable_required,
                       all_available=True, metric_findings=findings,
                       reason=f"below minimum evidence (trades={trades}/{policy['min_trades']}, "
                              f"obs={obs_count}/{policy['min_observations']})",
                       limitations=[EXTEND], explanation="Below the minimum evidence for a formal verdict.")

    # ---------- 6. DRIFT / CONSISTENT ------------------------------------------------ #
    multi_metric_min = int(policy["multi_metric_min"])
    drift_detected = (n_persistent >= 1) or (n_deviating >= multi_metric_min)
    if drift_detected:
        why = []
        if n_persistent >= 1:
            why.append(f"{n_persistent} metric(s) persistently outside range")
        if n_deviating >= multi_metric_min:
            why.append(f"{n_deviating} metrics simultaneously outside range (>= {multi_metric_min})")
        return _result(DRIFT, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                       reference=reference, regime_policy_hash=regime_policy_hash, reached=reached,
                       next_cp=next_cp, required_metrics=required_metrics,
                       available_required=available_required, unavailable_required=[],
                       all_available=True, metric_findings=findings,
                       reason="behavioural drift: " + "; ".join(why),
                       explanation="Observed behaviour has drifted from the frozen expectation: "
                                   + "; ".join(why) + ".")

    isolated = sorted(m for m, f in findings.items() if f.get("status") == "DEVIATION")
    lim = []
    if isolated:
        lim.append(f"single isolated non-persistent excursion(s) noted (not drift): {isolated}")
    return _result(CONSISTENT, policy=policy, mode=mode, checkpoint=checkpoint, observed=observed,
                   reference=reference, regime_policy_hash=regime_policy_hash, reached=reached,
                   next_cp=next_cp, required_metrics=required_metrics,
                   available_required=available_required, unavailable_required=[], all_available=True,
                   metric_findings=findings, limitations=lim,
                   reason="all required metrics inside the frozen distribution; no absolute-risk breach",
                   explanation=(f"Observed behaviour is consistent with the frozen expectation at checkpoint "
                                f"{checkpoint}. PnL sign is irrelevant so long as every metric sits inside "
                                "its expected range."))


def _metric_explanation(metric, value, lower, upper, direction, persistent) -> str:
    if direction == "none":
        return f"{metric}={value:.6g} inside [{lower:.6g}, {upper:.6g}]"
    edge = lower if direction == "below" else upper
    tag = "persistently " if persistent else ""
    return f"{metric}={value:.6g} {tag}{direction} expected edge {edge:.6g}"
