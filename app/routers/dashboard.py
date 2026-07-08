"""Dashboard aggregate + full-pipeline endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import DISCLAIMER
from app.database.db import get_db
from app.services import candle_count, dashboard_stats, run_full_pipeline

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
def get_dashboard(db: Session = Depends(get_db)):
    stats = dashboard_stats(db)
    stats["candles_stored"] = candle_count(db)
    stats["disclaimer"] = DISCLAIMER
    return stats


@router.post("/pipeline/run")
def run_pipeline(limit: int = Query(1000, ge=50, le=1000),
                 db: Session = Depends(get_db)):
    """One-click: collect data → generate hypotheses → backtest → report."""
    return run_full_pipeline(db, limit=limit, reset_hypotheses=True)
