"""Frozen, seeded, dependence-aware expected-behavioural ranges for shadow-forward.

Reference ranges are built ONLY from historical out-of-sample (OOS) evidence supplied by the
caller. Each range is a seeded moving-block-bootstrap confidence interval for the MEAN of a
behavioural metric across ORDERED OOS segments, so serial dependence inside the evidence is
respected (an i.i.d. bootstrap would understate the interval). It is an interval for the mean
across segments — NOT a prediction interval for a single new observation. An observed value may
only be compared to it when it shares the frozen comparability contract (same metric definition,
aggregation, segment horizon, ordering, grouping and cost basis); that contract is frozen into
the policy/range hashes.

Ordering. Blocks of *consecutive* segments only make sense if the segments have a meaningful
order, so every segment must carry a frozen ordering field (``segment_index`` by default; a
``fold_index`` or ``period_end`` may be frozen instead). Within every group the ordering field
must be present, real, unique and strictly increasing in the order supplied — arbitrary caller
list order is never used silently. Exact-duplicate segments are removed BEFORE blocks are
formed; because segments are required to arrive in strictly-increasing order, removing a
duplicate simply closes the gap and the remaining unique segments stay contiguous, so block
adjacency is well defined over the de-duplicated, ordered segments. Duplicate counts are
reported globally and per group/metric.

Required vs optional metrics. The policy freezes an explicit ``required_metrics`` list and an
explicit ``optional_metrics`` list. The whole result is PASS / counts-as-evidence ONLY when
EVERY required metric has a valid frozen range in EVERY group; one metric passing never makes
the result pass. An optional metric (e.g. ``long_short_balance`` on a long-only strategy) may
be NOT_AVAILABLE, but only because it was declared optional up front — never inferred optional
because data happened to be missing.

Fail-closed missing-data policy. For a required metric, ANY missing or non-finite value makes
that metric NOT_AVAILABLE — a range is never reported just because enough other values
survived. A malformed numeric value (wrong type, incl. bool) makes the whole build INVALID.
Offending segment order-indexes and counts are reported; evidence is never dropped, repaired
or reordered. Ordinary Python ints/floats and legitimate NumPy integer/floating scalars are
accepted; bool, NaN and infinity are rejected.

Block-count diagnostic. ``block_count_approximation`` is reported as N / block_length. It is a
purely DESCRIPTIVE diagnostic — how many non-overlapping blocks the sample spans — NOT a
statistically estimated effective sample size, and NOT guaranteed to be conservative.
``block_effective_sample_size`` is retained as a clearly-marked legacy alias of the same value.

The whole result is canonical-JSON hashable (``range_hash``) and embeds the full frozen policy
so downstream integrity checks can reproduce both ``range_hash`` and ``policy_hash``. The build
is deterministic for identical evidence, policy and seed, and the evidence is never mutated.

Research and simulation only — no broker, no real orders, no private keys.
"""
from __future__ import annotations

import math
import numbers
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.forward.manifest import canonical_json, sha256_text
from app.forward.shadow.dataquality import INVALID, NOT_AVAILABLE, PASS

RANGE_METHOD = "moving-block-bootstrap-mean-range"
RANGE_VERSION = "ranges-v2"

EXTEND = "EXTEND"

# Behavioural metrics. The 7 required ones must each have a valid range for the result to
# count as evidence; long/short balance is optional (inapplicable to long-only strategies).
DEFAULT_REQUIRED_METRICS: Tuple[str, ...] = (
    "return",
    "drawdown",
    "trade_count",
    "turnover",
    "exposure_time",
    "win_rate",
    "avg_holding_period",
)
DEFAULT_OPTIONAL_METRICS: Tuple[str, ...] = ("long_short_balance",)

_ALLOWED_ORDER_FIELDS: Tuple[str, ...] = ("segment_index", "fold_index", "period_end")
_MISSING_DATA_POLICY = "fail_closed"

# the concrete basis on which a reference is built; an observed result must declare the SAME
# basis (see consistency.evaluate_consistency) before it may be compared to a range.
COMPARABILITY_BASIS_KEYS: Tuple[str, ...] = (
    "metric_definition_version",
    "aggregation",
    "segment_horizon",
    "ordering_basis",
    "grouping_basis",
    "cost_basis",
)


