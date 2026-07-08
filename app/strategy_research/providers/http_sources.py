"""Live HTTP providers: Binance, FMP, CoinDesk.

Each maps its vendor-specific response onto the canonical OHLCV schema. All are
`is_real = True`. In sandboxes without outbound network they simply return an
empty frame (never fabricated bars); the engine then reports "no real data" for
that request rather than inventing a result.

API keys are read from the environment (FMP_API_KEY, COINDESK_API_KEY) so no
secret is ever hard-coded. Adding a new vendor = one more subclass here; the
evaluation engine is untouched.
"""
from __future__ import annotations

import os
from typing import List

import pandas as pd

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

from .base import CANONICAL_COLUMNS, DataRequest, DataSource

_TF_BINANCE = {"1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w"}
_TF_FMP = {"1h": "1hour", "4h": "4hour", "1d": "1day"}


def _rows_to_df(rows: List[list]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS)


class BinanceDataSource(DataSource):
    name = "binance"
    is_real = True

    def __init__(self, base_url: str = "https://api.binance.com"):
        self.base_url = base_url

    def _fetch(self, req: DataRequest) -> pd.DataFrame:
        if requests is None or req.timeframe not in _TF_BINANCE:
            return _rows_to_df([])
        symbol = req.asset.replace("/", "")
        params = {"symbol": symbol, "interval": _TF_BINANCE[req.timeframe],
                  "limit": min(int(req.limit or 1000), 1000)}
        try:
            r = requests.get(f"{self.base_url}/api/v3/klines", params=params, timeout=10)
            r.raise_for_status()
            rows = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]),
                     float(k[4]), float(k[5])] for k in r.json()]
            return _rows_to_df(rows)
        except Exception:
            return _rows_to_df([])


class FMPDataSource(DataSource):
    """Financial Modeling Prep — equities/indices/crypto historical bars."""

    name = "fmp"
    is_real = True

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("FMP_API_KEY", "")

    def _fetch(self, req: DataRequest) -> pd.DataFrame:
        if requests is None or not self.api_key:
            return _rows_to_df([])
        sym = req.asset.replace("/", "")
        try:
            if req.timeframe in _TF_FMP and req.timeframe != "1d":
                url = f"https://financialmodelingprep.com/api/v3/historical-chart/{_TF_FMP[req.timeframe]}/{sym}"
                js = requests.get(url, params={"apikey": self.api_key}, timeout=15).json()
                recs = js if isinstance(js, list) else []
            else:
                url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{sym}"
                js = requests.get(url, params={"apikey": self.api_key}, timeout=15).json()
                recs = js.get("historical", []) if isinstance(js, dict) else []
            rows = []
            for d in recs:
                ts = int(pd.Timestamp(d["date"], tz="UTC").value // 1_000_000)
                rows.append([ts, float(d["open"]), float(d["high"]),
                             float(d["low"]), float(d["close"]), float(d.get("volume", 0.0))])
            return _rows_to_df(rows)
        except Exception:
            return _rows_to_df([])


class CoinDeskDataSource(DataSource):
    """CoinDesk spot OHLCV (formerly CryptoCompare data API)."""

    name = "coindesk"
    is_real = True

    def __init__(self, api_key: str | None = None,
                 base_url: str = "https://data-api.coindesk.com"):
        self.api_key = api_key or os.environ.get("COINDESK_API_KEY", "")
        self.base_url = base_url

    def _fetch(self, req: DataRequest) -> pd.DataFrame:
        if requests is None:
            return _rows_to_df([])
        unit = {"1h": "hours", "4h": "hours", "1d": "days"}.get(req.timeframe)
        if unit is None:
            return _rows_to_df([])
        instrument = req.asset.replace("/", "-")
        params = {"market": "cadli", "instrument": instrument,
                  "limit": min(int(req.limit or 2000), 2000), "aggregate": 4 if req.timeframe == "4h" else 1}
        if self.api_key:
            params["api_key"] = self.api_key
        try:
            url = f"{self.base_url}/index/cc/v1/historical/{unit}"
            js = requests.get(url, params=params, timeout=15).json()
            data = (js.get("Data") or []) if isinstance(js, dict) else []
            rows = [[int(d["TIMESTAMP"]) * 1000, float(d["OPEN"]), float(d["HIGH"]),
                     float(d["LOW"]), float(d["CLOSE"]), float(d.get("VOLUME", 0.0))]
                    for d in data]
            return _rows_to_df(rows)
        except Exception:
            return _rows_to_df([])
