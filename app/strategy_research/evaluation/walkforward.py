"""Walk-forward, out-of-sample evaluation.

Expanding-window walk-forward: the series is cut into K+1 contiguous blocks. For
each test block k, parameters are selected on ALL data before it (in-sample) and
then applied to block k (out-of-sample). The OOS net-return segments are stitched
into a single out-of-sample equity curve — the only curve the verdict trusts.

Precomputing positions on the full frame and slicing per fold requires each
strategy to be causal. Trailing indicators can satisfy that requirement, but this
function does not enforce it for arbitrary strategies or point-in-time datasets.
Prefix and future-perturbation tests provide finite checks, not a universal proof.

The number of parameter configurations in the grid is the per-strategy
data-snooping budget, passed to the deflated-Sharpe correction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

from ..strategies.base import Strategy
from .backtest import run_backtest
from .stats import sharpe_per_period


@dataclass
class WalkForwardResult:
    oos_net: np.ndarray
    oos_open_time: np.ndarray
    oos_index: np.ndarray                       # positions in the original df
    oos_pos: np.ndarray = field(default_factory=lambda: np.array([]))
    selected_params: List[Dict] = field(default_factory=list)
    grid_size: int = 1
    sr_trials: List[float] = field(default_factory=list)
    fold_boundaries: List[int] = field(default_factory=list)
    in_sample_sharpe: float = 0.0
    oos_sharpe: float = 0.0


def _precompute(df: pd.DataFrame, strategy: Strategy, grid: List[Dict],
                timeframe: str, cost_bps: float, slippage_bps: float):
    """Return (nets, positions) arrays for every grid config."""
    nets, poss = [], []
    for p in grid:
        pos = strategy.positions(df, p)
        r = run_backtest(df, pos, timeframe, cost_bps, slippage_bps)
        nets.append(r.net_returns)
        poss.append(pos.reindex(df.index).fillna(0.0).to_numpy(dtype=float))
    return nets, poss


def walk_forward(df: pd.DataFrame, strategy: Strategy, timeframe: str,
                 cost_bps: float, slippage_bps: float, n_splits: int = 5,
                 min_train: int = 100) -> WalkForwardResult:
    grid = strategy.param_grid()
    n = len(df)
    nets, poss = _precompute(df, strategy, grid, timeframe, cost_bps, slippage_bps)

    # K+1 contiguous blocks; blocks 1..K are OOS test folds.
    edges = np.linspace(0, n, n_splits + 1, dtype=int)
    oos_parts: List[np.ndarray] = []
    oos_idx_parts: List[np.ndarray] = []
    oos_pos_parts: List[np.ndarray] = []
    selected: List[Dict] = []
    boundaries: List[int] = []
    is_sr_acc: List[float] = []

    for k in range(1, n_splits + 1):
        train_end = edges[k]
        test_start, test_stop = edges[k], edges[k + 1] if k + 1 < len(edges) else n
        if test_stop <= test_start:
            continue
        if train_end < min_train:
            # Not enough history to select yet — hold flat OOS (no trades, no cost).
            seg = np.zeros(test_stop - test_start)
            oos_parts.append(seg)
            oos_idx_parts.append(np.arange(test_start, test_stop))
            oos_pos_parts.append(np.zeros(test_stop - test_start))
            selected.append({"_": "insufficient_train"})
            boundaries.append(test_start)
            continue
        # Select the grid config with the best in-sample Sharpe on [0, train_end).
        best_j, best_sr = 0, -np.inf
        for j, net in enumerate(nets):
            sr = sharpe_per_period(net[:train_end])
            if sr > best_sr:
                best_sr, best_j = sr, j
        is_sr_acc.append(best_sr)
        selected.append(dict(grid[best_j]))
        boundaries.append(test_start)
        oos_parts.append(nets[best_j][test_start:test_stop])
        oos_idx_parts.append(np.arange(test_start, test_stop))
        oos_pos_parts.append(poss[best_j][test_start:test_stop])

    oos_net = np.concatenate(oos_parts) if oos_parts else np.array([])
    oos_index = np.concatenate(oos_idx_parts) if oos_idx_parts else np.array([], dtype=int)
    oos_pos = np.concatenate(oos_pos_parts) if oos_pos_parts else np.array([])
    oos_open_time = df["open_time"].to_numpy()[oos_index] if oos_index.size else np.array([])

    # Trial Sharpes over the OOS span (variance feeds the deflated-Sharpe correction).
    sr_trials = [sharpe_per_period(net[oos_index]) for net in nets] if oos_index.size else []

    return WalkForwardResult(
        oos_net=oos_net, oos_open_time=oos_open_time, oos_index=oos_index,
        oos_pos=oos_pos, selected_params=selected, grid_size=len(grid),
        sr_trials=sr_trials, fold_boundaries=boundaries,
        in_sample_sharpe=float(np.mean(is_sr_acc)) if is_sr_acc else 0.0,
        oos_sharpe=sharpe_per_period(oos_net),
    )