def comparability_basis(policy: Dict, group_by) -> Dict:
    """Concrete, comparable basis for a reference built with ``policy`` and ``group_by``.
    Both the reference and any observed result must agree on every field to be comparable."""
    return {
        "metric_definition_version": policy["metric_definition_version"],
        "aggregation": policy["aggregation"],
        "segment_horizon": policy["segment_horizon"],
        "ordering_basis": policy["order_field"],
        "grouping_basis": list(group_by) if group_by else [],
        "cost_basis": policy["cost_basis"],
    }

_ALL_GROUP = "__ALL__"

_NOTE = ("Expected-behaviour ranges are seeded moving-block-bootstrap intervals for the mean "
         "of each metric across ORDERED OOS segments. Required metrics fail closed on any "
         "missing/non-finite value; malformed values make the build INVALID. Exact-duplicate "
         "segments are de-duplicated and counted, and the evidence is never mutated. "
         "block_count_approximation = N / block_length is a descriptive block-count diagnostic; "
         "it is NOT a statistically estimated effective sample size and is NOT guaranteed to be "
         "conservative (block_effective_sample_size exists only as a legacy alias of the same "
         "value). Ranges describe historical behaviour only — they authorise nothing.")


# --------------------------------------------------------------------------- #
# numeric helpers (accept numpy scalars, reject bool/NaN/inf)                  #
# --------------------------------------------------------------------------- #
def _is_bool(v) -> bool:
    return isinstance(v, bool) or isinstance(v, np.bool_)


def _is_real(v) -> bool:
    """True for int/float and legitimate NumPy integer/floating scalars; False for bool."""
    return (not _is_bool(v)) and isinstance(v, numbers.Real)


def _is_finite(v) -> bool:
    return _is_real(v) and math.isfinite(float(v))


