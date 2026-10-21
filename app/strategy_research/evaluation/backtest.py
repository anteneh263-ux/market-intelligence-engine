"""Position-based backtest — research only, no execution.

Given a target-position series in {-1,0,+1} and a price frame, compute net
per-bar returns under frozen cost and slippage assumptions, plus trade-level
and risk metrics. The position is SHIFTED FORWARD one bar before returns are
applied: the signal at close t first affects the return at t+1. This prevents
same-bar execution, but does not validate signal causality. A signal that reads
future input can still leak through this function; independent causal-input
checks and point-in-time data validation are required.

Costs: charged on |change in held position| × (fee + slippage), in basis points
per unit of position turned over. A flat→long entry turns over 1 unit; a
long→short reversal turns over 2 units — exactly as a real stop-and-reverse would.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

from .stats import sharpe_per_period

PERIODS_PER_YEAR = {"1h": 8760.0, "4h": 2190.0, "1d": 365.0, "1w": 52.0}


@dataclass
class BacktestResult:
    net_returns: np.ndarray            # per-bar net return series
    equity: np.ndarray                 # equity curve (starts at 1.0)
    open_time: np.ndarray              # ms epoch per bar (aligned to net_returns)
    metrics: Dict = field(default_factory=dict)
    trade_returns: List[float] = field(default_factory=list)


def _max_drawdown(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    running_max = np.maximum.accumulate(equity)
    dd = (running_max - equity) / running_max
    return float(np.max(dd)) if dd.size else 0.0


def _trade_returns(exec_pos: np.ndarray, net: np.ndarray) -> List[float]:
    """Compound net returns within each maximal run of constant non-zero position."""
    trades: List[float] = []
    i, n = 0, len(exec_pos)
    while i < n:
        if exec_pos[i] == 0:
            i += 1
            continue
        j = i
        cur = exec_pos[i]
        acc = 1.0
        while j < n and exec_pos[j] == cur:
            acc *= (1.0 + net[j])
            j += 1
        trades.append(acc - 1.0)
        i = j
    return trades


def run_backtest(df: pd.DataFrame, positions: pd.Series, timeframe: str,
                 cost_bps: float, slippage_bps: float) -> BacktestResult:
    ret = df["close"].pct_change().fillna(0.0).to_numpy()
    pos = positions.reindex(df.index).fillna(0.0).to_numpy(dtype=float)
    # Position held INTO bar t is the signal decided at bar t-1's close.
    exec_pos = np.empty_like(pos)
    exec_pos[0] = 0.0
    exec_pos[1:] = pos[:-1]

    gross = exec_pos * ret
    turnover = np.abs(np.diff(np.concatenate([[0.0], exec_pos])))
    cost_rate = (cost_bps + slippage_bps) / 10_000.0
    cost = turnover * cost_rate
    net = gross - cost
    equity = np.cumprod(1.0 + net)

    ppy = PERIODS_PER_YEAR.get(timeframe, 365.0)
    sr = sharpe_per_period(net)
    trades = _trade_returns(exec_pos, net)
    tarr = np.array(trades, dtype=float)
    wins = tarr[tarr > 0]
    losses = tarr[tarr < 0]
    gp = float(wins.sum())
    gl = float(-losses.sum())

    metrics = {
        "total_return": float(equity[-1] - 1.0) if equity.size else 0.0,
        "sharpe_per_period": sr,
        "sharpe_annual": sr * np.sqrt(ppy),
        "ann_return": float(np.mean(net) * ppy),
        "ann_vol": float(np.std(net, ddof=1) * np.sqrt(ppy)) if net.size > 1 else 0.0,
        "max_drawdown": _max_drawdown(equity),
        "num_trades": int(tarr.size),
        "win_rate": float((tarr > 0).mean()) if tarr.size else 0.0,
        "profit_factor": (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0),
        "avg_trade": float(tarr.mean()) if tarr.size else 0.0,
        "total_turnover": float(turnover.sum()),
        "periods_per_year": ppy,
        "n_bars": int(net.size),
    }
    return BacktestResult(
        net_returns=net, equity=equity, open_time=df["open_time"].to_numpy(),
        metrics=metrics, trade_returns=trades,
    )
