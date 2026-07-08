"""Backtest endpoints, including ranking and the Strategy Graveyard."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.database.models import Backtest, Hypothesis
from app.serializers import backtest_dict, hypothesis_dict
from app.services import latest_backtest_for

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


def _rows(db: Session):
    hyps = db.execute(select(Hypothesis).order_by(Hypothesis.id)).scalars().all()
    out = []
    for h in hyps:
        bt = latest_backtest_for(db, h.id)
        if bt:
            out.append((h, bt))
    return out


@router.get("")
def list_backtests(db: Session = Depends(get_db)):
    """All latest backtests, ranked by Sharpe (best first)."""
    rows = _rows(db)
    rows.sort(key=lambda hb: hb[1].sharpe, reverse=True)
    items = []
    for rank, (h, bt) in enumerate(rows, start=1):
        d = backtest_dict(bt)
        d["rank"] = rank
        d["hypothesis"] = hypothesis_dict(h)
        items.append(d)
    return {"backtests": items}


@router.get("/graveyard")
def graveyard(db: Session = Depends(get_db)):
    """Rejected hypotheses (failed robustness gates)."""
    rows = [(h, bt) for h, bt in _rows(db) if bt.rejected]
    rows.sort(key=lambda hb: hb[1].sharpe, reverse=True)
    items = []
    for h, bt in rows:
        d = backtest_dict(bt)
        d["hypothesis"] = hypothesis_dict(h)
        items.append(d)
    return {"count": len(items), "graveyard": items}


@router.get("/{bid}")
def get_backtest(bid: int, db: Session = Depends(get_db)):
    bt = db.get(Backtest, bid)
    if not bt:
        raise HTTPException(404, "Backtest not found")
    d = backtest_dict(bt, include_curve=True)
    h = db.get(Hypothesis, bt.hypothesis_id)
    d["hypothesis"] = hypothesis_dict(h) if h else None
    return d