# --------------------------------------------------------------------------- #
# frozen policy                                                               #
# --------------------------------------------------------------------------- #
def freeze_range_policy(
    *,
    seed: int = 20240115,
    block_length: int = 4,
    n_resamples: int = 2000,
    percentile_lower: float = 5.0,
    percentile_upper: float = 95.0,
    min_unique_segments: int = 8,
    required_metrics: Sequence[str] = DEFAULT_REQUIRED_METRICS,
    optional_metrics: Sequence[str] = DEFAULT_OPTIONAL_METRICS,
    order_field: str = "segment_index",
    missing_data_policy: str = _MISSING_DATA_POLICY,
    aggregation: str = "per_segment_then_mean",
    cost_basis: str = "locked_backtest_costs",
    metric_definition_version: str = "metrics-v1",
    segment_horizon: str = "per_segment",
) -> Dict:
    """Return a frozen, canonical range policy. Every field that affects the numbers is
    explicit so the policy hash changes if any of them drifts."""
    if not (isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0):
        raise ValueError("seed must be a non-negative int")
    if not (isinstance(block_length, int) and not isinstance(block_length, bool) and block_length >= 1):
        raise ValueError("block_length must be an int >= 1")
    if not (isinstance(n_resamples, int) and not isinstance(n_resamples, bool) and n_resamples >= 1):
        raise ValueError("n_resamples must be an int >= 1")
    if not (isinstance(min_unique_segments, int) and not isinstance(min_unique_segments, bool)
            and min_unique_segments >= 2):
        raise ValueError("min_unique_segments must be an int >= 2")
    if not (math.isfinite(percentile_lower) and math.isfinite(percentile_upper)):
        raise ValueError("percentile bounds must be finite")
    if not (0.0 <= percentile_lower < percentile_upper <= 100.0):
        raise ValueError("require 0 <= percentile_lower < percentile_upper <= 100")
    required = tuple(required_metrics)
    optional = tuple(optional_metrics)
    if not required:
        raise ValueError("at least one required metric is needed")
    for m in required + optional:
        if not isinstance(m, str) or not m:
            raise ValueError("metric names must be non-empty strings")
    if len(set(required)) != len(required):
        raise ValueError("required metric names must be unique")
    if len(set(optional)) != len(optional):
        raise ValueError("optional metric names must be unique")
    if set(required) & set(optional):
        raise ValueError("a metric cannot be both required and optional")
    if not isinstance(order_field, str) or order_field not in _ALLOWED_ORDER_FIELDS:
        raise ValueError(f"order_field must be one of {_ALLOWED_ORDER_FIELDS}")
    if missing_data_policy != _MISSING_DATA_POLICY:
        raise ValueError("missing_data_policy must be 'fail_closed'")
    if not isinstance(aggregation, str) or not aggregation:
        raise ValueError("aggregation must be a non-empty string")
    if not isinstance(cost_basis, str) or not cost_basis:
        raise ValueError("cost_basis must be a non-empty string")
    if not isinstance(metric_definition_version, str) or not metric_definition_version:
        raise ValueError("metric_definition_version must be a non-empty string")
    if not isinstance(segment_horizon, str) or not segment_horizon:
        raise ValueError("segment_horizon must be a non-empty string")
    return {
        "method": RANGE_METHOD,
        "version": RANGE_VERSION,
        "seed": int(seed),
        "block_length": int(block_length),
        "n_resamples": int(n_resamples),
        "percentile_lower": float(percentile_lower),
        "percentile_upper": float(percentile_upper),
        "min_unique_segments": int(min_unique_segments),
        "required_metrics": list(required),
        "optional_metrics": list(optional),
        "metrics": list(required) + list(optional),
        "order_field": order_field,
        "missing_data_policy": missing_data_policy,
        "aggregation": aggregation,
        "cost_basis": cost_basis,
        "metric_definition_version": metric_definition_version,
        "segment_horizon": segment_horizon,
        "statistic": "mean across OOS segments",
        "resampling": "non-circular moving block bootstrap over ordered segments",
        # What the interval MEANS, and the basis an observed value must share to be comparable.
        "interval_semantics": ("a confidence interval for the MEAN of the metric across ordered "
                               "OOS segments; it is NOT a prediction interval for a single new "
                               "observation"),
        "comparability_contract": {
            "metric_definition": "identical per-metric definition to the evidence",
            "aggregation": aggregation,
            "segment_horizon": "identical segment horizon to the evidence segments",
            "ordering": order_field,
            "grouping": "identical group_by keys/values used at build time",
            "cost_basis": cost_basis,
            "note": ("an observed value may be compared to this interval only if it shares ALL of "
                     "the above; this contract is frozen into policy_hash/range_hash, so an "
                     "observation prepared on a different basis fails the frozen-hash check"),
        },
    }


DEFAULT_RANGE_POLICY: Dict = freeze_range_policy()


def range_policy_hash(policy: Dict) -> str:
    """Deterministic hash of the frozen range policy (drift-detectable)."""
    return sha256_text(canonical_json(policy))


