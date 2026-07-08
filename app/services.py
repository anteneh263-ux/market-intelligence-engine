"""Service layer — orchestrates the research pipeline end to end."""
from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import ASSETS, TIMEFRAMES
from app.data.collector import collect, load_candles
from app.backtesting.engine import persist_backtest
from app.database.models import Backtest, Candle, Hypothesis, Report
from app.hypotheses.engine import generate_default_hypotheses
from app.reports.generator import generate_report


def collect_all(db: Session, limit: int = 1000) -> List[dict]:
    results = []
    for asset in ASSETS:
        for tf in TIMEFRAMES:
            results.append(collect(db, asset, tf, limit))
    return results


def backtest_hypothesis(db: Session, h: Hypothesis) -> Optional[Backtest]:
    candles = load_candles(db, h.asset, h.timeframe)
    if not candles:
        return None
    bt = persist_backtest(db, h, candles)
    generate_report(db, h, bt)
    return bt


def run_full_pipeline(db: Session, limit: int = 1000, reset_hypotheses: bool = True) -> dict:
    """Collect data, (re)generate hypotheses, backtest all, write reports."""
    collected = collect_all(db, limit)

    if reset_hypotheses:
        db.query(Report).delete()
        db.query(Backtest).delete()
        db.query(Hypothesis).delete()
        db.commit()

    hyps = generate_default_hypotheses(db)
    tested = 0
    for h in hyps:
        if backtest_hypothesis(db, h):
            tested += 1

    return {
        "collected": collected,
        "hypotheses_created": len(hyps),
        "hypotheses_tested": tested,
    }


def latest_backtest_for(db: Session, hypothesis_id: int) -> Optional[Backtest]:
    stmt = (
        select(Backtest)
        .where(Backtest.hypothesis_id == hypothesis_id)
        .order_by(Backtest.created_at.desc(), Backtest.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def dashboard_stats(db: Session) -> dict:
    """Aggregate metrics for the Home dashboard."""
    # Use the latest backtest per hypothesis
    hyps = db.execute(select(Hypothesis)).scalars().all()
    rows = []
    for h in hyps:
        bt = latest_backtest_for(db, h.id)
        if bt:
            rows.append((h, bt))

    total = len(rows)
    if total == 0:
        return {
            "total_hypotheses_tested": 0,
            "avg_sharpe": 0.0,
            "avg_drawdown": 0.0,
            "active": 0,
            "rejected": 0,
            "best": None,
            "worst": None,
        }

    avg_sharpe = sum(b.sharpe for _, b in rows) / total
    avg_dd = sum(b.max_drawdown for _, b in rows) / total
    rejected = sum(1 for _, b in rows if b.rejected)
    active = total - rejected

    ranked = sorted(rows, key=lambda hb: hb[1].sharpe, reverse=True)
    best_h, best_b = ranked[0]
    worst_h, worst_b = ranked[-1]

    def brief(h: Hypothesis, b: Backtest) -> dict:
        return {
            "hypothesis_id": h.id,
            "name": h.name,
            "asset": h.asset,
            "timeframe": h.timeframe,
            "sharpe": round(b.sharpe, 3),
            "total_return": round(b.total_return, 4),
            "max_drawdown": round(b.max_drawdown, 4),
            "num_trades": b.num_trades,
            "rejected": b.rejected,
        }

    return {
        "total_hypotheses_tested": total,
        "avg_sharpe": round(avg_sharpe, 3),
        "avg_drawdown": round(avg_dd, 4),
        "active": active,
        "rejected": rejected,
        "best": brief(best_h, best_b),
        "worst": brief(worst_h, worst_b),
    }


def candle_count(db: Session) -> int:
    return db.execute(select(func.count(Candle.id))).scalar_one()
