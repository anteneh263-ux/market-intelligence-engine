"""Hypothesis endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.database.models import Hypothesis
from app.hypotheses.engine import create_hypothesis, generate_default_hypotheses
from app.serializers import backtest_dict, hypothesis_dict
from app.services import backtest_hypothesis, latest_backtest_for

router = APIRouter(prefix="/api/hypotheses", tags=["hypotheses"])


class HypothesisIn(BaseModel):
    name: str
    logic: str
    conditions: dict
    direction: str = "long"
    timeframe: str = "1h"
    asset: str = "BTC/USDT"


@router.get("")
def list_hypotheses(db: Session = Depends(get_db)):
    hyps = db.execute(select(Hypothesis).order_by(Hypothesis.id)).scalars().all()
    out = []
    for h in hyps:
        bt = latest_backtest_for(db, h.id)
        item = hypothesis_dict(h)
        item["latest_backtest"] = backtest_dict(bt) if bt else None
        out.append(item)
    return {"hypotheses": out}


@router.get("/{hid}")
def get_hypothesis(hid: int, db: Session = Depends(get_db)):
    h = db.get(Hypothesis, hid)
    if not h:
        raise HTTPException(404, "Hypothesis not found")
    bt = latest_backtest_for(db, h.id)
    item = hypothesis_dict(h)
    item["latest_backtest"] = backtest_dict(bt, include_curve=True) if bt else None
    return item


@router.post("/generate-defaults")
def generate_defaults(db: Session = Depends(get_db)):
    hyps = generate_default_hypotheses(db)
    return {"created": len(hyps), "hypotheses": [hypothesis_dict(h) for h in hyps]}


@router.post("")
def add_hypothesis(payload: HypothesisIn, db: Session = Depends(get_db)):
    h = create_hypothesis(db, **payload.model_dump())
    return hypothesis_dict(h)


@router.post("/{hid}/backtest")
def backtest_one(hid: int, db: Session = Depends(get_db)):
    h = db.get(Hypothesis, hid)
    if not h:
        raise HTTPException(404, "Hypothesis not found")
    bt = backtest_hypothesis(db, h)
    if not bt:
        raise HTTPException(400, "No candle data — run data collection first.")
    return backtest_dict(bt, include_curve=True)
