"""CSVDataSource — load real OHLCV from a local CSV file.

This is the primary way to feed *real* data into the engine in a locked-down
environment: export candles from any exchange/vendor and drop the CSV in.

Accepted header (case-insensitive, flexible aliases):
  timestamp/open_time/date/time, open, high, low, close, volume
Timestamp may be ms epoch, s epoch, or an ISO/parseable date string.
"""
from __future__ import annotations

import os
from typing import Dict

import pandas as pd

from .base import CANONICAL_COLUMNS, DataRequest, DataSource

_ALIASES: Dict[str, str] = {
    "timestamp": "open_time", "open_time": "open_time", "opentime": "open_time",
    "date": "open_time", "time": "open_time", "datetime": "open_time",
    "o": "open", "open": "open",
    "h": "high", "high": "high",
    "l": "low", "low": "low",
    "c": "close", "close": "close", "adj close": "close", "adj_close": "close",
    "v": "volume", "vol": "volume", "volume": "volume",
}


def _to_ms(series: pd.Series) -> pd.Series:
    # Numeric epoch (s or ms) or parseable datetime -> int64 ms epoch.
    if pd.api.types.is_numeric_dtype(series):
        s = series.astype("int64")
        # Heuristic: values < 1e12 are seconds, else ms.
        return (s * 1000).where(s < 1_000_000_000_000, s)
    dt = pd.to_datetime(series, utc=True, errors="coerce")
    return (dt.view("int64") // 1_000_000).astype("int64")


class CSVDataSource(DataSource):
    name = "csv"
    is_real = True  # CSVs are assumed real market data (that is their purpose)

    def __init__(self, root: str = "data_csv"):
        self.root = root

    def _path(self, req: DataRequest) -> str:
        # <root>/<ASSET>_<timeframe>.csv, with '/' stripped from the asset.
        safe = req.asset.replace("/", "").replace(":", "")
        return os.path.join(self.root, f"{safe}_{req.timeframe}.csv")

    def _fetch(self, req: DataRequest) -> pd.DataFrame:
        path = req.__dict__.get("path") or self._path(req)
        if not os.path.exists(path):
            return pd.DataFrame(columns=CANONICAL_COLUMNS)
        raw = pd.read_csv(path)
        raw.columns = [str(c).strip().lower() for c in raw.columns]
        ren = {c: _ALIASES[c] for c in raw.columns if c in _ALIASES}
        raw = raw.rename(columns=ren)
        if "volume" not in raw.columns:
            raw["volume"] = 0.0
        missing = [c for c in CANONICAL_COLUMNS if c not in raw.columns]
        if missing:
            raise ValueError(f"CSV {path} missing required columns {missing}")
        raw["open_time"] = _to_ms(raw["open_time"])
        df = raw[CANONICAL_COLUMNS].copy()
        if req.start:
            df = df[df["open_time"] >= _to_ms(pd.Series([req.start]))[0]]
        if req.end:
            df = df[df["open_time"] <= _to_ms(pd.Series([req.end]))[0]]
        if req.limit:
            df = df.tail(int(req.limit))
        return df
