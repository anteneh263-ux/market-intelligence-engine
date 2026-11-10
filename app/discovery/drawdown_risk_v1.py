"""Read-only, conditional drawdown diagnostic; no selection or verdict authority.

Rows are consecutive OOS periods; columns are synchronized asset net simple
returns. Moving blocks never wrap. Portfolio weights are fixed, rebalanced each
period. Costs must already be included in returns. No stores or backtests are
created by this module.
"""
from __future__ import annotations

import hashlib
import json
from numbers import Integral

import numpy as np

VERSION = "drawdown-risk-v1"


def _integer(value, name, minimum):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _max_drawdown(returns):
    # Include starting equity, so a loss in the first period is counted.
    if np.any(returns == -1):
        return 1.0
    log_equity = np.r_[0.0, np.cumsum(np.log1p(returns))]
    if not np.all(np.isfinite(log_equity)):
        raise ValueError("Equity calculation exceeded finite numeric range")
    return float(np.max(-np.expm1(log_equity - np.maximum.accumulate(log_equity))))


def drawdown_report(net_returns, *, times, block_lengths, provenance,
                    weights=None, simulations=5000, seed=0, horizon=None,
                    drawdown_limit=None):
    """Return a JSON-compatible risk report, without writing or changing state.

    `times`: strictly increasing numeric timestamps with no missing periods;
    callers must verify spacing/calendar and OOS provenance. `provenance` must
    name strategy, matrix, dataset, and cost assumptions. These are caller
    declarations, not independently verified evidence. Limits are descriptive,
    never pass/fail rules. All values use fractions, not percentage points.
    """
    a = np.asarray(net_returns, dtype=float)
    if a.ndim == 1:
        a = a[:, None]
    if a.ndim != 2 or a.shape[0] < 2 or a.shape[1] < 1:
        raise ValueError("Need >=2 periods and >=1 asset")
    if not np.all(np.isfinite(a)) or np.any(a < -1):
        raise ValueError("Returns must be finite simple returns >= -1")
    t = np.asarray(times, dtype=float)
    if t.ndim != 1 or t.size != a.shape[0] or not np.all(np.isfinite(t)) or not np.all(np.diff(t) > 0):
        raise ValueError("Need finite, strictly increasing timestamps aligned to returns")
    n, assets = a.shape
    if weights is None:
        if assets != 1:
            raise ValueError("Explicit portfolio weights required for multiple assets")
        weights = [1.0]
    w = np.asarray(weights, dtype=float)
    if w.shape != (assets,) or not np.all(np.isfinite(w)) or np.any(w < 0) or not np.isclose(w.sum(), 1.0, rtol=0, atol=1e-12):
        raise ValueError("Weights must be finite, nonnegative and sum to one")
    simulations = _integer(simulations, "simulations", 1)
    seed = _integer(seed, "seed", 0)
    horizon = n if horizon is None else _integer(horizon, "horizon", 2)
    lengths = [_integer(x, "block length", 1) for x in block_lengths]
    if not lengths or len(set(lengths)) != len(lengths) or max(lengths) > n:
        raise ValueError("Block lengths must be unique, nonempty and <= observed periods")
    if not isinstance(provenance, dict) or any(
        not isinstance(provenance.get(k), str) or not provenance[k].strip()
        for k in ("strategy", "matrix", "dataset", "costs")
    ):
        raise ValueError("Declare strategy, matrix, dataset and costs as nonempty strings")
    if drawdown_limit is not None:
        if isinstance(drawdown_limit, bool) or not np.isfinite(drawdown_limit) or not 0 <= drawdown_limit <= 1:
            raise ValueError("Drawdown limit must be a fraction in [0, 1]")
        drawdown_limit = float(drawdown_limit)
    # Digest exact supplied values, distinct from upstream dataset identity.
    input_hash = hashlib.sha256()
    for values in (a, t, w):
        input_hash.update(str(values.shape).encode())
        input_hash.update(np.asarray(values, dtype="<f8").tobytes())
    portfolio = a @ w
    scenarios = []
    for length in lengths:
        rng = np.random.default_rng(np.random.SeedSequence([seed, length]))
        draws = np.empty(simulations)
        blocks = (horizon + length - 1) // length
        for i in range(simulations):
            starts = rng.integers(0, n - length + 1, size=blocks)
            indices = (starts[:, None] + np.arange(length)).ravel()[:horizon]
            # Same time indices apply to every asset, preserving cross-asset
            # dependence; aggregating first is equivalent for fixed weights.
            draws[i] = _max_drawdown(portfolio[indices])
        q = np.quantile(draws, [0.5, 0.95, 0.99], method="linear")
        scenario = {"block_length": length, "median_max_drawdown": float(q[0]),
                    "p95_max_drawdown": float(q[1]), "p99_max_drawdown": float(q[2])}
        if drawdown_limit is not None:
            count = int(np.count_nonzero(draws > drawdown_limit))
            scenario.update({"exceedance_count": count,
                             "exceedance_fraction": count / simulations})
        scenarios.append(scenario)
    return {
        "version": VERSION, "status": "diagnostic_only",
        "method": "noncircular_moving_block_bootstrap",
        "provenance": {k: provenance[k] for k in ("strategy", "matrix", "dataset", "costs")},
        "input_sha256": input_hash.hexdigest(),
        "observed_periods": n, "assets": assets, "weights": w.tolist(),
        "time_start": float(t[0]), "time_end": float(t[-1]),
        "horizon_periods": horizon, "simulations": simulations, "seed": seed,
        "quantile_method": "linear", "drawdown_limit": drawdown_limit,
        "observed_max_drawdown": _max_drawdown(portfolio), "scenarios": scenarios,
        "limitations": [
            "Conditional on supplied OOS net returns; OOS status and costs are caller declarations.",
            "Does not correct candidate selection, verify DSR trial accounting, or establish profitability.",
            "Resampling cannot create unobserved crises; dependence beyond block length is lost.",
            "Fixed weights imply period rebalancing; additional rebalancing costs are not modeled.",
            "Quantiles and exceedance fractions are simulation estimates, not future guarantees.",
            "Caller must verify synchronized calendars and missing periods; no rows are silently dropped.",
        ],
    }


def render_report(report):
    """Render a reviewable text report; caller owns any explicit file export."""
    return json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
