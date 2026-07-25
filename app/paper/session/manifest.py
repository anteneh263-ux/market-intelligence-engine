"""Frozen, immutable paper-session manifest.

A session is defined by an immutable manifest written once at creation. It pins every input
that could change the run — strategy identity, instrument, data source, capital, sizing,
execution profile, cost assumptions, market-hours policy, absolute-risk limits, the frozen
Shadow-Forward policy/range hashes and comparability basis, and the software version — plus
the two hard invariants ``paper_trading_only=True`` and ``live_execution_enabled=False``
(constants, never parameters, so live execution cannot be enabled through this manifest).

Re-hashing the manifest reproduces ``manifest_hash``; any drift after the session starts is
detectable with ``detect_manifest_drift`` and must invalidate/stop the session.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from app.forward.manifest import canonical_json, code_hash, param_hash, sha256_text
from app.paper.session import (
    LIVE_EXECUTION_ENABLED,
    PAPER_SESSION_VERSION,
    PAPER_TRADING_ONLY,
)
from app.paper.session.profiles import execution_profile_hash, validate_execution_profile
from app.paper.session.sanitizer import (
    freeze_signal_policy,
    resolve_max_target_abs,
    signal_policy_hash,
    validate_signal_policy,
)

MANIFEST_KIND = "paper_session_manifest"


def _risk_hash(limits: Dict) -> str:
    return sha256_text(canonical_json(limits))


# the canonical, frozen over-limit rule for Stage 1 (a clamped finite target may continue)
_STAGE1_OVER_LIMIT = "clamp"


def _frozen_signal_policy(position_sizing_policy: Dict, absolute_risk_limits: Dict,
                          shorting_allowed: bool) -> Dict:
    """Freeze the SignalPolicy from the frozen gross limits. max |target| is the tightest of the
    position-sizing max_gross and the absolute-risk max_position_gross / max_leverage."""
    max_target_abs = resolve_max_target_abs(
        position_sizing_policy.get("max_gross"),
        absolute_risk_limits.get("max_position_gross"),
        absolute_risk_limits.get("max_leverage"))
    return freeze_signal_policy(max_target_abs=max_target_abs, over_limit=_STAGE1_OVER_LIMIT,
                               shorting_allowed=bool(shorting_allowed))


def expected_signal_policy(manifest: Dict, absolute_risk_limits: Dict) -> Dict:
    """The SignalPolicy the runtime limits SHOULD reproduce — recomputed from the frozen rules
    (clamp over-limit, tightest gross limit, long-only). Used to bind runtime to the manifest."""
    return _frozen_signal_policy(manifest["position_sizing_policy"], absolute_risk_limits,
                                 bool(manifest.get("shorting_allowed", False)))


def build_session_manifest(
    *,
    strategy,
    params: Optional[Dict],
    asset: str,
    timeframe: str,
    data_source: Dict,
    start_timestamp_ms: int,
    initial_capital: float,
    position_sizing_policy: Dict,
    execution_profile: Dict,
    absolute_risk_limits: Dict,
    gap_stale_policy: Dict,
    regime_policy_hash: str,
    consistency_policy_hash: str,
    reference_range_hash: str,
    comparability_basis: Dict,
    session_id: Optional[str] = None,
    code_version: str = PAPER_SESSION_VERSION,
    dataset_hash: Optional[str] = None,
) -> Dict:
    """Build the immutable session manifest. Fails closed (ValueError) on malformed inputs."""
    if not isinstance(asset, str) or not asset:
        raise ValueError("asset must be a non-empty string")
    if not isinstance(timeframe, str) or not timeframe:
        raise ValueError("timeframe must be a non-empty string")
    if not (isinstance(start_timestamp_ms, int) and not isinstance(start_timestamp_ms, bool)
            and start_timestamp_ms > 0):
        raise ValueError("start_timestamp_ms must be a positive int (UTC epoch ms)")
    if not (isinstance(initial_capital, (int, float)) and not isinstance(initial_capital, bool)
            and initial_capital > 0):
        raise ValueError("initial_capital must be a positive number")
    ok, why = validate_execution_profile(execution_profile)
    if not ok:
        raise ValueError(f"execution_profile invalid: {why}")
    # Stage 1 is hard-locked to long-only: reject any strategy/session that would allow shorting
    if getattr(strategy, "direction", "long_short") != "long_only":
        raise ValueError("Stage 1 is long-only; a strategy requesting shorting is not permitted "
                         "(short-margin/leverage accounting is a separately-reviewed future phase)")
    for name, val in (("data_source", data_source), ("position_sizing_policy", position_sizing_policy),
                      ("absolute_risk_limits", absolute_risk_limits), ("gap_stale_policy", gap_stale_policy),
                      ("comparability_basis", comparability_basis)):
        if not isinstance(val, dict) or not val:
            raise ValueError(f"{name} must be a non-empty dict")
    for name, val in (("regime_policy_hash", regime_policy_hash),
                      ("consistency_policy_hash", consistency_policy_hash),
                      ("reference_range_hash", reference_range_hash)):
        if not (isinstance(val, str) and val):
            raise ValueError(f"{name} must be a non-empty string")

    body = {
        "kind": MANIFEST_KIND,
        "manifest_version": PAPER_SESSION_VERSION,
        "software_version": code_version,
        # --- hard invariants (constants, never parameters) --------------------------- #
        "paper_trading_only": PAPER_TRADING_ONLY,
        "live_execution_enabled": LIVE_EXECUTION_ENABLED,
        # --- strategy identity ------------------------------------------------------- #
        "strategy": {
            "strategy_id": getattr(strategy, "strategy_id", "unknown"),
            "version": getattr(strategy, "version", getattr(strategy, "strategy_class", "unknown")),
            "strategy_hash": code_hash(strategy),
            "direction": getattr(strategy, "direction", "long_short"),
        },
        # execution/strategy contract: shorting is only allowed for a long_short strategy
        "shorting_allowed": getattr(strategy, "direction", "long_short") != "long_only",
        "dataset_hash": dataset_hash,   # None when not bound to a specific dataset
        # frozen signal-sanitisation policy (max |target| from ALL gross limits) -----------
        "signal_policy": _frozen_signal_policy(position_sizing_policy, absolute_risk_limits,
                                               getattr(strategy, "direction", "long_short") != "long_only"),
        "params": dict(params or {}),
        "param_hash": param_hash(params),
        # --- instrument / data ------------------------------------------------------- #
        "asset": asset,
        "instrument_type": execution_profile["instrument_type"],
        "timeframe": timeframe,
        "data_source": dict(data_source),
        "start_timestamp_ms": int(start_timestamp_ms),
        # --- capital / sizing / execution -------------------------------------------- #
        "initial_capital": float(initial_capital),
        "position_sizing_policy": dict(position_sizing_policy),
        "execution_profile": dict(execution_profile),
        "execution_profile_hash": execution_profile_hash(execution_profile),
        "cost_assumptions": {
            "fee_model": execution_profile["fee_model"],
            "maker_fee_bps": execution_profile["maker_fee_bps"],
            "taker_fee_bps": execution_profile["taker_fee_bps"],
            "commission_per_unit": execution_profile["commission_per_unit"],
            "min_commission": execution_profile["min_commission"],
            "half_spread_bps": execution_profile["half_spread_bps"],
            "slippage_bps": execution_profile["slippage_bps"],
            "funding": execution_profile.get("funding"),
        },
        "market_hours_policy": execution_profile["market_hours"],
        # --- risk / data policy ------------------------------------------------------ #
        "absolute_risk_limits": dict(absolute_risk_limits),
        "absolute_risk_limits_hash": _risk_hash(absolute_risk_limits),
        "gap_and_stale_data_policy": dict(gap_stale_policy),
        # --- frozen Shadow-Forward basis --------------------------------------------- #
        "regime_policy_hash": regime_policy_hash,
        "consistency_policy_hash": consistency_policy_hash,
        "reference_range_hash": reference_range_hash,
        "comparability_basis": dict(comparability_basis),
    }

    body["signal_policy_hash"] = signal_policy_hash(body["signal_policy"])
    # session_id is derived from the frozen content when not supplied, so identical inputs
    # reproduce the same id (deterministic), without a wall clock.
    core_hash = sha256_text(canonical_json(body))
    body["session_id"] = session_id if session_id else f"ps-{core_hash[:16]}"
    body["manifest_hash"] = sha256_text(canonical_json(body))
    return body


def manifest_hash(manifest: Dict) -> str:
    """Recompute the manifest content hash over every field except ``manifest_hash``."""
    body = {k: v for k, v in manifest.items() if k != "manifest_hash"}
    return sha256_text(canonical_json(body))


def detect_manifest_drift(frozen: Dict) -> Tuple[bool, str]:
    """Return (drifted, reason). A manifest whose stored hash no longer reproduces has drifted
    and the session must be invalidated/stopped."""
    if not isinstance(frozen, dict) or "manifest_hash" not in frozen:
        return True, "manifest missing manifest_hash"
    if frozen.get("paper_trading_only") is not True or frozen.get("live_execution_enabled") is not False:
        return True, "manifest hard invariants violated (paper_trading_only / live_execution_enabled)"
    if frozen.get("shorting_allowed") is not False:
        return True, "Stage 1 is long-only; shorting_allowed must be False"
    # the frozen SignalPolicy must be well-formed and reproduce its own hash
    sp = frozen.get("signal_policy")
    ok_sp, why_sp = validate_signal_policy(sp)
    if not ok_sp:
        return True, f"signal_policy invalid: {why_sp}"
    if signal_policy_hash(sp) != frozen.get("signal_policy_hash"):
        return True, "signal_policy content hash does not reproduce signal_policy_hash (tampered)"
    if sp.get("shorting_allowed") is not False:
        return True, "signal_policy.shorting_allowed must be False in Stage 1"
    if manifest_hash(frozen) != frozen["manifest_hash"]:
        return True, "manifest content hash does not reproduce manifest_hash (tampered)"
    return False, ""
