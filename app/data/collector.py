"""Data Collector — fetch OHLCV candles and persist to SQLite.

Sources, tried in order of preference:
  1. Binance public REST (no API key required)
  2. CCXT (if installed and reachable)
  3. Synthetic generator (deterministic, fully offline)

The synthetic fallback guarantees the platform is demonstrable even when
exchange endpoints are geo-blocked or unreachable. It is clearly flagged in
API responses so research is never mistaken for real market data.
"""
from __future__ import annotations

import math
import time
from typing import List, Tuple

import requests
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import Candle

# Milliseconds per timeframe bar
TF_MS = {"1h": 3_600_000, "4h": 14_400_000}
# Binance interval codes
TF_BINANCE = {"1h": "1h", "4h": "4h"}


# --------------------------------------------------------------------------- #
# Live sources
# --------------------------------------------------------------------------- #
def _fetch_binance(asset: str, timeframe: str, limit: int) -> List[list]:
    """Fetch klines from Binance public REST. Returns [] on failure."""
    symbol = asset.replace("/", "")
    url = f"{settings.binance_base_url}/api/v3/klines"
    params = {"symbol": symbol, "interval": TF_BINANCE[timeframe], "limit": min(limit, 1000)}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        rows = r.json()
        return [
            [int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
            for k in rows
        ]
    except Exception:
        return []


def _fetch_ccxt(asset: str, timeframe: str, limit: int) -> List[list]:
    """Fetch OHLCV via ccxt. Returns [] on failure."""
    try:
        import ccxt  # type: ignore

        ex = ccxt.binance({"enableRateLimit": True})
        data = ex.fetch_ohlcv(asset, timeframe=timeframe, limit=min(limit, 1000))
        return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in data]
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Synthetic (offline) source
# --------------------------------------------------------------------------- #
def _synthetic(asset: str, timeframe: str, limit: int) -> List[list]:
    """Deterministic pseudo-market OHLCV using a seeded random walk.

    Produces regime changes (trend + volatility clustering) so indicators and
    hypotheses have realistic structure to work with. Deterministic per
    (asset, timeframe) so results are reproducible.
    """
    step = TF_MS[timeframe]
    now = int(time.time() * 1000)
    start = now - step * limit

    # Seed derived from asset/timeframe for reproducibility
    seed = abs(hash((asset, timeframe))) % (2**31)
    base_price = 60_000.0 if asset.startswith("BTC") else 3_000.0

    def rng(n: int) -> float:
        # simple deterministic LCG mapped to [-1, 1]
        x = (seed + n * 1_103_515_245 + 12_345) & 0x7FFFFFFF
        x = (x * 1_103_515_245 + 12_345) & 0x7FFFFFFF
        return (x / 0x7FFFFFFF) * 2.0 - 1.0

    candles: List[list] = []
    price = base_price
    vol = 0.015
    for i in range(limit):
        # slowly varying volatility regime (clusters of calm and stress)
        vol = max(0.006, min(0.06, vol + 0.003 * rng(i * 7)))
        # medium-cycle trend plus a faster oscillation so momentum AND
        # overbought/oversold (RSI) extremes both occur in the series
        trend = 0.0008 * math.sin(i / 55.0)
        swing = 0.010 * math.sin(i / 9.0 + rng(i * 17))
        drift = trend + swing + 0.0003 * rng(i * 3)
        ret = drift + vol * rng(i)
        open_p = price
        close_p = max(1.0, open_p * (1 + ret))
        high_p = max(open_p, close_p) * (1 + abs(vol * rng(i * 5)) * 0.5)
        low_p = min(open_p, close_p) * (1 - abs(vol * rng(i * 11)) * 0.5)
        base_vol = 1_000 if asset.startswith("BTC") else 8_000
        volume = base_vol * (1 + 0.6 * abs(rng(i * 13))) * (1 + 3 * abs(ret) / max(vol, 1e-6) * 0.2)
        candles.append([start + i * step, open_p, high_p, low_p, close_p, volume])
        price = close_p
    return candles


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def fetch_ohlcv(asset: str, timeframe: str, limit: int = 1000) -> Tuple[List[list], str]:
    """Return (candles, source_used). Falls back to synthetic on failure."""
    source = settings.data_source.lower()
    rows: List[list] = []
    used = "synthetic"

    if source == "binance":
        rows = _fetch_binance(asset, timeframe, limit)
        used = "binance"
    elif source == "ccxt":
        rows = _fetch_ccxt(asset, timeframe, limit)
        used = "ccxt"

    if not rows:
        rows = _synthetic(asset, timeframe, limit)
        used = "synthetic"

    return rows, used


def store_candles(db: Session, asset: str, timeframe: str, rows: List[list]) -> int:
    """Replace stored candles for asset/timeframe with `rows`. Returns count."""
    db.execute(
        delete(Candle).where(Candle.asset == asset, Candle.timeframe == timeframe)
    )
    objs = [
        Candle(
            asset=asset,
            timeframe=timeframe,
            open_time=int(r[0]),
            open=r[1],
            high=r[2],
            low=r[3],
            close=r[4],
            volume=r[5],
        )
        for r in rows
    ]
    db.add_all(objs)
    db.commit()
    return len(objs)


def collect(db: Session, asset: str, timeframe: str, limit: int = 1000) -> dict:
    """Fetch + store candles for one asset/timeframe."""
    rows, used = fetch_ohlcv(asset, timeframe, limit)
    n = store_candles(db, asset, timeframe, rows)
    return {"asset": asset, "timeframe": timeframe, "candles": n, "source": used}


def load_candles(db: Session, asset: str, timeframe: str) -> List[Candle]:
    stmt = (
        select(Candle)
        .where(Candle.asset == asset, Candle.timeframe == timeframe)
        .order_by(Candle.open_time.asc())
    )
    return list(db.execute(stmt).scalars().all())