def validate_range_policy(policy) -> Tuple[bool, str]:
    """Validate a supplied range policy before use. Returns (ok, reason)."""
    if not isinstance(policy, dict):
        return False, "policy is not a dict"
    if policy.get("method") != RANGE_METHOD or policy.get("version") != RANGE_VERSION:
        return False, "unsupported range method/version"
    seed = policy.get("seed")
    if not (isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0):
        return False, "seed must be a non-negative int"
    bl = policy.get("block_length")
    if not (isinstance(bl, int) and not isinstance(bl, bool) and bl >= 1):
        return False, "block_length must be an int >= 1"
    nr = policy.get("n_resamples")
    if not (isinstance(nr, int) and not isinstance(nr, bool) and nr >= 1):
        return False, "n_resamples must be an int >= 1"
    mus = policy.get("min_unique_segments")
    if not (isinstance(mus, int) and not isinstance(mus, bool) and mus >= 2):
        return False, "min_unique_segments must be an int >= 2"
    lo, hi = policy.get("percentile_lower"), policy.get("percentile_upper")
    if not (_is_finite(lo) and _is_finite(hi) and 0.0 <= lo < hi <= 100.0):
        return False, "invalid percentile bounds"
    req, opt = policy.get("required_metrics"), policy.get("optional_metrics")
    if not (isinstance(req, list) and req and all(isinstance(m, str) and m for m in req)):
        return False, "required_metrics must be a non-empty list of strings"
    if not (isinstance(opt, list) and all(isinstance(m, str) and m for m in opt)):
        return False, "optional_metrics must be a list of strings"
    if len(set(req)) != len(req):
        return False, "required_metrics must be unique"
    if len(set(opt)) != len(opt):
        return False, "optional_metrics must be unique"
    if set(req) & set(opt):
        return False, "required and optional metrics overlap"
    if policy.get("metrics") != list(req) + list(opt):
        return False, "metrics must equal required + optional"
    if policy.get("order_field") not in _ALLOWED_ORDER_FIELDS:
        return False, "order_field is not supported"
    if policy.get("missing_data_policy") != _MISSING_DATA_POLICY:
        return False, "missing_data_policy must be 'fail_closed'"
    if not (isinstance(policy.get("aggregation"), str) and policy["aggregation"]):
        return False, "aggregation must be a non-empty string"
    if not (isinstance(policy.get("cost_basis"), str) and policy["cost_basis"]):
        return False, "cost_basis must be a non-empty string"
    if not (isinstance(policy.get("metric_definition_version"), str) and policy["metric_definition_version"]):
        return False, "metric_definition_version must be a non-empty string"
    if not (isinstance(policy.get("segment_horizon"), str) and policy["segment_horizon"]):
        return False, "segment_horizon must be a non-empty string"
    if not (isinstance(policy.get("interval_semantics"), str) and policy["interval_semantics"]):
        return False, "interval_semantics metadata is required"
    cc = policy.get("comparability_contract")
    if not (isinstance(cc, dict) and cc.get("ordering") == policy.get("order_field")
            and cc.get("aggregation") == policy.get("aggregation")
            and cc.get("cost_basis") == policy.get("cost_basis")):
        return False, "comparability_contract must be present and consistent with the policy"
    return True, ""


# --------------------------------------------------------------------------- #
# bootstrap                                                                    #
# --------------------------------------------------------------------------- #
def _derive_seed(base_seed: int, group_key: str, metric: str) -> int:
    """Deterministically derive a per-(group, metric) seed from the frozen base seed."""
    digest = sha256_text(canonical_json([int(base_seed), group_key, metric]))
    return int(digest[:16], 16)


def _block_bootstrap_means(values: np.ndarray, block_length: int, n_resamples: int,
                           rng: np.random.Generator) -> np.ndarray:
    """Distribution of the resampled mean under a non-circular moving block bootstrap over the
    ordered values."""
    n = int(values.shape[0])
    block = min(int(block_length), n)
    if block < 1:
        block = 1
    n_blocks = int(math.ceil(n / block))
    max_start = n - block
    offsets = np.arange(block)
    means = np.empty(n_resamples, dtype=float)
    for r in range(n_resamples):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        idx = (starts[:, None] + offsets[None, :]).reshape(-1)[:n]
        means[r] = float(values[idx].mean())
    return means


