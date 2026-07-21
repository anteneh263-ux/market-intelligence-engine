"""Frozen schema + pure helpers for the paper-trading ledger.

Everything here is deterministic (no wall-clock, no RNG) so runs are reproducible
and the append-only JSONL log can rebuild state byte-for-byte. All timestamps are
UTC. A daily candle's open_time is UTC-midnight ms epoch; its close is +1 day.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict

SCHEMA_VERSION = "paper-v1"
BUY_AND_HOLD_ID = "BUY_AND_HOLD"

TIMEFRAME_MS = {"1d": 86_400_000, "1h": 3_600_000, "4h": 14_400_000, "1w": 604_800_000}


def tf_ms(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    return TIMEFRAME_MS[timeframe]


def iso_utc(ms: int) -> str:
    """ms epoch -> ISO-8601 UTC string, e.g. 2026-01-02T00:00:00Z."""
    dt = datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def event_id(strategy_id: str, market: str, candle_open_ms: int) -> str:
    """Deterministic, unique per (account, candle). No wall-clock -> reproducible."""
    key = f"{SCHEMA_VERSION}|{strategy_id}|{market}|{int(candle_open_ms)}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def resolve_target(direction: str, raw_signal: float) -> int:
    """Map a raw strategy signal in {-1,0,+1} to a paper target position, respecting
    long-only strategies (a short signal becomes CASH, i.e. 0). Enforces the
    {-1,0,+1} domain (== max 1x exposure per side)."""
    s = 0
    if raw_signal > 0:
        s = 1
    elif raw_signal < 0:
        s = -1
    if direction == "long_only" and s < 0:
        s = 0
    return s


def drop_incomplete(df, step_ms: int, now_ms: int):
    """Return only COMPLETED candles: those whose close time (open_time + step) is at
    or before `now_ms`. Pure + deterministic (now_ms is passed in), so it is unit
    testable and never depends on a wall clock inside the accounting core."""
    if df is None or len(df) == 0:
        return df
    ot = df["open_time"].astype("int64")
    return df[ot + int(step_ms) <= int(now_ms)].reset_index(drop=True)
