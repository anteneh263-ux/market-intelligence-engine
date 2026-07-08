"""DataSource — the common market-data interface.

Every provider (CSV, Binance, FMP, CoinDesk, ...) implements this one interface.
The evaluation engine depends ONLY on this abstraction, never on a concrete
provider, so the data source is replaceable without changing evaluation code.

Contract (all providers must honour it):
  * Return bars sorted ascending by open_time, de-duplicated, gap-tolerant.
  * Return a normalized OHLCVFrame (identical columns/dtypes) regardless of source.
  * Never fabricate bars. A provider that cannot serve a request returns an
    empty frame with `is_real=<its truthfulness>`; it does not invent data.
  * Declare `is_real`: True for genuine market data, False for synthetic. The
    engine refuses to write a scientific verdict from `is_real=False` data.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

CANONICAL_COLUMNS = ["open_time", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class DataRequest:
    """A single, fully-specified data request. Immutable so it can be hashed
    into experiment provenance."""

    asset: str                 # provider-agnostic symbol, e.g. "BTC/USDT", "AAPL"
    timeframe: str             # "1h", "4h", "1d", ...
    start: Optional[str] = None  # ISO date "YYYY-MM-DD" (inclusive) or None
    end: Optional[str] = None    # ISO date "YYYY-MM-DD" (inclusive) or None
    limit: Optional[int] = None  # cap on number of bars (most-recent) if start/end absent


@dataclass
class OHLCVFrame:
    """Normalized result: a DataFrame plus honest provenance metadata."""

    df: pd.DataFrame                      # canonical columns, ascending open_time
    provider: str
    asset: str
    timeframe: str
    is_real: bool                         # True = genuine market data
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Enforce the canonical schema so downstream code is provider-blind.
        if self.df is None:
            self.df = pd.DataFrame(columns=CANONICAL_COLUMNS)
        missing = [c for c in CANONICAL_COLUMNS if c not in self.df.columns]
        if missing:
            raise ValueError(f"{self.provider}: frame missing columns {missing}")
        self.df = (
            self.df[CANONICAL_COLUMNS]
            .dropna(subset=["open", "high", "low", "close"])
            .drop_duplicates(subset=["open_time"])
            .sort_values("open_time")
            .reset_index(drop=True)
        )
        for c in ["open", "high", "low", "close", "volume"]:
            self.df[c] = self.df[c].astype(float)
        self.df["open_time"] = self.df["open_time"].astype("int64")

    @property
    def n(self) -> int:
        return len(self.df)


class DataSource(abc.ABC):
    """Abstract market-data provider. Implement `_fetch` and `is_real`."""

    name: str = "base"
    is_real: bool = False

    @abc.abstractmethod
    def _fetch(self, req: DataRequest) -> pd.DataFrame:
        """Return a DataFrame with the canonical columns (may be empty)."""

    def get(self, req: DataRequest) -> OHLCVFrame:
        raw = self._fetch(req)
        return OHLCVFrame(
            df=raw,
            provider=self.name,
            asset=req.asset,
            timeframe=req.timeframe,
            is_real=self.is_real,
            meta={"request": req.__dict__},
        )
