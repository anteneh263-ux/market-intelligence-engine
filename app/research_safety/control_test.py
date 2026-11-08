"""Calibrated target-control test (version ``target-control-test-v1``).

Spec: docs/specs/SPEC-target-control-calibrated-v1.md.

Compares a candidate with a control on the SAME out-of-sample bars of the frozen
walk-forward, at the matrix headline cost. The quantity tested is

    dSR = equal-weight mean over assets of [annual net Sharpe(candidate) - annual net Sharpe(control)]

with a paired circular block bootstrap (after Ledoit & Wolf, 2008): the same resampled
block indices are applied to candidate and control and to every asset at once, so
their mutual correlation and the cross-asset correlation are kept.

This is a control comparison only. It is not a causal claim, not a profitability claim
and not a basis for trading. The legacy ``target_control.compare`` is unchanged.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

VERSION = "target-control-test-v1"
SPEC = "docs/specs/SPEC-target-control-calibrated-v1.md"

# Locked parameters (spec section 4). Changing any of them is a new version.
BLOCK_LENGTH = 10
N_BOOT = 2000
ALPHA = 0.05
PERIODS_PER_YEAR = 252.0
_CHUNK = 250  # bootstrap resamples processed per batch (memory only; no effect on results)


def _sr_ann(x: np.ndarray, ppy: float, axis: int = 0) -> np.ndarray:
    """Annualised Sharpe along ``axis``; a series with zero spread gives 0."""
    mu = x.mean(axis=axis)
    sd = x.std(axis=axis, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sr = np.where(sd > 0, mu / sd, 0.0)
    return sr * math.sqrt(ppy)


def delta_sr(cand: np.ndarray, ctrl: np.ndarray, ppy: float = PERIODS_PER_YEAR) -> Tuple[float, np.ndarray]:
    """Return (pooled dSR, per-asset dSR) for arrays shaped (T, A)."""
    cand = np.asarray(cand, dtype=float)
    ctrl = np.asarray(ctrl, dtype=float)
    if cand.shape != ctrl.shape or cand.ndim != 2:
        raise ValueError("candidate and control must both be shaped (T, A)")
    per_asset = _sr_ann(cand, ppy) - _sr_ann(ctrl, ppy)
    return float(per_asset.mean()), per_asset


def circular_block_indices(n: int, block: int, n_boot: int, rng: np.random.Generator) -> np.ndarray:
    """(n_boot, n) row indices of a circular block bootstrap with fixed block length."""
    if n < 2 or block < 1:
        raise ValueError("need at least 2 bars and block length >= 1")
    n_blocks = -(-n // block)
    starts = rng.integers(0, n, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    return idx.reshape(n_boot, -1)[:, :n]


def paired_test(cand: np.ndarray, ctrl: np.ndarray, *, seed: int,
                block_length: int = BLOCK_LENGTH, n_boot: int = N_BOOT,
                alpha: float = ALPHA, ppy: float = PERIODS_PER_YEAR) -> Dict:
    """One-sided test of H0: dSR <= 0 on aligned (T, A) net-return arrays."""
    cand = np.asarray(cand, dtype=float)
    ctrl = np.asarray(ctrl, dtype=float)
    point, per_asset = delta_sr(cand, ctrl, ppy)
    n = cand.shape[0]
    rng = np.random.default_rng(seed)
    idx = circular_block_indices(n, block_length, n_boot, rng)
    boot = np.empty(n_boot)
    for s in range(0, n_boot, _CHUNK):
        rows = idx[s:s + _CHUNK]
        cb, kb = cand[rows], ctrl[rows]                      # (b, T, A)
        boot[s:s + rows.shape[0]] = (_sr_ann(cb, ppy, axis=1) - _sr_ann(kb, ppy, axis=1)).mean(axis=1)
    lower = float(np.quantile(boot, alpha))
    upper = float(np.quantile(boot, 1.0 - alpha))
    if lower > 0:
        verdict, reason = "candidate_wins", f"lower {alpha:.0%} bound of dSR {lower:.3f} is above 0"
    elif upper < 0:
        verdict, reason = "control_wins", f"upper {1 - alpha:.0%} bound of dSR {upper:.3f} is below 0"
    else:
        verdict, reason = "inconclusive", f"dSR interval [{lower:.3f}, {upper:.3f}] contains 0"
    return {
        "version": VERSION,
        "spec": SPEC,
        "note": "Control comparison only - NOT a causal claim, not a profitability claim.",
        "estimand": "mean over assets of annual net Sharpe(candidate) - annual net Sharpe(control)",
        "method": "paired circular block bootstrap, joint across assets (after Ledoit & Wolf 2008)",
        "parameters": {"block_length": block_length, "n_boot": n_boot, "alpha": alpha,
                       "periods_per_year": ppy, "seed": seed},
        "n_bars": int(n), "n_assets": int(cand.shape[1]),
        "delta_sharpe_annual": round(point, 6),
        "delta_sharpe_annual_per_asset": [round(float(x), 6) for x in per_asset],
        "interval": [round(lower, 6), round(upper, 6)],
        "verdict": verdict,
        "verdict_reason": reason,
    }


def align_common_calendar(series: Mapping[str, Tuple[Sequence, Sequence, Sequence]]
                          ) -> Tuple[pd.DatetimeIndex, np.ndarray, np.ndarray, list]:
    """Align per-asset (dates, candidate_net, control_net) on the common calendar.

    Common dates are those present for every asset. Returns on dates outside it (e.g. crypto
    weekends) are compounded into the next common date (log returns summed); returns after the
    last common date are dropped. Returns (dates, candidate (T, A), control (T, A), assets)."""
    assets = list(series)
    if not assets:
        raise ValueError("no assets")
    frames = {}
    for a in assets:
        dates, c, k = series[a]
        idx = pd.DatetimeIndex(pd.to_datetime(dates)).normalize()
        if idx.has_duplicates:
            raise ValueError(f"{a}: more than one bar per calendar day; the test expects daily bars")
        c_arr, k_arr = np.asarray(c, float), np.asarray(k, float)
        if not (np.isfinite(c_arr).all() and np.isfinite(k_arr).all()) or (c_arr <= -1).any() or (k_arr <= -1).any():
            raise ValueError(f"{a}: net returns must be finite and above -100 %")
        f = pd.DataFrame({"c": np.log1p(np.asarray(c, float)), "k": np.log1p(np.asarray(k, float))}, index=idx)
        frames[a] = f.groupby(level=0).sum().sort_index()
    common = frames[assets[0]].index
    for a in assets[1:]:
        common = common.intersection(frames[a].index)
    common = common.sort_values()
    if len(common) < 2:
        raise ValueError("fewer than 2 common dates across assets")
    C, K = [], []
    for a in assets:
        f = frames[a]
        f = f[f.index <= common[-1]]
        pos = np.searchsorted(common.values, f.index.values, side="left")   # next common date >= d
        g = f.groupby(pos).sum().reindex(range(len(common)), fill_value=0.0)
        C.append(np.expm1(g["c"].to_numpy()))
        K.append(np.expm1(g["k"].to_numpy()))
    return common, np.column_stack(C), np.column_stack(K), assets


def compare_calibrated(candidate_id: str, control_id: str, assets: Sequence[str],
                       timeframe: str = "1d", matrix: str = "srp-matrix-v4", provider: str = "csv",
                       provider_kwargs: Optional[Dict] = None, seed: int = 0,
                       frames: Optional[Mapping[str, pd.DataFrame]] = None) -> Dict:
    """Run candidate and control through the frozen walk-forward at the headline cost and test dSR.

    ``frames`` (asset -> canonical OHLCV frame) bypasses the provider; used by calibration."""
    from app.strategy_research import MATRICES
    from app.strategy_research.evaluation.walkforward import walk_forward
    from app.strategy_research.providers import DataRequest, get_provider
    from app.strategy_research.strategies import get_strategy
    from .target_control import ensure_controls_registered

    ensure_controls_registered()
    m = MATRICES.get(matrix, MATRICES["srp-matrix-v4"])
    cand_s, ctrl_s = get_strategy(candidate_id), get_strategy(control_id)
    source = None if frames is not None else get_provider(provider, **(provider_kwargs or {}))
    per_asset = {}
    for a in assets:
        df = frames[a] if frames is not None else source.get(DataRequest(a, timeframe, None, None, None)).df
        wc = walk_forward(df, cand_s, timeframe, m.headline_cost_bps, m.headline_slippage_bps,
                          m.n_splits, m.min_train)
        wk = walk_forward(df, ctrl_s, timeframe, m.headline_cost_bps, m.headline_slippage_bps,
                          m.n_splits, m.min_train)
        if not np.array_equal(wc.oos_index, wk.oos_index):
            raise ValueError(f"{a}: candidate and control out-of-sample bars differ")
        dates = pd.to_datetime(df["open_time"].to_numpy()[wc.oos_index], unit="ms")
        per_asset[a] = (dates, wc.oos_net, wk.oos_net)
    dates, C, K, order = align_common_calendar(per_asset)
    result = paired_test(C, K, seed=seed)
    result.update({"candidate_id": candidate_id, "control_id": control_id, "assets": order,
                   "timeframe": timeframe, "matrix": m.version,
                   "cost_bps": m.headline_cost_bps, "slippage_bps": m.headline_slippage_bps,
                   "first_date": str(dates[0].date()), "last_date": str(dates[-1].date())})
    return result
