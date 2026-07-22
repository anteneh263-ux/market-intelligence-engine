"""Deterministic locked manifest for a forward test.

When a forward test starts, the strategy's code, parameters and risk rules are frozen
into a canonical-JSON manifest. Re-hashing the same inputs must reproduce the same
manifest and forward_test_id. All timestamps are UTC; the manifest never depends on a
wall clock except through the explicitly-passed start date.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
from typing import Dict, List, Optional, Sequence

MANIFEST_VERSION = "forward-v1"

# pre-defined default evaluation thresholds (locked into the manifest; never chosen
# after the test starts). Overridable at start() time, but frozen once written.
DEFAULT_THRESHOLDS: Dict[str, float] = {
    "min_observations": 60,     # bars before evaluation can PASS/FAIL (else EXTEND)
    "min_active_days": 40,      # bars with a non-zero position
    "min_trades": 10,           # position changes
    "max_drawdown": 0.60,       # critical: forward OOS DD must stay within this
    "sharpe_floor": 0.0,        # critical: forward per-period Sharpe must be >= this
    "reference_retention": 0.30,  # forward Sharpe must be >= this * reference Sharpe
}


def canonical_json(obj) -> str:
    """Deterministic, canonical JSON (sorted keys, no incidental whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def code_hash(strategy) -> str:
    """SHA-256 of the strategy CLASS source - the decisive 'did the code change?' hash."""
    return sha256_text(inspect.getsource(strategy.__class__))


def param_hash(params: Optional[Dict]) -> str:
    return sha256_text(canonical_json(params or {}))


def strategy_files(strategy) -> List[Dict[str, Optional[str]]]:
    """The source file that defines the strategy + the Strategy base, each with SHA-256."""
    files: List[str] = []
    try:
        files.append(inspect.getsourcefile(strategy.__class__) or "")
    except Exception:
        pass
    try:
        from app.strategy_research.strategies import base as _base
        files.append(inspect.getsourcefile(_base) or "")
    except Exception:
        pass
    out: List[Dict[str, Optional[str]]] = []
    for p in files:
        if p:
            out.append({"path": os.path.relpath(p) if os.path.exists(p) else p,
                        "sha256": sha256_file(p)})
    return out


def git_commit_sha(repo_root: Optional[str] = None) -> Optional[str]:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root or ".",
                             capture_output=True, text=True, timeout=5)
        sha = out.stdout.strip()
        return sha or None
    except Exception:
        return None


def _add_days_iso(start_date: str, days: int) -> str:
    from datetime import datetime, timedelta, timezone
    dt = datetime.strptime(start_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (dt + timedelta(days=int(days))).strftime("%Y-%m-%d")


def build_manifest(*, strategy, params: Dict, assets: Sequence[str], timeframe: str,
                   start_date: str, cost_bps: float, slippage_bps: float,
                   declared_inputs: Optional[Sequence[str]],
                   promotion_ref: Optional[str], backtest_ref: Optional[Dict],
                   oos_ref: Optional[Dict], thresholds: Optional[Dict] = None,
                   repo_root: Optional[str] = None) -> Dict:
    """Assemble the deterministic locked manifest. forward_test_id is a hash of the
    immutable core, so identical inputs -> identical id."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    ch = code_hash(strategy)
    ph = param_hash(params)
    direction = getattr(strategy, "direction", "long_short")
    position_limits = {"min": (-1 if direction == "long_short" else 0), "max": 1,
                       "max_gross_exposure": 1.0, "direction": direction}
    core = {
        "strategy_id": strategy.strategy_id,
        "code_hash": ch,
        "param_hash": ph,
        "assets": list(assets),
        "timeframe": timeframe,
        "start_date": start_date,
        "manifest_version": MANIFEST_VERSION,
    }
    ftid = "FT-" + strategy.strategy_id + "-" + sha256_text(canonical_json(core))[:12]
    return {
        "manifest_version": MANIFEST_VERSION,
        "forward_test_id": ftid,
        "strategy_id": strategy.strategy_id,
        "strategy_name": getattr(strategy, "name", strategy.strategy_id),
        "strategy_version": ch[:12],
        "status": "DRAFT",
        "start_date": start_date,
        "earliest_eval_date": _add_days_iso(start_date, th["min_observations"]),
        "strategy_files": strategy_files(strategy),
        "code_hash": ch,
        "params": dict(params or {}),
        "param_hash": ph,
        "declared_inputs": sorted(declared_inputs) if declared_inputs else None,
        "assets": list(assets),
        "timeframe": timeframe,
        "cost_bps": float(cost_bps),
        "slippage_bps": float(slippage_bps),
        "position_limits": position_limits,
        "signal_timing": "close_of_bar",
        "execution_delay": "next_bar_open",
        "promotion_report_ref": promotion_ref,
        "backtest_reference": backtest_ref,
        "oos_reference": oos_ref,
        "git_commit_sha": git_commit_sha(repo_root),
        "thresholds": th,
    }
