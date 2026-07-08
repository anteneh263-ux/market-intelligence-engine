"""Market data endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import ASSETS, TIMEFRAMES
from app.data.collector import collect, load_candles
from app.database.db import get_db
from app.features.builder import build_features, latest_feature_row
from app.serializers import candle_dict
from app.services import collect_all

router = APIRouter(prefix="/api/data", tags=["data"])


@router.get("/assets")
def list_assets():
    return {"assets": ASSETS, "timeframes": TIMEFRAMES}


@router.post("/collect")
def collect_endpoint(
    limit: int = Query(1000, ge=50, le=1000),
    db: Session = Depends(get_db),
):
    """Fetch OHLCV for every asset/timeframe and store it."""
    return {"results": collect_all(db, limit)}


@router.post("/collect/{asset_base}/{timeframe}")
def collect_one(asset_base: str, timeframe: str,
                limit: int = Query(1000, ge=50, le=1000),
                db: Session = Depends(get_db)):
    asset = f"{asset_base.upper()}/USDT" if "/" not in asset_base else asset_base.upper()
    return collect(db, asset, timeframe, limit)


@router.get("/candles/{asset_base}/{timeframe}")
def get_candles(asset_base: str, timeframe: str,
                limit: int = Query(300, ge=10, le=1000),
                db: Session = Depends(get_db)):
    asset = f"{asset_base.upper()}/USDT" if "/" not in asset_base else asset_base.upper()
    candles = load_candles(db, asset, timeframe)
    tail = candles[-limit:]
    return {
        "asset": asset,
        "timeframe": timeframe,
        "count": len(candles),
        "candles": [candle_dict(c) for c in tail],
        "latest_features": latest_feature_row(candles),
    }


@router.get("/features/{asset_base}/{timeframe}")
def get_features(asset_base: str, timeframe: str,
                 limit: int = Query(300, ge=10, le=1000),
                 db: Session = Depends(get_db)):
    """Return candles enriched with indicators for charting."""
    asset = f"{asset_base.upper()}/USDT" if "/" not in asset_base else asset_base.upper()
    candles = load_candles(db, asset, timeframe)
    df = build_features(candles)
    if df.empty:
        return {"asset": asset, "timeframe": timeframe, "rows": []}
    cols = ["open_time", "close", "rsi", "ema20", "ema50", "ema200",
            "volume", "volume_change", "volatility"]
    rows = df[cols].tail(limit).where(df.notna(), None).to_dict(orient="records")
    return {"asset": asset, "timeframe": timeframe, "rows": rows}
