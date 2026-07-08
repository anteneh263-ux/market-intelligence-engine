"""Plain-dict serializers for ORM objects (kept simple for the MVP)."""
from __future__ import annotations

from app.database.models import Backtest, Candle, Hypothesis, Report


def candle_dict(c: Candle) -> dict:
    return {
        "open_time": c.open_time,
        "open": c.open, "high": c.high, "low": c.low,
        "close": c.close, "volume": c.volume,
    }


def hypothesis_dict(h: Hypothesis) -> dict:
    return {
        "id": h.id,
        "name": h.name,
        "logic": h.logic,
        "conditions": h.conditions,
        "direction": h.direction,
        "timeframe": h.timeframe,
        "asset": h.asset,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }


def backtest_dict(b: Backtest, include_curve: bool = False) -> dict:
    d = {
        "id": b.id,
        "hypothesis_id": b.hypothesis_id,
        "total_return": b.total_return,
        "win_rate": b.win_rate,
        "profit_factor": b.profit_factor,
        "max_drawdown": b.max_drawdown,
        "sharpe": b.sharpe,
        "num_trades": b.num_trades,
        "avg_win": b.avg_win,
        "avg_loss": b.avg_loss,
        "rejected": b.rejected,
        "reject_reasons": b.reject_reasons or [],
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }
    if include_curve:
        d["equity_curve"] = b.equity_curve or []
    return d


def report_dict(r: Report, include_markdown: bool = True) -> dict:
    d = {
        "id": r.id,
        "backtest_id": r.backtest_id,
        "hypothesis_id": r.hypothesis_id,
        "title": r.title,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
    if include_markdown:
        d["markdown"] = r.markdown
    return d
