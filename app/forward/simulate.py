"""Shared, causal position-based simulator used for BOTH the locked backtest reference
and the forward run - so backtest and forward use the SAME strategy logic and the SAME
parameters. Signal is decided at a bar's close and executed no earlier than the NEXT
bar (positions shifted forward one bar; no look-ahead). Per-bar P&L is against a fixed
notional so it is history-independent and can be windowed (forward-only) cleanly."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.paper.schema import iso_utc, resolve_target
from app.strategy_research.evaluation.stats import sharpe_per_period

_PPY = {"1d": 365.0, "1h": 8760.0, "4h": 2190.0, "1w": 52.0}
EQUITY0 = 10_000.0


def executed_positions(strategy, df: pd.DataFrame) -> np.ndarray:
    """Positions HELD each bar: the resolved signal decided at the PREVIOUS bar's close,
    shifted forward one bar (execute no earlier than the next bar). executed[0] = 0."""
    raw = strategy.positions(df).to_numpy()
    direction = getattr(strategy, "direction", "long_short")
    tgt = np.array([resolve_target(direction, float(x)) for x in raw], dtype=float)
    ex = np.zeros(len(tgt), dtype=float)
    ex[1:] = tgt[:-1]
    return ex


def _per_bar(strategy, df: pd.DataFrame, cost_bps: float, slip_bps: float) -> Dict[str, np.ndarray]:
    n = len(df)
    o = df["open"].to_numpy(float)
    c = df["close"].to_numpy(float)
    ot = df["open_time"].to_numpy("int64") if "open_time" in df.columns else np.arange(n, dtype="int64")
    raw = strategy.positions(df).to_numpy()
    direction = getattr(strategy, "direction", "long_short")
    tgt = np.array([resolve_target(direction, float(x)) for x in raw], dtype=float)
    ex = np.zeros(n, dtype=float)
    ex[1:] = tgt[:-1]
    fee, slp = cost_bps / 1e4, slip_bps / 1e4
    bar_ret = np.zeros(n)
    bar_ret[1:] = np.where(c[:-1] != 0, (c[1:] - c[:-1]) / c[:-1], 0.0)
    turnover = np.zeros(n)
    turnover[1:] = np.abs(np.diff(ex))
    gross = ex * bar_ret * EQUITY0
    costs = turnover * fee * EQUITY0
    slippage = turnover * slp * EQUITY0
    net = gross - costs - slippage
    return {"idx": np.arange(n), "open_time": ot, "open": o, "signal": tgt, "executed": ex,
            "bar_ret": bar_ret, "turnover": turnover, "gross": gross, "costs": costs,
            "slippage": slippage, "net": net, "net_ret": net / EQUITY0}


def window_metrics(net_ret: np.ndarray, executed: np.ndarray, timeframe: str) -> Dict:
    arr = np.asarray(net_ret, dtype=float)
    ex = np.asarray(executed, dtype=float)
    ppy = _PPY.get(timeframe, 365.0)
    sr = sharpe_per_period(arr) if arr.size else 0.0
    eq = float(np.prod(1.0 + arr)) if arr.size else 1.0
    peak = 1.0
    mdd = 0.0
    run = 1.0
    for r in arr:
        run *= (1.0 + r)
        peak = max(peak, run)
        mdd = max(mdd, (peak - run) / peak if peak > 0 else 0.0)
    trades = int(np.sum(np.abs(np.diff(ex)) > 0)) if ex.size > 1 else 0
    active = int(np.sum(ex != 0))
    active_pnl = arr[ex != 0] if active else np.array([])
    return {
        "observations": int(arr.size),
        "sharpe_per_period": float(sr),
        "sharpe_annual": float(sr * np.sqrt(ppy)),
        "total_return": float(eq - 1.0),
        "annual_return": float(eq ** (ppy / max(arr.size, 1)) - 1.0) if arr.size else 0.0,
        "max_drawdown": float(mdd),
        "num_trades": trades,
        "active_days": active,
        "hit_rate": float(np.mean(active_pnl > 0)) if active_pnl.size else 0.0,
        "turnover": float(np.sum(np.abs(np.diff(ex)))) if ex.size > 1 else 0.0,
        "costs": None,
    }


def _rows(pb: Dict[str, np.ndarray], sel: np.ndarray, has_time: bool) -> List[Dict]:
    """Build observation rows for the selected bar indices, with cumulative/drawdown
    accumulated from the start of the selection (window-local)."""
    rows: List[Dict] = []
    run = 1.0
    peak = 1.0
    for i in sel:
        run *= (1.0 + pb["net_ret"][i])
        peak = max(peak, run)
        dd = (peak - run) / peak if peak > 0 else 0.0
        rows.append({
            "candle_open_time_ms": int(pb["open_time"][i]),
            "datetime_utc": iso_utc(int(pb["open_time"][i])) if has_time else str(int(i)),
            "signal": int(pb["signal"][i]),
            "desired_position": int(pb["signal"][i]),
            "executed_position": int(pb["executed"][i]),
            "execution_price": float(pb["open"][i]),
            "gross_pnl": round(float(pb["gross"][i]), 6),
            "costs": round(float(pb["costs"][i]), 6),
            "slippage": round(float(pb["slippage"][i]), 6),
            "net_pnl": round(float(pb["net"][i]), 6),
            "cumulative_return": round(run - 1.0, 8),
            "drawdown": round(dd, 8),
            "error": None,
        })
    return rows


def simulate(strategy, df: pd.DataFrame, cost_bps: float, slippage_bps: float,
             timeframe: str = "1d") -> Tuple[List[Dict], Dict]:
    """Whole-frame simulation (used for the locked backtest reference)."""
    pb = _per_bar(strategy, df, cost_bps, slippage_bps)
    sel = np.arange(1, len(df))                       # bar 0 has no return
    rows = _rows(pb, sel, "open_time" in df.columns)
    m = window_metrics(pb["net_ret"][1:], pb["executed"][1:], timeframe)
    m["costs"] = float(np.sum(pb["costs"][1:] + pb["slippage"][1:]))
    return rows, m


def simulate_forward(strategy, full_df: pd.DataFrame, forward_start_ms: int,
                     cost_bps: float, slippage_bps: float, timeframe: str = "1d",
                     after_ms: Optional[int] = None) -> Tuple[List[Dict], Dict]:
    """Forward-only simulation. Positions are computed on the FULL frame (causal warm-up),
    but observations + metrics cover ONLY bars with open_time >= forward_start_ms (and
    strictly greater than `after_ms`, for resuming). P&L accumulates from the forward
    start, physically separate from the training window."""
    pb = _per_bar(strategy, full_df, cost_bps, slippage_bps)
    ot = pb["open_time"]
    mask = ot >= int(forward_start_ms)
    if after_ms is not None:
        mask = mask & (ot > int(after_ms))
    sel = pb["idx"][mask].astype(int)
    sel = sel[sel >= 1]
    rows = _rows(pb, sel, "open_time" in full_df.columns)
    m = window_metrics(pb["net_ret"][sel], pb["executed"][sel], timeframe)
    m["costs"] = float(np.sum(pb["costs"][sel] + pb["slippage"][sel]))
    return rows, m


def metrics_from_observations(observations: List[Dict], timeframe: str = "1d") -> Dict:
    """Rebuild window metrics from PERSISTED forward observations. net_pnl was booked
    against the fixed notional EQUITY0, so net return per bar is net_pnl / EQUITY0. This
    keeps evaluation on the physically-separated forward observation log (never training
    data)."""
    if not observations:
        return window_metrics(np.array([]), np.array([]), timeframe)
    net_ret = np.array([float(o.get("net_pnl", 0.0)) / EQUITY0 for o in observations],
                       dtype=float)
    executed = np.array([float(o.get("executed_position", 0.0)) for o in observations],
                        dtype=float)
    m = window_metrics(net_ret, executed, timeframe)
    m["costs"] = float(sum(float(o.get("costs", 0.0)) + float(o.get("slippage", 0.0))
                           for o in observations))
    return m
