"""Buy-and-hold and versioned random-timing baselines.

``random_timing`` preserves the v1/v2 Bernoulli redraw method for historical
reproduction; it does NOT match executed trade counts. Matrix v3 uses
``random_timing_trade_matched`` to match engine ``num_trades`` exactly per seed.
Neither method establishes absence of timing skill or matches market exposure.
"""
from __future__ import annotations

from numbers import Integral
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
    """Legacy v1/v2 Bernoulli redraw benchmark, preserved unchanged.

    ``target_trades`` controls redraw probability, NOT actual engine trades.
    Redraws can repeat a state; long-only exits are not new trades.
    """
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


TRADE_MATCHED_METHOD = "executed-runs-v1"


def _integer(name: str, value: int, minimum: int) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _trade_matched_positions(n: int, target: int, direction: str,
                             rng: np.random.Generator) -> np.ndarray:
    """Sample uniformly from the documented path family (validated inputs).

    Only the first n-1 signals execute. The final signal repeats its predecessor
    and cannot create a phantom final trade. No forced terminal liquidation.
    """
    pos = np.zeros(n)
    m = n - 1
    if target == 0:
        return pos
    if direction == "long_short":
        # Fully exposed +/-1 paths: choose T-1 distinct reversals and initial sign.
        sign = float(rng.choice([-1.0, 1.0]))
        cuts = np.sort(rng.choice(m - 1, size=target - 1, replace=False) + 1)
        edges = np.concatenate(([0], cuts, [m]))
        for left, right in zip(edges[:-1], edges[1:]):
            pos[left:right] = sign
            sign = -sign
    else:
        # Binary paths with T long runs: 2T distinct boundaries in [0, m].
        # Adjacent intervals necessarily have at least one cash slot between them.
        edges = np.sort(rng.choice(m + 1, size=2 * target, replace=False))
        for left, right in zip(edges[::2], edges[1::2]):
            pos[left:right] = 1.0
    pos[-1] = pos[-2]
    return pos


def random_timing_trade_matched(df: pd.DataFrame, timeframe: str, cost_bps: float,
                                slippage_bps: float, target_trades: int, direction: str,
                                n_seeds: int = 25, base_seed: int = 12345) -> Dict:
    """Matrix v3: exact engine trade-count matching, deterministic PCG64 seeds.

    A trade is one maximal constant nonzero run of EXECUTED positions, including
    a still-open final run. One-bar lag leaves m=len(df)-1 executable slots.
    Long/short supports 0..m trades; long/cash (``long_only``) 0..ceil(m/2).
    Impossible targets raise instead of clamping. Zero trades means all cash.

    Conditional path families are uniform: long/short is always exposed for T>0;
    long/cash samples binary paths with T long runs. Holding duration, exposure,
    turnover and aggregate cost are NOT matched to the strategy. Fees/slippage
    use the same engine and rates, and actual activity is returned for audit.
    """
    n = len(df)
    if n == 0:
        raise ValueError("trade-matched benchmark requires at least one bar")
    target = _integer("target_trades", target_trades, 0)
    count = _integer("n_seeds", n_seeds, 1)
    first_seed = _integer("base_seed", base_seed, 0)
    if direction not in ("long_short", "long_only"):
        raise ValueError("direction must be long_short or long_only")
    m = n - 1
    maximum = m if direction == "long_short" else (m + 1) // 2
    if target > maximum:
        raise ValueError(f"target_trades={target} exceeds {direction} maximum {maximum} for {n} bars")

    srs, rets, dds = [], [], []
    counts, turnovers, exposed = [], [], []
    seeds = list(range(first_seed, first_seed + count))
    for seed in seeds:
        rng = np.random.Generator(np.random.PCG64(seed))
        pos = _trade_matched_positions(n, target, direction, rng)
        result = run_backtest(df, pd.Series(pos, index=df.index), timeframe, cost_bps, slippage_bps)
        metrics = result.metrics
        if metrics["num_trades"] != target:
            raise RuntimeError("trade-matched benchmark disagrees with engine num_trades")
        counts.append(metrics["num_trades"])
        turnovers.append(metrics["total_turnover"])
        exposed.append(int(np.count_nonzero(pos[:-1])))
        srs.append(metrics["sharpe_per_period"])
        rets.append(metrics["total_return"])
        dds.append(metrics["max_drawdown"])
    return {
        "sharpe_per_period": float(np.mean(srs)),
        "sharpe_annual": float(np.mean(srs) * np.sqrt(metrics["periods_per_year"])),
        "sharpe_std": float(np.std(srs, ddof=1)) if count > 1 else 0.0,
        "sharpe_p95": float(np.percentile(srs, 95)),
        "total_return": float(np.mean(rets)),
        "max_drawdown": float(np.mean(dds)),
        "n_seeds": count,
        "sharpe_samples": srs,
        "matching": {
            "method": TRADE_MATCHED_METHOD,
            "metric": "num_trades",
            "target_trades": target,
            "direction": direction,
            "n_bars": n,
            "executable_slots": m,
            "rng": "numpy.PCG64",
            "seeds": seeds,
            "num_trades": counts,
            "total_turnover": turnovers,
            "exposed_bars": exposed,
            "exposure_fraction": [x / m if m else 0.0 for x in exposed],
            "exposure_denominator": "executable_slots",
        },
    }
