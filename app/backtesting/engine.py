"""Backtest Engine — simple, transparent, no real execution.

Risk assumptions (MVP):
  * fixed position size (fraction of equity per trade, no leverage)
  * fee charged on entry and exit (default 0.1% per side)
  * non-overlapping trades: once in a position we wait `hold` candles to exit
  * no shorting borrow costs / slippage modelled (documented as a weakness)

This is a research tool. It does NOT place orders anywhere.
"""
from __future__ import annotations

import math
from typing import List

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import Backtest, Candle, Hypothesis
from app.features.builder import build_features


def _series(df: pd.DataFrame, ref):
    """Resolve a condition operand to a numeric Series."""
    if isinstance(ref, (int, float)):
        return pd.Series(float(ref), index=df.index)
    return df[ref]


def _eval_condition(df: pd.DataFrame, cond: dict) -> pd.Series:
    left = _series(df, cond["left"])
    right = _series(df, cond["right"])
    op = cond["op"]
    if op == ">":
        return left > right
    if op == "<":
        return left < right
    if op == ">=":
        return left >= right
    if op == "<=":
        return left <= right
    if op == "cross_above":
        return (left > right) & (left.shift(1) <= right.shift(1))
    if op == "cross_below":
        return (left < right) & (left.shift(1) >= right.shift(1))
    raise ValueError(f"Unknown operator: {op}")


def entry_signal(df: pd.DataFrame, conditions: List[dict]) -> pd.Series:
    if not conditions:
        return pd.Series(False, index=df.index)
    mask = pd.Series(True, index=df.index)
    for c in conditions:
        mask &= _eval_condition(df, c)
    return mask.fillna(False)


def run_backtest(candles: List[Candle], hypothesis: Hypothesis) -> dict:
    """Execute the hypothesis over candles and return metric dict + equity curve."""
    df = build_features(candles)
    spec = hypothesis.conditions or {}
    entries = entry_signal(df, spec.get("entry", []))
    hold = int(spec.get("hold", 6))
    direction = 1 if hypothesis.direction == "long" else -1
    fee = settings.fee_pct
    size = settings.position_size

    closes = df["close"].values
    times = df["open_time"].values
    n = len(df)

    trades: List[float] = []          # net return per trade (after fees)
    equity = 1.0
    equity_curve = [{"t": int(times[0]), "equity": 1.0}] if n else []

    i = 0
    # start after warmup so EMA200 is meaningful
    warmup = min(200, max(0, n - 1))
    i = warmup
    while i < n - 1:
        if bool(entries.iloc[i]):
            entry_price = closes[i]
            exit_idx = min(i + hold, n - 1)
            exit_price = closes[exit_idx]
            gross = direction * (exit_price - entry_price) / entry_price
            net = gross - 2 * fee              # entry + exit fee
            trade_ret = net * size
            trades.append(trade_ret)
            equity *= (1 + trade_ret)
            equity_curve.append({"t": int(times[exit_idx]), "equity": round(equity, 6)})
            i = exit_idx + 1                    # non-overlapping
        else:
            i += 1

    return _metrics(trades, equity, equity_curve, hold)


def _metrics(trades: List[float], equity: float, equity_curve, hold: int) -> dict:
    arr = np.array(trades, dtype=float)
    num = int(arr.size)

    if num == 0:
        return {
            "total_return": 0.0, "win_rate": 0.0, "profit_factor": 0.0,
            "max_drawdown": 0.0, "sharpe": 0.0, "num_trades": 0,
            "avg_win": 0.0, "avg_loss": 0.0, "equity_curve": equity_curve,
        }

    wins = arr[arr > 0]
    losses = arr[arr < 0]
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())

    total_return = float(equity - 1.0)
    win_rate = float(len(wins) / num)
    profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else 0.0
    )

    # Max drawdown from the equity curve
    eq = np.array([p["equity"] for p in equity_curve], dtype=float)
    running_max = np.maximum.accumulate(eq)
    dd = (running_max - eq) / running_max
    max_dd = float(dd.max()) if dd.size else 0.0

    # Sharpe of per-trade returns (mean/std). Not annualised — trade-level.
    std = float(arr.std(ddof=1)) if num > 1 else 0.0
    sharpe = float(arr.mean() / std * math.sqrt(num)) if std > 0 else 0.0

    return {
        "total_return": total_return,
        "win_rate": win_rate,
        "profit_factor": None if profit_factor == float("inf") else profit_factor,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "num_trades": num,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "equity_curve": equity_curve,
    }


def evaluate_rejection(m: dict) -> tuple[bool, List[str]]:
    """Strategy Graveyard rules. Returns (rejected, reasons)."""
    reasons: List[str] = []
    pf = m["profit_factor"]
    if m["sharpe"] < settings.reject_sharpe_below:
        reasons.append(f"Sharpe {m['sharpe']:.2f} < {settings.reject_sharpe_below}")
    if m["max_drawdown"] > settings.reject_drawdown_above:
        reasons.append(
            f"Max drawdown {m['max_drawdown']*100:.1f}% > {settings.reject_drawdown_above*100:.0f}%"
        )
    if m["num_trades"] < settings.reject_min_trades:
        reasons.append(f"Only {m['num_trades']} trades < {settings.reject_min_trades}")
    if pf is not None and pf < settings.reject_profit_factor_below:
        reasons.append(f"Profit factor {pf:.2f} < {settings.reject_profit_factor_below}")
    return (len(reasons) > 0, reasons)


def persist_backtest(db: Session, hypothesis: Hypothesis, candles: List[Candle]) -> Backtest:
    m = run_backtest(candles, hypothesis)
    rejected, reasons = evaluate_rejection(m)
    bt = Backtest(
        hypothesis_id=hypothesis.id,
        total_return=m["total_return"],
        win_rate=m["win_rate"],
        profit_factor=m["profit_factor"],
        max_drawdown=m["max_drawdown"],
        sharpe=m["sharpe"],
        num_trades=m["num_trades"],
        avg_win=m["avg_win"],
        avg_loss=m["avg_loss"],
        rejected=rejected,
        reject_reasons=reasons,
        equity_curve=m["equity_curve"],
    )
    db.add(bt)
    db.commit()
    db.refresh(bt)
    return bt