def _metric_range(*, policy: Dict, group_key: str, metric: str, required: bool,
                  segment_count: int, duplicate_count: int, values: List[float],
                  missing_idx: List, non_finite_idx: List) -> Dict:
    """Build one metric range (or a fail-closed NOT_AVAILABLE)."""
    block_length = int(policy["block_length"])
    min_unique = int(policy["min_unique_segments"])
    p_lo = float(policy["percentile_lower"])
    p_hi = float(policy["percentile_upper"])
    usable = len(values)
    block_count = (usable / block_length) if block_length > 0 else 0.0

    base = {
        "metric": metric,
        "required": bool(required),
        "method": policy["method"],
        "version": policy["version"],
        "seed": int(policy["seed"]),
        "block_length": block_length,
        "n_resamples": int(policy["n_resamples"]),
        "percentile_lower": p_lo,
        "percentile_upper": p_hi,
        "segment_count": int(segment_count),
        "usable_count": int(usable),
        # descriptive diagnostic only: how many non-overlapping blocks the sample spans.
        "block_count_approximation": float(block_count),
        "block_count_approximation_note": ("descriptive diagnostic = usable_count / block_length; "
                                           "NOT a statistically estimated effective sample size and "
                                           "NOT guaranteed to be conservative"),
        # legacy alias (same value) — kept for backward compatibility, NOT a formal/conservative ESS
        "block_effective_sample_size": float(block_count),
        "block_effective_sample_size_note": "legacy alias of block_count_approximation; not a formal or conservative ESS",
        "missing_count": len(missing_idx),
        "non_finite_count": len(non_finite_idx),
        "missing_segment_order_indexes": list(missing_idx),
        "non_finite_segment_order_indexes": list(non_finite_idx),
        "duplicate_count": int(duplicate_count),
        "lower": None,
        "upper": None,
        "limitations": [],
    }

    # fail-closed: any missing/non-finite in a metric blocks a range (strict for required;
    # optional metrics simply become NOT_AVAILABLE without blocking the whole result).
    if missing_idx or non_finite_idx:
        base["classification"] = NOT_AVAILABLE
        base["limitations"] = [
            "fail_closed: missing/non-finite values present; no range reported",
            f"missing={len(missing_idx)}, non_finite={len(non_finite_idx)}",
        ]
        return base
    if usable < min_unique:
        base["classification"] = NOT_AVAILABLE
        base["limitations"] = [EXTEND, f"only {usable} usable segment(s); need >= {min_unique}"]
        return base

    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(_derive_seed(int(policy["seed"]), group_key, metric))
    means = _block_bootstrap_means(arr, block_length, int(policy["n_resamples"]), rng)
    lower = float(np.percentile(means, p_lo))
    upper = float(np.percentile(means, p_hi))
    if not (math.isfinite(lower) and math.isfinite(upper)):
        base["classification"] = NOT_AVAILABLE
        base["limitations"] = [EXTEND, "bootstrap produced non-finite bounds"]
        return base
    if lower > upper:
        lower, upper = upper, lower
    base["lower"] = lower
    base["upper"] = upper
    base["observed_mean"] = float(arr.mean())
    base["classification"] = PASS
    if block_count < 2.0:
        base["limitations"].append(
            f"block_count_approximation {block_count:.3f} is small; interval is fragile")
    return base


def _group_key_repr(group_by: Optional[Sequence[str]], record: Dict) -> Tuple[Optional[str], Dict]:
    """Deterministic string key + explicit field map. Returns (None, {}) if a group_by field is
    missing (caller must fail closed — never pool under null)."""
    if not group_by:
        return _ALL_GROUP, {}
    fields = {}
    for k in group_by:
        if k not in record:
            return None, {}
        fields[k] = record[k]
    return canonical_json([[k, fields[k]] for k in group_by]), fields


# --------------------------------------------------------------------------- #
# public entry point                                                          #
# --------------------------------------------------------------------------- #
def build_reference_ranges(
    evidence,
    *,
    policy: Dict = DEFAULT_RANGE_POLICY,
    group_by: Optional[Sequence[str]] = None,
) -> Dict:
    """Build frozen expected-behavioural ranges from segmented OOS evidence. Pure,
    deterministic, fail-closed and side-effect-free (the evidence is never mutated)."""
    try:
        return _build(evidence, policy=policy, group_by=group_by)
    except Exception as exc:  # defensive: never raise
        return _invalid(policy, group_by, f"reference-range build error: {type(exc).__name__}")


def _invalid(policy, group_by, reason: str, offending: Optional[Dict] = None) -> Dict:
    return {
        "kind": "reference_ranges",
        "classification": INVALID,
        "method": policy.get("method") if isinstance(policy, dict) else None,
        "version": policy.get("version") if isinstance(policy, dict) else None,
        "reason": reason,
        "group_by": list(group_by) if group_by else [],
        "groups": {},
        "offending": offending or {},
        "limitations": ["evidence/policy could not be evaluated; failing closed to INVALID"],
        "note": _NOTE,
    }


