"""Deterministic frozen manifest for a shadow-forward (or validation-forward) experiment.

This EXTENDS the locked forward manifest (app/forward/manifest.py) additively — it reuses
the same canonical JSON and the same code / parameter / file hashes. The experiment id
and the manifest hash are derived from the COMPLETE canonical frozen definition (every
frozen field except the id itself and genuinely mutable provenance), so any change to any
frozen field — thresholds, costs, start time, expected ranges, required regimes, … —
yields a different id.

Old `forward-v1` manifests remain readable and unchanged; this is a new, separate
`shadow-forward-v1` document.

The frozen manifest stores only `initial_lifecycle_status`. The CURRENT status is derived
from the verified event ledger (see lifecycle.derive_current_status) — a legitimate
transition never rewrites the frozen manifest. Any frozen-field drift → INVALIDATED, with
all history preserved.

Research and simulation only — no broker, no real orders, no private keys.
"""
from __future__ import annotations

import inspect
import math
from typing import Dict, List, Optional, Sequence

from app.forward.manifest import (
    canonical_json,
    code_hash,
    git_commit_sha,
    param_hash,
    sha256_file,
    sha256_text,
)
from app.forward.shadow.lifecycle import (
    STATUSES,
    is_valid_status,
    mode_status_compatible,
)

SHADOW_MANIFEST_VERSION = "shadow-forward-v1"
RANGE_METHOD_VERSION = "block-bootstrap-v1"
REGIME_METHOD_VERSION = "regime-v1"

DEFAULT_CHECKPOINTS: List[int] = [30, 60, 90]
DEFAULT_MIN_TRADES = 30
DEFAULT_MIN_CALENDAR_DAYS = 60
DEFAULT_REQUIRED_REGIMES: List[str] = ["trend", "range"]

# fields excluded from the frozen identity: the id itself, and provenance that does not
# define the experiment (the git commit is recorded but must not change the id).
_ID_EXCLUDED = {"forward_test_id", "git_commit_sha"}


class ManifestError(ValueError):
    """Raised when a manifest definition is invalid."""


def default_position_limits(strategy) -> Dict:
    direction = getattr(strategy, "direction", "long_short")
    return {"min": -1 if direction == "long_short" else 0, "max": 1,
            "max_gross_exposure": 1.0, "direction": direction}


def canonical_strategy_files(strategy) -> List[Dict]:
    """Frozen strategy-file identity that is STABLE across working directories.

    forward-v1's strategy_files() records a working-directory-relative path, which would
    make forward_test_id depend on the process CWD. Here we instead identify each file by a
    stable ROLE plus its dotted MODULE name plus the SHA-256 of its contents — no
    filesystem path enters the identity. Sorted by role for determinism."""
    out: List[Dict] = []
    cls = type(strategy)
    try:
        src = inspect.getsourcefile(cls)
        if src:
            out.append({"role": "strategy_class_source", "module": cls.__module__,
                        "sha256": sha256_file(src)})
    except Exception:
        pass
    try:
        from app.strategy_research.strategies import base as _base
        bsrc = inspect.getsourcefile(_base)
        if bsrc:
            out.append({"role": "strategy_base", "module": _base.__name__,
                        "sha256": sha256_file(bsrc)})
    except Exception:
        pass
    return sorted(out, key=lambda d: d["role"])


def risk_rule_hash(strategy, params: Optional[Dict] = None, *,
                   position_limits: Optional[Dict] = None,
                   absolute_stop_thresholds: Optional[Dict] = None,
                   risk_rules: Optional[Dict] = None) -> str:
    """Hash the ACTUAL frozen risk configuration: direction, the supplied position limits,
    the risk-relevant parameters, any extra risk rules, and the absolute safety-stop
    thresholds. Not merely hard-coded defaults — whatever is frozen is hashed."""
    limits = position_limits if position_limits is not None else default_position_limits(strategy)
    rules = {
        "direction": getattr(strategy, "direction", "long_short"),
        "position_limits": limits,
        "params": dict(params if params is not None else getattr(strategy, "default_params", {}) or {}),
        "risk_rules": dict(risk_rules or {}),
        "absolute_stop_thresholds": dict(absolute_stop_thresholds or {}),
    }
    return sha256_text(canonical_json(rules))


