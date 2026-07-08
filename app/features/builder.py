"""Feature Builder — turn raw candles into an indicator-rich DataFrame."""
from __future__ import annotations

from typing import List

import pandas as pd

from app.database.models import Candle
from app.features.indicators import ema, rsi, volatility


def candles_to_df(candles: List[Candle]) -> pd.DataFrame:
    df = pd.DataFrame(
        [
            {
                "open_time": c.open_time,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ]
    )
    return df


def build_features(candles: List[Candle]) -> pd.DataFrame:
    """Compute the full feature set required by the Feature Builder spec.

    Features: RSI, EMA20, EMA50, EMA200, volume change, volatility, and
    forward/backward returns over 1, 6, 12, 24 candles.
    """
    df = candles_to_df(candles)
    if df.empty:
        return df

    df["ret"] = df["close"].pct_change().fillna(0.0)
    df["rsi"] = rsi(df["close"], 14)
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["volume_change"] = df["volume"].pct_change().fillna(0.0)
    df["volatility"] = volatility(df["ret"], 20).fillna(0.0)
    # rolling mean volatility to detect "low vol -> expansion" regimes
    df["vol_ma"] = df["volatility"].rolling(20).mean().fillna(0.0)

    # trailing returns over N candles (momentum features)
    for n in (1, 6, 12, 24):
        df[f"ret_{n}"] = df["close"].pct_change(n).fillna(0.0)

    # forward return over N candles (used only for exploratory stats, not signals)
    for n in (1, 6, 12, 24):
        df[f"fwd_ret_{n}"] = df["close"].shift(-n) / df["close"] - 1.0

    return df


def latest_feature_row(candles: List[Candle]) -> dict:
    df = build_features(candles)
    if df.empty:
        return {}
    row = df.iloc[-1]
    keys = [
        "close", "rsi", "ema20", "ema50", "ema200",
        "volume_change", "volatility", "ret_1", "ret_6", "ret_12", "ret_24",
    ]
    return {k: (None if pd.isna(row[k]) else float(row[k])) for k in keys}
