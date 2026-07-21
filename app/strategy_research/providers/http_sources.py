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


_TF_COINBASE = {"1h": 3600, "1d": 86400, "1w": 604800}  # granularity in seconds


def normalize_coinbase(rows) -> pd.DataFrame:
    """Normalize a Coinbase Exchange candles payload to the canonical OHLCV schema.

    Coinbase returns an array of arrays in the DOCUMENTED order
        [ time(seconds), low, high, open, close, volume ]
    typically NEWEST-FIRST. We map to canonical [open_time(ms), open, high, low,
    close, volume], sort ascending, and drop duplicate open_times. Malformed rows
    are skipped (never fabricated)."""
    out = []
    for k in rows:
        if not isinstance(k, (list, tuple)) or len(k) < 6:
            continue
        try:
            ts = int(k[0])
            low, high, opn, close, vol = (float(k[1]), float(k[2]), float(k[3]),
                                          float(k[4]), float(k[5]))
        except (TypeError, ValueError):
            continue
        out.append([ts * 1000, opn, high, low, close, vol])
    df = _rows_to_df(out)
    if df.empty:
        return df
    return (df.drop_duplicates(subset=["open_time"])
              .sort_values("open_time").reset_index(drop=True))


class CoinbaseDataSource(DataSource):
    """Coinbase Exchange public candles -- FREE, NO API key. USD spot pairs
    (e.g. BTC-USD, ETH-USD). This is an EXPLICIT provider: the user selects
    `--provider coinbase`; there is NO hidden automatic fallback from any other
    provider. Handles Coinbase's 300-candles-per-request cap by paginating
    backwards so SMA-200 strategies receive enough warm-up history.

    In a sandbox without outbound network it returns an empty frame (never
    fabricated bars); the engine then reports 'no completed candles' cleanly.
    """

    name = "coinbase"
    is_real = True
    _MAX_PER_REQ = 300

    def __init__(self, base_url: str = "https://api.exchange.coinbase.com"):
        self.base_url = base_url

    def _fetch(self, req: DataRequest) -> pd.DataFrame:
        if requests is None or req.timeframe not in _TF_COINBASE:
            return _rows_to_df([])
        import datetime as _dt

        gran = _TF_COINBASE[req.timeframe]
        product = req.asset.replace("/", "-").upper()   # BTC/USD -> BTC-USD
        want = int(req.limit or 300)
        raw: List[list] = []
        seen = set()
        end = None                                       # first page = most recent
        guard = 0
        while len(seen) < want and guard < 25:           # bounded pagination
            guard += 1
            params = {"granularity": gran}
            if end is not None:                          # older window
                start = end - _dt.timedelta(seconds=gran * self._MAX_PER_REQ)
                params["start"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
                params["end"] = end.strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                r = requests.get(f"{self.base_url}/products/{product}/candles",
                                 params=params, timeout=15,
                                 headers={"User-Agent": "mie-paper/1.0"})
                r.raise_for_status()
                rows = r.json()
            except Exception:
                break
            if not isinstance(rows, list) or not rows:
                break
            added = 0
            for k in rows:
                if isinstance(k, (list, tuple)) and len(k) >= 6:
                    ts = int(k[0])
                    if ts not in seen:
                        seen.add(ts)
                        raw.append(k)
                        added += 1
            if added == 0:                               # no forward progress
                break
            oldest = min(int(k[0]) for k in raw)
            end = _dt.datetime.fromtimestamp(oldest, _dt.timezone.utc)
        return normalize_coinbase(raw)