def _finite_numbers(obj) -> bool:
    """True iff every numeric leaf in obj is finite (rejects NaN/inf thresholds)."""
    if isinstance(obj, bool):
        return True
    if isinstance(obj, (int, float)):
        return math.isfinite(obj)
    if isinstance(obj, dict):
        return all(_finite_numbers(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return all(_finite_numbers(v) for v in obj)
    return True


def _validate(*, strategy, test_mode: str, initial_lifecycle_status: str,
              assets: Sequence[str], timeframe: str, data_cutoff: str, start_time: str,
              cost_bps: float, slippage_bps: float, checkpoints: Sequence[int],
              min_calendar_days: int, min_trades: int, required_regimes: Sequence[str],
              warning_thresholds: Dict, absolute_stop_thresholds: Dict,
              expected_ranges: Dict, position_limits: Dict) -> None:
    if not getattr(strategy, "strategy_id", None):
        raise ManifestError("strategy identity (strategy_id) is required")
    if test_mode not in {"shadow", "validation"}:
        raise ManifestError(f"test_mode must be 'shadow' or 'validation', got {test_mode!r}")
    if not is_valid_status(initial_lifecycle_status):
        raise ManifestError(f"initial_lifecycle_status {initial_lifecycle_status!r} not in "
                            f"the lifecycle model {sorted(STATUSES)}")
    if not mode_status_compatible(test_mode, initial_lifecycle_status):
        raise ManifestError(f"mode {test_mode!r} is incompatible with initial status "
                            f"{initial_lifecycle_status!r}")
    # assets: non-empty, unique, no blanks
    alist = list(assets)
    if not alist:
        raise ManifestError("at least one asset is required")
    if any((not isinstance(a, str)) or (not a.strip()) for a in alist):
        raise ManifestError("assets must be non-blank strings")
    if len(set(alist)) != len(alist):
        raise ManifestError(f"assets must be unique, got {alist}")
    if not timeframe or not data_cutoff or not start_time:
        raise ManifestError("timeframe, data_cutoff and start_time are required")
    cps = list(checkpoints)
    if not cps or any((not isinstance(c, int)) or isinstance(c, bool) or c <= 0 for c in cps):
        raise ManifestError(f"checkpoints must be positive integers, got {cps}")
    if cps != sorted(cps) or len(set(cps)) != len(cps):
        raise ManifestError(f"checkpoints must be sorted and unique, got {cps}")
    if min_calendar_days < 0 or min_trades < 0:
        raise ManifestError("min_calendar_days and min_trades must be non-negative")
    # costs / slippage: finite AND non-negative
    for name, val in (("cost_bps", cost_bps), ("slippage_bps", slippage_bps)):
        if not math.isfinite(float(val)) or float(val) < 0:
            raise ManifestError(f"{name} must be finite and non-negative, got {val!r}")
    for name, thr in (("warning_thresholds", warning_thresholds),
                      ("absolute_stop_thresholds", absolute_stop_thresholds),
                      ("expected_ranges", expected_ranges),
                      ("position_limits", position_limits)):
        if not _finite_numbers(thr):
            raise ManifestError(f"{name} contains a non-finite numeric value")
    # required regimes: list, unique, non-empty, non-blank
    if not isinstance(required_regimes, (list, tuple)) or not list(required_regimes):
        raise ManifestError("required_regimes must be a non-empty list")
    rr = list(required_regimes)
    if any((not isinstance(r, str)) or (not r.strip()) for r in rr):
        raise ManifestError("required_regimes must be non-blank strings")
    if len(set(rr)) != len(rr):
        raise ManifestError(f"required_regimes must be unique, got {rr}")


def build_shadow_manifest(
    *,
    strategy,
    params: Dict,
    assets: Sequence[str],
    timeframe: str,
    data_cutoff: str,
    start_time: str,
    test_mode: str = "shadow",
    initial_lifecycle_status: str = "SHADOW",
    cost_bps: float,
    slippage_bps: float,
    position_limits: Optional[Dict] = None,
    risk_rules: Optional[Dict] = None,
    expected_ranges: Optional[Dict] = None,
    warning_thresholds: Optional[Dict] = None,
    absolute_stop_thresholds: Optional[Dict] = None,
    checkpoints: Optional[Sequence[int]] = None,
    min_calendar_days: int = DEFAULT_MIN_CALENDAR_DAYS,
    min_trades: int = DEFAULT_MIN_TRADES,
    required_regimes: Optional[Sequence[str]] = None,
    reference_evidence_hash: Optional[str] = None,
    parent_batch_hash: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> Dict:
    """Assemble the deterministic frozen manifest. `forward_test_id` and `manifest_hash`
    are derived from the COMPLETE canonical frozen definition."""
    checkpoints = list(checkpoints if checkpoints is not None else DEFAULT_CHECKPOINTS)
    required_regimes = list(required_regimes if required_regimes is not None
                            else DEFAULT_REQUIRED_REGIMES)
    expected_ranges = expected_ranges or {}
    warning_thresholds = warning_thresholds or {}
    absolute_stop_thresholds = absolute_stop_thresholds or {}
    limits = position_limits or default_position_limits(strategy)

    _validate(strategy=strategy, test_mode=test_mode,
              initial_lifecycle_status=initial_lifecycle_status, assets=assets,
              timeframe=timeframe, data_cutoff=data_cutoff, start_time=start_time,
              cost_bps=cost_bps, slippage_bps=slippage_bps, checkpoints=checkpoints,
              min_calendar_days=min_calendar_days, min_trades=min_trades,
              required_regimes=required_regimes, warning_thresholds=warning_thresholds,
              absolute_stop_thresholds=absolute_stop_thresholds,
              expected_ranges=expected_ranges, position_limits=limits)

    ch = code_hash(strategy)
    ph = param_hash(params)
    rh = risk_rule_hash(strategy, params, position_limits=limits,
                        absolute_stop_thresholds=absolute_stop_thresholds,
                        risk_rules=risk_rules)
    files = canonical_strategy_files(strategy)
    if not ch or not ph or not rh:
        raise ManifestError("code, parameter and risk-rule hashes are all required")

    # Build every frozen field EXCEPT forward_test_id; then derive the id + hash from the
    # complete canonical frozen definition (git_commit_sha excluded from the identity).
    manifest: Dict = {
        "manifest_version": SHADOW_MANIFEST_VERSION,
        "test_mode": test_mode,
        "initial_lifecycle_status": initial_lifecycle_status,
        "strategy_id": strategy.strategy_id,
        "strategy_name": getattr(strategy, "name", strategy.strategy_id),
        "strategy_version": ch[:12],
        "code_hash": ch,
        "params": dict(params or {}),
        "param_hash": ph,
        "risk_rule_hash": rh,
        "risk_rules": dict(risk_rules or {}),
        "strategy_files": files,
        "data_cutoff": data_cutoff,
        "start_time": start_time,
        "assets": sorted(assets),
        "timeframe": timeframe,
        "cost_bps": float(cost_bps),
        "slippage_bps": float(slippage_bps),
        "position_limits": limits,
        "signal_timing": "close_of_bar",
        "execution_delay": "next_bar_open",
        "checkpoints": checkpoints,
        "min_calendar_days": int(min_calendar_days),
        "min_trades": int(min_trades),
        "required_regimes": sorted(required_regimes),
        "expected_ranges": expected_ranges,
        "range_method": RANGE_METHOD_VERSION,
        "regime_method": REGIME_METHOD_VERSION,
        "warning_thresholds": warning_thresholds,
        "absolute_stop_thresholds": absolute_stop_thresholds,
        "reference_evidence_hash": reference_evidence_hash,
        "parent_batch_hash": parent_batch_hash,
        "git_commit_sha": git_commit_sha(repo_root),
    }
    digest = sha256_text(canonical_json({k: v for k, v in manifest.items()
                                         if k not in _ID_EXCLUDED}))
    manifest["forward_test_id"] = "SF-" + strategy.strategy_id + "-" + digest[:12]
    return manifest


def manifest_hash(manifest: Dict) -> str:
    """Deterministic hash of the COMPLETE frozen definition (all fields except the id and
    git provenance). Matches the digest used to derive forward_test_id."""
    return sha256_text(canonical_json({k: v for k, v in manifest.items()
                                       if k not in _ID_EXCLUDED}))


def frozen_field_drift(manifest: Dict, strategy, params: Optional[Dict] = None, *,
                       position_limits: Optional[Dict] = None,
                       absolute_stop_thresholds: Optional[Dict] = None,
                       risk_rules: Optional[Dict] = None) -> Dict:
    """Recompute the decisive freeze hashes and report drift. Any change → the caller must
    move the experiment to INVALIDATED while preserving all history."""
    changes: List[Dict] = []
    cur_code = code_hash(strategy)
    if cur_code != manifest.get("code_hash"):
        changes.append({"field": "code_hash", "frozen": manifest.get("code_hash"), "current": cur_code})
    cur_param = param_hash(params if params is not None else getattr(strategy, "default_params", {}) or {})
    if cur_param != manifest.get("param_hash"):
        changes.append({"field": "param_hash", "frozen": manifest.get("param_hash"), "current": cur_param})
    cur_risk = risk_rule_hash(
        strategy, params,
        position_limits=position_limits if position_limits is not None else manifest.get("position_limits"),
        absolute_stop_thresholds=absolute_stop_thresholds
        if absolute_stop_thresholds is not None else manifest.get("absolute_stop_thresholds"),
        risk_rules=risk_rules if risk_rules is not None else manifest.get("risk_rules"))
    if cur_risk != manifest.get("risk_rule_hash"):
        changes.append({"field": "risk_rule_hash", "frozen": manifest.get("risk_rule_hash"), "current": cur_risk})

    # strategy file SHA-256 drift (by stable role, CWD-independent). Compare the COMPLETE
    # role->hash mappings in BOTH directions so a changed hash, a missing role, an
    # unexpected new role, or a duplicate frozen role are all detected.
    frozen_list = manifest.get("strategy_files", []) or []
    frozen_roles = [f.get("role") for f in frozen_list]
    for role in sorted({r for r in frozen_roles if frozen_roles.count(r) > 1},
                       key=lambda x: (x is None, x)):
        changes.append({"field": "strategy_file", "role": role,
                        "issue": "duplicate_frozen_role"})
    frozen_map = {f.get("role"): f.get("sha256") for f in frozen_list}
    current_map = {f["role"]: f["sha256"] for f in canonical_strategy_files(strategy)}
    for role in sorted(set(frozen_map) | set(current_map), key=lambda x: (x is None, x)):
        fz, cur = frozen_map.get(role), current_map.get(role)
        if role not in current_map:
            changes.append({"field": "strategy_file", "role": role,
                            "issue": "missing_current_role", "frozen": fz, "current": None})
        elif role not in frozen_map:
            changes.append({"field": "strategy_file", "role": role,
                            "issue": "unexpected_current_role", "frozen": None, "current": cur})
        elif fz != cur:
            changes.append({"field": "strategy_file", "role": role,
                            "issue": "hash_changed", "frozen": fz, "current": cur})
    return {"ok": not changes, "changes": changes}


# required top-level fields every shadow manifest must carry
_REQUIRED_FIELDS = (
    "manifest_version", "forward_test_id", "test_mode", "initial_lifecycle_status",
    "strategy_id", "code_hash", "param_hash", "risk_rule_hash", "strategy_files",
    "data_cutoff", "start_time", "assets", "timeframe", "cost_bps", "slippage_bps",
    "position_limits", "checkpoints", "min_calendar_days", "min_trades",
    "required_regimes", "expected_ranges", "warning_thresholds", "absolute_stop_thresholds",
)


def verify_manifest_integrity(manifest: Dict) -> Dict:
    """Validate a shadow manifest against itself. Because forward_test_id is excluded from
    the frozen digest, this recomputes the digest, reconstructs the expected id and compares
    it to the stored id — so changing ONLY forward_test_id is detected. Also checks the
    manifest version, required fields and required file hashes.
    Returns {ok, errors:[{kind, detail}]}."""
    errors: List[Dict] = []
    if manifest.get("manifest_version") != SHADOW_MANIFEST_VERSION:
        errors.append({"kind": "manifest_version",
                       "detail": f"expected {SHADOW_MANIFEST_VERSION}, got "
                                 f"{manifest.get('manifest_version')!r}"})
    missing = [f for f in _REQUIRED_FIELDS if f not in manifest]
    if missing:
        errors.append({"kind": "missing_fields", "detail": f"missing {missing}"})

    files = manifest.get("strategy_files") or []
    if not files:
        errors.append({"kind": "missing_file_hashes", "detail": "no strategy file hashes"})
    for f in files:
        if not f.get("sha256") or not f.get("role"):
            errors.append({"kind": "invalid_file_hash", "detail": f"{f}"})

    sid = manifest.get("strategy_id")
    stored_id = manifest.get("forward_test_id")
    if sid and stored_id:
        digest = manifest_hash(manifest)
        expected_id = "SF-" + sid + "-" + digest[:12]
        if expected_id != stored_id:
            errors.append({"kind": "forward_test_id_mismatch",
                           "detail": f"expected {expected_id}, stored {stored_id} "
                                     f"(forward_test_id was altered or a frozen field drifted)"})
    return {"ok": not errors, "errors": errors}