def _base_result(classification: str, *, policy: Dict, group_by, reason: str = "",
                 groups: Optional[Dict] = None, n_raw: int = 0, n_unique: int = 0,
                 duplicate_count: int = 0, required_metrics=None, optional_metrics=None,
                 available_required=None, unavailable_required=None,
                 all_required_available: Optional[bool] = None,
                 limitations: Optional[List[str]] = None) -> Dict:
    groups = groups or {}
    counts_as_evidence = bool(all_required_available) if all_required_available is not None else False
    out = {
        "kind": "reference_ranges",
        "classification": classification,
        "method": policy["method"],
        "version": policy["version"],
        "policy": policy,  # full frozen policy embedded so policy_hash is reproducible downstream
        "policy_hash": range_policy_hash(policy),
        "seed": int(policy["seed"]),
        "block_length": int(policy["block_length"]),
        "n_resamples": int(policy["n_resamples"]),
        "percentile_lower": float(policy["percentile_lower"]),
        "percentile_upper": float(policy["percentile_upper"]),
        "order_field": policy["order_field"],
        "group_by": list(group_by) if group_by else [],
        "comparability_basis": comparability_basis(policy, group_by),
        "required_metrics": list(required_metrics if required_metrics is not None else policy["required_metrics"]),
        "optional_metrics": list(optional_metrics if optional_metrics is not None else policy["optional_metrics"]),
        "available_required_metrics": sorted(available_required or []),
        "unavailable_required_metrics": sorted(unavailable_required or []),
        "all_required_ranges_available": bool(all_required_available) if all_required_available is not None else False,
        "counts_as_evidence": counts_as_evidence,
        "n_segments_raw": int(n_raw),
        "n_segments_unique": int(n_unique),
        "duplicate_count": int(duplicate_count),
        "reason": reason,
        "groups": groups,
        "limitations": limitations or [],
        "note": _NOTE,
    }
    # deterministic content hash over everything except the hash field itself
    out["range_hash"] = sha256_text(canonical_json(out))
    return out


