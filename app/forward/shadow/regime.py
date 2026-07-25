"""Frozen, versioned, causal market-regime classification for shadow-forward.

Every regime label is derived ONLY from information available at or before the current bar
(trailing rolling windows). There are no centered windows, no future returns, no backward
fill from future values and no full-sample thresholds. Warm-up bars without enough history
are labelled NOT_AVAILABLE, never guessed. Malformed / non-finite / mis-ordered input
returns a structured INVALID result rather than raising, and the input DataFrame is never
mutated.

The trend signal is the trailing fast/slow SMA separation of ``close``. The volatility signal
is genuinely built from ``high`` and ``low``: a per-bar high-low range (a causal, intrabar
range-volatility proxy) is smoothed over ``vol_window`` and compared against its own trailing
median over ``vol_ref_window``. ``high`` and ``low`` are therefore central required inputs, not
decoration, and each bar is validated for ``low <= close <= high``.

The regime policy is frozen and canonical-JSON hashable, so policy/method drift after
freezing is detectable via ``regime_policy_hash`` AND is rejected at evaluation time by
``classify_regimes`` (an unsupported method/version or malformed policy returns INVALID).

Research and simulation only — no broker, no real orders, no private keys.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.forward.manifest import canonical_json, sha256_text
from app.forward.shadow.dataquality import (
    INVALID,
    NOT_AVAILABLE,
    PASS,
    normalize_timestamps,
)

REGIME_METHOD = "sma-slope-trailing-hlrange-vol-regime"
REGIME_VERSION = "regime-v1"

# the four combined regime labels + the fail-closed markers
TREND_HIGH_VOL = "TREND_HIGH_VOL"
TREND_LOW_VOL = "TREND_LOW_VOL"
RANGE_HIGH_VOL = "RANGE_HIGH_VOL"
RANGE_LOW_VOL = "RANGE_LOW_VOL"
LABEL_NA = "NOT_AVAILABLE"
LABELS = (TREND_HIGH_VOL, TREND_LOW_VOL, RANGE_HIGH_VOL, RANGE_LOW_VOL, LABEL_NA)
_COMBINED = (TREND_HIGH_VOL, TREND_LOW_VOL, RANGE_HIGH_VOL, RANGE_LOW_VOL)

_REQUIRED_COLUMNS: Tuple[str, ...] = ("open_time", "close", "high", "low")
_WINDOW_FIELDS: Tuple[str, ...] = ("fast_window", "slow_window", "vol_window", "vol_ref_window")


def _expected_warmup(fast_window: int, slow_window: int, vol_window: int, vol_ref_window: int) -> int:
    """First index at which the slow SMA, the smoothed vol and its trailing median are all
    defined (0-based). Everything before this is NOT_AVAILABLE."""
    sma_ready = slow_window - 1
    vol_ready = (vol_window - 1) + (vol_ref_window - 1)
    return int(max(sma_ready, vol_ready))


def freeze_regime_policy(
    *,
    fast_window: int = 20,
    slow_window: int = 50,
    vol_window: int = 20,
    vol_ref_window: int = 100,
    trend_threshold: float = 0.01,
    vol_high_multiple: float = 1.0,
) -> Dict:
    """Return a frozen, canonical regime policy. All fields are explicit so the policy is
    fully reproducible and its hash changes if any field drifts."""
    if any((not isinstance(w, int)) or isinstance(w, bool) or w < 2
           for w in (fast_window, slow_window, vol_window, vol_ref_window)):
        raise ValueError("regime windows must be integers >= 2")
    if fast_window >= slow_window:
        raise ValueError("fast_window must be < slow_window")
    if not (math.isfinite(trend_threshold) and math.isfinite(vol_high_multiple)):
        raise ValueError("thresholds must be finite")
    if not (trend_threshold > 0 and vol_high_multiple > 0):
        raise ValueError("thresholds must be strictly positive")
    warmup = _expected_warmup(fast_window, slow_window, vol_window, vol_ref_window)
    return {
        "method": REGIME_METHOD,
        "version": REGIME_VERSION,
        "required_columns": list(_REQUIRED_COLUMNS),
        "lookback_windows": {"fast_window": fast_window, "slow_window": slow_window,
                             "vol_window": vol_window, "vol_ref_window": vol_ref_window},
        "warmup": int(warmup),
        "thresholds": {"trend_threshold": float(trend_threshold),
                       "vol_high_multiple": float(vol_high_multiple)},
        "timestamp_assumptions": {
            "unit": "utc_epoch_ms",
            "order": "strictly_increasing_unique",
            "order_enforced": True,            # classify_regimes rejects non-increasing / duplicate ts
            "uniform_cadence_required": False,
            "uniform_cadence_verified": False,  # spacing is assumed but NOT re-derived / checked
            "note": ("strictly increasing, unique timestamps are enforced; uniform cadence is "
                     "assumed but NOT verified by this code"),
        },
        "execution_timing": {
            # a label uses bar t's own high/low/close, so it is only known AFTER bar t closes.
            "label_available_after": "bar_t_close",
            "earliest_permitted_execution": "bar_t_plus_1_open",
            "prohibited": ["bar_t_open", "intrabar_before_bar_t_close"],
            "note": ("the regime label for bar t depends on bar t's completed OHLC, so it may "
                     "not drive an order at bar t open or intrabar; the earliest legal action is "
                     "bar t+1 open. A trailing calculation ALONE does not prevent same-bar "
                     "execution look-ahead — alignment must be enforced by the consumer "
                     "(see align_labels_to_execution)."),
        },
        "causality": "trailing-only windows; labels depend on the current and prior bars only",
    }


DEFAULT_REGIME_POLICY: Dict = freeze_regime_policy()


def regime_policy_hash(policy: Dict) -> str:
    """Deterministic hash of the frozen policy (drift-detectable)."""
    return sha256_text(canonical_json(policy))


def _win_ok(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 2


def validate_regime_policy(policy) -> Tuple[bool, str]:
    """Fully validate a supplied regime policy BEFORE it is indexed. Returns (ok, reason).

    An unsupported method/version, a missing/typed-wrong field, non-positive or non-finite
    thresholds, fast_window >= slow_window, an inconsistent warm-up or missing timestamp
    assumptions all fail closed."""
    if not isinstance(policy, dict):
        return False, "policy is not a dict"
    if policy.get("method") != REGIME_METHOD:
        return False, f"unsupported method {policy.get('method')!r}"
    if policy.get("version") != REGIME_VERSION:
        return False, f"unsupported version {policy.get('version')!r}"
    if policy.get("required_columns") != list(_REQUIRED_COLUMNS):
        return False, "required_columns do not match the frozen contract"
    lw = policy.get("lookback_windows")
    if not isinstance(lw, dict) or any(f not in lw for f in _WINDOW_FIELDS):
        return False, "lookback_windows is missing required fields"
    if any(not _win_ok(lw[f]) for f in _WINDOW_FIELDS):
        return False, "every lookback window must be an int >= 2"
    if lw["fast_window"] >= lw["slow_window"]:
        return False, "fast_window must be < slow_window"
    th = policy.get("thresholds")
    if not isinstance(th, dict) or "trend_threshold" not in th or "vol_high_multiple" not in th:
        return False, "thresholds are missing required fields"
    for k in ("trend_threshold", "vol_high_multiple"):
        v = th[k]
        if not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0):
            return False, f"threshold {k} must be a finite positive number"
    warm = policy.get("warmup")
    if not (isinstance(warm, int) and not isinstance(warm, bool) and warm >= 1):
        return False, "warmup must be an int >= 1"
    expected = _expected_warmup(lw["fast_window"], lw["slow_window"], lw["vol_window"], lw["vol_ref_window"])
    if warm != expected:
        return False, f"warmup {warm} inconsistent with windows (expected {expected})"
    ta = policy.get("timestamp_assumptions")
    if not isinstance(ta, dict):
        return False, "timestamp_assumptions must be a dict"
    if ta.get("unit") != "utc_epoch_ms":
        return False, "timestamp_assumptions.unit must be 'utc_epoch_ms'"
    if ta.get("order") != "strictly_increasing_unique":
        return False, "timestamp_assumptions.order must be 'strictly_increasing_unique'"
    if ta.get("order_enforced") is not True:
        return False, "timestamp_assumptions.order_enforced must be True (this code enforces it)"
    # be honest: this code does NOT verify uniform cadence, so the frozen contract must say so
    if ta.get("uniform_cadence_verified") is not False:
        return False, "timestamp_assumptions.uniform_cadence_verified must be False (cadence is not checked)"
    et = policy.get("execution_timing")
    if not isinstance(et, dict):
        return False, "execution_timing must be a dict"
    if et.get("label_available_after") != "bar_t_close":
        return False, "execution_timing.label_available_after must be 'bar_t_close'"
    if et.get("earliest_permitted_execution") != "bar_t_plus_1_open":
        return False, "execution_timing.earliest_permitted_execution must be 'bar_t_plus_1_open'"
    prohibited = et.get("prohibited")
    if not (isinstance(prohibited, list)
            and "bar_t_open" in prohibited and "intrabar_before_bar_t_close" in prohibited):
        return False, "execution_timing.prohibited must forbid bar_t_open and intrabar execution"
    return True, ""


def _result(classification: str, *, policy: Dict, labels: Optional[List[str]] = None,
            n_rows: int = 0, warmup: int = 0, reason: str = "",
            limitations: Optional[List[str]] = None,
            offending: Optional[Dict] = None) -> Dict:
    labels = labels or []
    counts: Dict[str, int] = {}
    for lab in labels:
        counts[lab] = counts.get(lab, 0) + 1
    n_labeled = sum(v for k, v in counts.items() if k in _COMBINED)
    try:
        phash = regime_policy_hash(policy) if isinstance(policy, dict) and policy else None
    except Exception:
        phash = None
    return {
        "kind": "regime_classification",
        "classification": classification,
        "method": policy.get("method") if isinstance(policy, dict) else None,
        "version": policy.get("version") if isinstance(policy, dict) else None,
        "policy_hash": phash,
        "n_rows": int(n_rows),
        "warmup": int(warmup),
        "labels": labels,
        "counts": counts,
        "n_labeled": int(n_labeled),
        "reason": reason,
        "limitations": limitations or [],
        "offending": offending or {},
        "note": ("Causal, trailing-only regime labels. Warm-up rows are NOT_AVAILABLE, "
                 "never guessed. The input frame is never modified."),
    }


def _coerce_column(df: pd.DataFrame, name: str) -> Tuple[Optional[np.ndarray], str]:
    """Coerce one OHLC column to float WITHOUT mutating df. Returns (array, reason). Any
    non-numeric / non-finite value fails closed (array is None)."""
    ser = pd.to_numeric(df[name], errors="coerce")
    arr = ser.to_numpy(dtype=float, copy=True)
    if not np.all(np.isfinite(arr)):
        return None, f"non-finite or non-numeric values in {name}"
    return arr, ""


def classify_regimes(df: pd.DataFrame, policy: Dict = DEFAULT_REGIME_POLICY) -> Dict:
    """Label each bar with a frozen, causal regime. Fail-closed and deterministic; never
    mutates ``df``."""
    try:
        ok, why = validate_regime_policy(policy)
        if not ok:
            return _result(INVALID, policy=policy if isinstance(policy, dict) else {},
                           reason=f"invalid regime policy: {why}")
        if not isinstance(df, pd.DataFrame):
            return _result(INVALID, policy=policy, reason="input is not a DataFrame")
        req = policy["required_columns"]
        missing = [c for c in req if c not in df.columns]
        if missing:
            return _result(INVALID, policy=policy, reason=f"missing required columns {missing}")
        n = int(len(df))
        if n == 0:
            return _result(NOT_AVAILABLE, policy=policy, n_rows=0, reason="empty frame")

        # timestamps: normalise to UTC ms, require strictly increasing + unique (no reorder)
        ts_check, ot = normalize_timestamps(df["open_time"])
        if ot is None:
            return _result(INVALID, policy=policy, n_rows=n,
                           reason=f"timestamps unusable: {ts_check['explanation']}")
        if n > 1:
            d = np.diff(ot)
            if np.any(d <= 0):
                return _result(INVALID, policy=policy, n_rows=n,
                               reason="timestamps not strictly increasing / unique")

        close, why = _coerce_column(df, "close")
        if close is None:
            return _result(INVALID, policy=policy, n_rows=n, reason=why)
        high, why = _coerce_column(df, "high")
        if high is None:
            return _result(INVALID, policy=policy, n_rows=n, reason=why)
        low, why = _coerce_column(df, "low")
        if low is None:
            return _result(INVALID, policy=policy, n_rows=n, reason=why)
        if np.any(close <= 0):
            return _result(INVALID, policy=policy, n_rows=n, reason="non-positive close price")
        # high/low are central inputs: enforce a coherent bar geometry
        bad_geom = np.where(~((low <= high) & (low <= close) & (close <= high)))[0]
        if bad_geom.size:
            return _result(INVALID, policy=policy, n_rows=n,
                           reason="incoherent OHLC bar geometry (require low <= close <= high)",
                           offending={"bad_bar_indexes": [int(i) for i in bad_geom[:20]],
                                      "bad_bar_count": int(bad_geom.size)})

        lw = policy["lookback_windows"]
        th = policy["thresholds"]
        fast_w, slow_w = int(lw["fast_window"]), int(lw["slow_window"])
        vol_w, vol_ref_w = int(lw["vol_window"]), int(lw["vol_ref_window"])
        trend_thr = float(th["trend_threshold"])
        vol_mult = float(th["vol_high_multiple"])

        c = pd.Series(close)
        sma_fast = c.rolling(fast_w, min_periods=fast_w).mean().to_numpy()
        sma_slow = c.rolling(slow_w, min_periods=slow_w).mean().to_numpy()

        # volatility genuinely from high & low: per-bar normalised intrabar range, smoothed,
        # then compared to its own trailing median (all causal / trailing-only).
        hl_range = (high - low) / close
        vol = pd.Series(hl_range).rolling(vol_w, min_periods=vol_w).mean().to_numpy()
        vol_ref = pd.Series(vol).rolling(vol_ref_w, min_periods=vol_ref_w).median().to_numpy()

        with np.errstate(divide="ignore", invalid="ignore"):
            slope = np.where(close != 0, np.abs(sma_fast - sma_slow) / np.abs(close), np.nan)

        labels: List[str] = []
        first_labeled: Optional[int] = None
        for i in range(n):
            if not (np.isfinite(sma_slow[i]) and np.isfinite(vol_ref[i])
                    and np.isfinite(slope[i]) and np.isfinite(vol[i])):
                labels.append(LABEL_NA)
                continue
            if first_labeled is None:
                first_labeled = i
            trending = slope[i] > trend_thr
            high_vol = vol[i] > vol_ref[i] * vol_mult
            if trending and high_vol:
                labels.append(TREND_HIGH_VOL)
            elif trending:
                labels.append(TREND_LOW_VOL)
            elif high_vol:
                labels.append(RANGE_HIGH_VOL)
            else:
                labels.append(RANGE_LOW_VOL)

        warmup = int(first_labeled) if first_labeled is not None else n
        n_labeled = sum(1 for x in labels if x != LABEL_NA)
        classification = PASS if n_labeled > 0 else NOT_AVAILABLE
        limitations = []
        if classification == NOT_AVAILABLE:
            limitations.append("insufficient history: every bar is within the warm-up window")
        return _result(classification, policy=policy, labels=labels, n_rows=n,
                       warmup=warmup, limitations=limitations)
    except Exception as exc:  # defensive: never raise
        return _result(INVALID, policy=policy if isinstance(policy, dict) else {},
                       reason=f"regime classification error: {type(exc).__name__}")


def align_labels_to_execution(labels: List[str], *,
                              execution: str = "bar_t_plus_1_open") -> List[str]:
    """Align per-bar regime labels to a legal EXECUTION timeline.

    The label for bar ``t`` is computed from bar ``t``'s completed high/low/close, so it is only
    known after bar ``t`` closes and may NOT drive an order at bar ``t``'s open. This helper
    returns a list ``exec_labels`` where ``exec_labels[i]`` is the regime label that may legally
    drive an order executed at bar ``i``'s open — i.e. the label of the most recently CLOSED bar,
    ``labels[i-1]``. ``exec_labels[0]`` is ``NOT_AVAILABLE`` (no prior closed bar). This one-bar
    forward shift makes same-bar execution look-ahead structurally impossible for consumers.

    Only ``bar_t_plus_1_open`` execution is permitted; ``bar_t_open`` and intrabar execution are
    prohibited by the frozen policy and raise ``ValueError``.
    """
    if execution == "bar_t_open" or execution == "intrabar_before_bar_t_close":
        raise ValueError(f"execution timing {execution!r} is prohibited: a bar-t label cannot "
                         "act at bar-t open or intrabar (same-bar look-ahead)")
    if execution != "bar_t_plus_1_open":
        raise ValueError(f"unsupported execution timing {execution!r}")
    if not isinstance(labels, list):
        raise TypeError("labels must be a list")
    if not labels:
        return []
    return [LABEL_NA] + list(labels[:-1])
