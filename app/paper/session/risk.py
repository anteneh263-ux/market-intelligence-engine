"""Frozen absolute-risk controls for a paper session.

Limits are frozen before the session starts and hashed into the manifest. The evaluator is
pure: it reports breaches, it does not act. The engine acts on a hard breach by blocking new
entries, permitting only the frozen safe-exit, setting ``absolute_risk.breached = True``,
writing append-only evidence and moving the session to RISK_STOPPED (human approval required
to restart).

The system never weakens its own limits: there is no code path that raises a limit or relaxes
a breach, and ``freeze_risk_limits`` only *tightens*-checkable, positive values.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

from app.forward.manifest import canonical_json, sha256_text

RISK_VERSION = "abs-risk-v1"


def _pos(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0


def _pos_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _nonneg_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def freeze_risk_limits(
    *,
    max_position_gross: float = 1.0,     # max |position notional| / equity
    max_leverage: float = 1.0,           # max gross exposure / equity
    max_loss_per_trade: float = 0.05,    # fraction of capital lost on a single closed trade
    max_daily_loss: float = 0.05,        # fraction of capital lost within one session-day
    max_session_loss: float = 0.20,      # fraction of initial capital lost over the session
    max_drawdown: float = 0.10,          # peak-to-trough equity drawdown fraction
    max_missing_bars: int = 1,           # consecutive missing/stale bars tolerated
    stale_data_tolerance_ms: int = 172_800_000,  # 2 days: max gap before data is 'stale'
    max_consecutive_errors: int = 3,     # consecutive execution/data errors tolerated
) -> Dict:
    """Freeze the absolute-risk limits. All values must be positive/finite."""
    fracs = {
        "max_position_gross": max_position_gross, "max_leverage": max_leverage,
        "max_loss_per_trade": max_loss_per_trade, "max_daily_loss": max_daily_loss,
        "max_session_loss": max_session_loss, "max_drawdown": max_drawdown,
    }
    for k, v in fracs.items():
        if not _pos(v):
            raise ValueError(f"{k} must be a finite positive number")
    if not _nonneg_int(max_missing_bars):
        raise ValueError("max_missing_bars must be a non-negative int")
    if not _pos_int(stale_data_tolerance_ms):
        raise ValueError("stale_data_tolerance_ms must be a positive int")
    if not _nonneg_int(max_consecutive_errors):
        raise ValueError("max_consecutive_errors must be a non-negative int")
    return {
        "risk_version": RISK_VERSION,
        **{k: float(v) for k, v in fracs.items()},
        "max_missing_bars": int(max_missing_bars),
        "stale_data_tolerance_ms": int(stale_data_tolerance_ms),
        "max_consecutive_errors": int(max_consecutive_errors),
    }


def risk_limits_hash(limits: Dict) -> str:
    return sha256_text(canonical_json(limits))


def validate_risk_limits(limits) -> Tuple[bool, str]:
    if not isinstance(limits, dict) or limits.get("risk_version") != RISK_VERSION:
        return False, "unsupported/malformed risk limits"
    for k in ("max_position_gross", "max_leverage", "max_loss_per_trade", "max_daily_loss",
              "max_session_loss", "max_drawdown"):
        if not _pos(limits.get(k)):
            return False, f"{k} must be positive"
    if not _pos_int(limits.get("stale_data_tolerance_ms")):
        return False, "stale_data_tolerance_ms must be a positive int"
    for k in ("max_missing_bars", "max_consecutive_errors"):
        if not _nonneg_int(limits.get(k)):
            return False, f"{k} must be a non-negative int"
    return True, ""


def evaluate_risk(state: Dict, limits: Dict) -> List[Dict]:
    """Return a list of hard breaches (possibly empty). ``state`` carries observed values:
    gross_exposure_frac, leverage, drawdown, session_loss_frac, daily_loss_frac,
    last_trade_loss_frac, consecutive_missing_bars, data_gap_ms, consecutive_errors.

    Each breach is {rule, observed, limit, message}. Pure — it never mutates anything.
    """
    breaches: List[Dict] = []

    def _check(rule: str, observed, limit, over=True):
        if observed is None:
            return
        if (over and observed > limit) or (not over and observed < limit):
            breaches.append({"rule": rule, "observed": float(observed), "limit": float(limit),
                             "message": f"{rule} {observed} breached frozen limit {limit}"})

    _check("max_position_gross", state.get("gross_exposure_frac"), limits["max_position_gross"])
    _check("max_leverage", state.get("leverage"), limits["max_leverage"])
    _check("max_drawdown", state.get("drawdown"), limits["max_drawdown"])
    _check("max_session_loss", state.get("session_loss_frac"), limits["max_session_loss"])
    _check("max_daily_loss", state.get("daily_loss_frac"), limits["max_daily_loss"])
    _check("max_loss_per_trade", state.get("last_trade_loss_frac"), limits["max_loss_per_trade"])

    cmb = state.get("consecutive_missing_bars")
    if isinstance(cmb, int) and cmb > limits["max_missing_bars"]:
        breaches.append({"rule": "max_missing_bars", "observed": cmb,
                         "limit": limits["max_missing_bars"],
                         "message": f"{cmb} consecutive missing bars > {limits['max_missing_bars']}"})
    gap = state.get("data_gap_ms")
    if isinstance(gap, int) and gap > limits["stale_data_tolerance_ms"]:
        breaches.append({"rule": "stale_data_tolerance_ms", "observed": gap,
                         "limit": limits["stale_data_tolerance_ms"],
                         "message": f"data gap {gap}ms > stale tolerance {limits['stale_data_tolerance_ms']}ms"})
    errs = state.get("consecutive_errors")
    if isinstance(errs, int) and errs > limits["max_consecutive_errors"]:
        breaches.append({"rule": "max_consecutive_errors", "observed": errs,
                         "limit": limits["max_consecutive_errors"],
                         "message": f"{errs} consecutive errors > {limits['max_consecutive_errors']}"})
    return breaches
