"""Honest baselines every strategy must beat: buy-and-hold and random-timing.

Buy-and-hold: always long (position = +1). The question "did the strategy add
anything?" is meaningless without it.

Random-timing: random positions with the SAME trade frequency as the strategy,
averaged over many deterministic seeds. This controls for "you'd have made this
just by being in the market at random with similar turnover" — a strategy that
cannot beat matched random timing has no timing skill.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from .backtest import run_backtest
from .stats import sharpe_per_period


def buy_and_hold(df: pd.DataFrame, timeframe: str, cost_bps: float,
                 slippage_bps: float) -> Dict:
    pos = pd.Series(1.0, index=df.index)
    r = run_backtest(df, pos, timeframe, cost_bps, slippage_bps)
    return r.metrics


def random_timing(df: pd.DataFrame, timeframe: str, cost_bps: float,
                  slippage_bps: float, target_trades: int, direction: str,
                  n_seeds: int = 25, base_seed: int = 12345) -> Dict:
    """Average metrics of random position series matched to `target_trades`
    flips. Deterministic given seeds. Long/short strategies draw from {-1,+1};
    long-only from {0,+1}."""
    n = len(df)
    if n < 3 or target_trades < 1:
        return {"sharpe_per_period": 0.0, "sharpe_annual": 0.0, "total_return": 0.0,
                "max_drawdown": 0.0, "n_seeds": 0, "sharpe_samples": []}
    flip_p = min(1.0, target_trades / max(n - 1, 1))
    states = np.array([-1.0, 1.0]) if direction == "long_short" else np.array([0.0, 1.0])

    srs: List[float] = []
    rets: List[float] = []
    dds: List[float] = []
    for k in range(n_seeds):
        rng = np.random.default_rng(base_seed + k)
        pos = np.empty(n)
        pos[0] = rng.choice(states)
        flips = rng.random(n) < flip_p
        for i in range(1, n):
            pos[i] = rng.choice(states) if flips[i] else pos[i - 1]
        r = run_backtest(df, pd.Series(pos, index=df.index), timeframe, cost_bps, slippage_bps)
        srs.append(r.metrics["sharpe_per_period"])
        rets.append(r.metrics["total_return"])
        dds.append(r.metrics["max_drawdown"])
    return {
        "sharpe_per_period": float(np.mean(srs)),
        "sharpe_annual": float(np.mean(srs)) * float(np.sqrt(
            run_backtest(df, pd.Series(states[0], index=df.index), timeframe,
                         cost_bps, slippage_bps).metrics["periods_per_year"])),
        "sharpe_std": float(np.std(srs, ddof=1)) if len(srs) > 1 else 0.0,
        "sharpe_p95": float(np.percentile(srs, 95)),
        "total_return": float(np.mean(rets)),
        "max_drawdown": float(np.mean(dds)),
        "n_seeds": n_seeds,
        "sharpe_samples": srs,
    }