def _build(evidence, *, policy, group_by) -> Dict:
    ok, why = validate_range_policy(policy)
    if not ok:
        return _invalid(policy, group_by, f"invalid range policy: {why}")

    required = list(policy["required_metrics"])
    optional = list(policy["optional_metrics"])
    declared = required + optional
    order_field = policy["order_field"]

    if group_by is not None:
        if not isinstance(group_by, (list, tuple)) or not all(isinstance(k, str) and k for k in group_by):
            return _invalid(policy, None, "group_by must be a sequence of non-empty field names")
        group_by = list(group_by)

    if not isinstance(evidence, (list, tuple)):
        return _invalid(policy, group_by, "evidence must be a list of segment records")
    n_raw = len(evidence)
    if n_raw == 0:
        return _base_result(NOT_AVAILABLE, policy=policy, group_by=group_by,
                            reason="no evidence segments supplied", all_required_available=False,
                            unavailable_required=required,
                            limitations=[EXTEND, "supply ordered historical OOS segments first"])

    # ---- structural + malformed-value validation (report offending, fail closed) -------- #
    malformed: List[Dict] = []
    for i, rec in enumerate(evidence):
        if not isinstance(rec, dict):
            return _invalid(policy, group_by, f"segment {i} is not a dict")
        if not isinstance(rec.get("metrics"), dict):
            return _invalid(policy, group_by, f"segment {i} has no 'metrics' dict")
        if order_field not in rec or not _is_finite(rec[order_field]):
            return _invalid(policy, group_by,
                            f"segment {i} missing/invalid frozen order field {order_field!r}")
        if group_by:
            for k in group_by:
                if k not in rec:
                    return _invalid(policy, group_by,
                                    f"segment {i} missing group_by field {k!r} (no null pooling)")
        for m in declared:
            if m in rec["metrics"]:
                v = rec["metrics"][m]
                # malformed = present but not a real number (str/list/None/bool/complex).
                # NaN / inf are real numbers here and are handled later as non-finite, not
                # malformed. bool is explicitly rejected by _is_real.
                if not _is_real(v):
                    malformed.append({"segment_order_index": rec[order_field], "metric": m,
                                      "type": type(v).__name__})
    if malformed:
        return _invalid(policy, group_by, "malformed numeric value(s) in required/optional metrics",
                        offending={"malformed": malformed[:50], "malformed_count": len(malformed)})

    # ---- deterministic exact-duplicate de-duplication (global) ------------------------- #
    seen: Dict[str, int] = {}
    unique_records: List[Dict] = []
    for rec in evidence:
        key = canonical_json(rec)
        if key in seen:
            seen[key] += 1
        else:
            seen[key] = 0
            unique_records.append(rec)
    total_duplicates = sum(seen.values())
    n_unique = len(unique_records)

    # ---- bucket into groups ------------------------------------------------------------ #
    grouped: Dict[str, Dict] = {}
    for rec in unique_records:
        gkey, fields = _group_key_repr(group_by, rec)
        if gkey is None:
            return _invalid(policy, group_by, "a segment is missing a group_by field (no null pooling)")
        g = grouped.setdefault(gkey, {"fields": fields, "records": []})
        g["records"].append(rec)

    # per-group duplicate counts: an exact-duplicate segment belongs to its own group, so we
    # count, per group, every original record whose canonical form was already seen.
    group_dupes: Dict[str, int] = {gk: 0 for gk in grouped}
    first_seen: Dict[str, bool] = {}
    for rec in evidence:
        key = canonical_json(rec)
        gkey, _ = _group_key_repr(group_by, rec)
        if gkey is None:
            continue
        if key in first_seen:
            group_dupes[gkey] = group_dupes.get(gkey, 0) + 1
        else:
            first_seen[key] = True

    out_groups: Dict[str, Dict] = {}
    union_unavailable: set = set()
    all_groups_complete = True
    for gkey in sorted(grouped.keys()):
        recs = grouped[gkey]["records"]
        fields = grouped[gkey]["fields"]
        # ordering: strictly increasing, unique, in supplied order (no silent reorder)
        order_vals = [rec[order_field] for rec in recs]
        for a, b in zip(order_vals, order_vals[1:]):
            if not (float(b) > float(a)):
                return _invalid(policy, group_by,
                                f"group {gkey}: order field {order_field!r} is not strictly "
                                f"increasing/unique as supplied")
        dup_ct = int(group_dupes.get(gkey, 0))
        metric_ranges: Dict[str, Dict] = {}
        available_required_g: List[str] = []
        unavailable_required_g: List[str] = []
        for metric in declared:
            is_req = metric in required
            values: List[float] = []
            missing_idx: List = []
            non_finite_idx: List = []
            for rec in recs:
                mv = rec["metrics"]
                if metric not in mv:
                    missing_idx.append(rec[order_field])
                    continue
                v = mv[metric]
                if _is_finite(v):
                    values.append(float(v))
                else:
                    non_finite_idx.append(rec[order_field])
            mr = _metric_range(policy=policy, group_key=gkey, metric=metric, required=is_req,
                               segment_count=len(recs), duplicate_count=dup_ct, values=values,
                               missing_idx=missing_idx, non_finite_idx=non_finite_idx)
            metric_ranges[metric] = mr
            if is_req:
                if mr["classification"] == PASS:
                    available_required_g.append(metric)
                else:
                    unavailable_required_g.append(metric)
                    union_unavailable.add(metric)
        group_complete = (len(unavailable_required_g) == 0)
        all_groups_complete = all_groups_complete and group_complete
        out_groups[gkey] = {
            "group_key": gkey,
            "group_fields": fields,
            "segment_count": len(recs),
            "duplicate_count": dup_ct,
            "available_required_metrics": sorted(available_required_g),
            "unavailable_required_metrics": sorted(unavailable_required_g),
            "all_required_ranges_available": group_complete,
            "counts_as_evidence": group_complete,
            "metrics": metric_ranges,
        }

    all_required_available = bool(out_groups) and all_groups_complete
    available_required = [m for m in required if m not in union_unavailable]
    classification = PASS if all_required_available else NOT_AVAILABLE
    limitations: List[str] = []
    if not all_required_available:
        limitations = [EXTEND, "not every required metric has a valid range in every group; "
                               "result does not count as complete evidence"]
    return _base_result(classification, policy=policy, group_by=group_by, groups=out_groups,
                        n_raw=n_raw, n_unique=n_unique, duplicate_count=total_duplicates,
                        required_metrics=required, optional_metrics=optional,
                        available_required=available_required,
                        unavailable_required=sorted(union_unavailable),
                        all_required_available=all_required_available, limitations=limitations)
