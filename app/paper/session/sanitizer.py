"""Explicit signal sanitizer — every strategy target passes through this before it can become
``pending`` or reach the broker.

A raw strategy output is NEVER silently coerced to ``0.0``. It is explicitly classified:

    accepted   a finite real number within the frozen position/leverage policy
    clamped    a finite real number beyond the frozen |target| limit, under a ``clamp`` policy
    rejected   short target while shorting is disallowed, an over-limit target under a
               ``reject`` policy, or any non-real / non-finite / missing value
               (NaN, +/-inf, bool, str, list, dict, complex, None)

A rejected target is NOT acted on: the engine cancels any pending decision and fails closed to
``SIGNAL_STOPPED`` (it does not silently hold the previous target), so infinity / NaN can never
reach broker arithmetic. A clamped finite target under the frozen clamp policy may continue,
but the clamp is always journaled. Every non-accept decision yields a deterministic journal
event (``signal_rejected`` / ``signal_clamped``) with the original value in a safely-
serialisable (string) form, the normalised value, a reason code, the decision bar, the frozen
policy and the action taken.

The ``SignalPolicy`` is frozen into the session manifest (see manifest.py) and its hash is part
of the manifest hash, so a change to ``over_limit``, ``max_target_abs``, ``shorting_allowed`` or
the policy version is detectable and rejected (INVALIDATED) before any journal is opened.
"""
from __future__ import annotations

import math
import numbers
from typing import Dict, Tuple

from app.forward.manifest import canonical_json, sha256_text

SIGNAL_POLICY_VERSION = "signal-policy-v1"

ACCEPTED = "accepted"
CLAMPED = "clamped"
REJECTED = "rejected"

# reason codes
R_OK = "ok"
R_MISSING = "missing_value"
R_NON_FINITE = "non_finite_value"
R_INVALID_TYPE = "invalid_datatype"
R_SHORT_NOT_ALLOWED = "short_not_permitted"
R_ABOVE_LIMIT = "above_position_limit"


def freeze_signal_policy(*, max_target_abs: float = 1.0, over_limit: str = "clamp",
                         shorting_allowed: bool = False) -> Dict:
    """Freeze the signal-sanitisation policy. ``over_limit`` is 'clamp' or 'reject'."""
    if not (isinstance(max_target_abs, (int, float)) and not isinstance(max_target_abs, bool)
            and math.isfinite(max_target_abs) and max_target_abs > 0):
        raise ValueError("max_target_abs must be a finite positive number")
    if over_limit not in ("clamp", "reject"):
        raise ValueError("over_limit must be 'clamp' or 'reject'")
    return {
        "policy_version": SIGNAL_POLICY_VERSION,
        "max_target_abs": float(max_target_abs),
        "over_limit": over_limit,
        "shorting_allowed": bool(shorting_allowed),
    }


def signal_policy_hash(policy: Dict) -> str:
    """Deterministic hash of the frozen signal policy (drift-detectable)."""
    return sha256_text(canonical_json(policy))


def validate_signal_policy(policy) -> Tuple[bool, str]:
    """Structural validation of a frozen signal policy. Returns (ok, reason)."""
    if not isinstance(policy, dict):
        return False, "signal policy is not a dict"
    if policy.get("policy_version") != SIGNAL_POLICY_VERSION:
        return False, "unsupported signal policy version"
    if policy.get("over_limit") not in ("clamp", "reject"):
        return False, "over_limit must be 'clamp' or 'reject'"
    mta = policy.get("max_target_abs")
    if not (isinstance(mta, (int, float)) and not isinstance(mta, bool)
            and math.isfinite(mta) and mta > 0):
        return False, "max_target_abs must be a finite positive number"
    if not isinstance(policy.get("shorting_allowed"), bool):
        return False, "shorting_allowed must be a bool"
    return True, ""


def resolve_max_target_abs(*limits) -> float:
    """The tightest of the supplied frozen gross limits. Each must be finite and positive."""
    for v in limits:
        if not (isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(float(v)) and v > 0):
            raise ValueError("every gross limit must be a finite positive number")
    return float(min(float(v) for v in limits))


def _is_bool(v) -> bool:
    return isinstance(v, bool) or isinstance(v, numbers.Integral) and isinstance(v, bool)


def _safe_repr(v) -> str:
    """A safely serialisable string form of any original value (never emits NaN/inf tokens)."""
    try:
        return f"{type(v).__name__}:{v!r}"[:200]
    except Exception:
        return f"{type(v).__name__}:<unrepr>"


def sanitize_target(raw, *, policy: Dict, decision_bar: int) -> Tuple[Dict, "float|None"]:
    """Classify a raw strategy target. Returns (event_payload_or_none, normalized_value).

    ``normalized_value`` is a float when accepted/clamped, else None (the caller must NOT act on
    a rejected signal). ``event_payload_or_none`` is None only for a clean accept.
    """
    max_abs = float(policy["max_target_abs"])
    over_limit = policy["over_limit"]
    shorting = bool(policy["shorting_allowed"])

    def _event(status, reason, normalized):
        return {
            "status": status, "reason_code": reason,
            "original": _safe_repr(raw),
            "normalized": (float(normalized) if normalized is not None else None),
            "decision_bar": int(decision_bar),
            "policy": {"max_target_abs": max_abs, "over_limit": over_limit,
                       "shorting_allowed": shorting},
            "action": ("accept" if status == ACCEPTED else status),
        }

    # ---- type / finiteness gate (reject; never coerce to 0.0) ------------------------ #
    if raw is None:
        return _event(REJECTED, R_MISSING, None), None
    if isinstance(raw, bool):
        return _event(REJECTED, R_INVALID_TYPE, None), None
    if not isinstance(raw, numbers.Real):
        return _event(REJECTED, R_INVALID_TYPE, None), None
    val = float(raw)
    if not math.isfinite(val):
        return _event(REJECTED, R_NON_FINITE, None), None

    # ---- policy gates ---------------------------------------------------------------- #
    if val < 0 and not shorting:
        return _event(REJECTED, R_SHORT_NOT_ALLOWED, None), None
    if abs(val) > max_abs + 1e-12:
        if over_limit == "reject":
            return _event(REJECTED, R_ABOVE_LIMIT, None), None
        clamped = math.copysign(max_abs, val)
        return _event(CLAMPED, R_ABOVE_LIMIT, clamped), clamped

    return None, val  # clean accept
