"""Sequential completed-bar ingestion and deterministic historical replay.

Each bar is validated in isolation, one at a time, in arrival order — there is no look-ahead
and no backward fill. A bar must have a finite, positive OHLC with valid geometry, a strictly
increasing unique UTC-ms ``open_time``, and be COMPLETE (an incomplete bar can never produce
an executable signal). Gaps are handled by a frozen policy that only counts MISSING TRADING
bars (weekends/closed sessions are expected for equities and gold, not treated as gaps).
"""
from __future__ import annotations

import math
from typing import Dict, Iterator, List, Optional, Tuple

import pandas as pd

from app.paper.session.profiles import is_trading_time

TF_MS: Dict[str, int] = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000,
}


def timeframe_ms(timeframe: str) -> int:
    if timeframe not in TF_MS:
        raise ValueError(f"unsupported timeframe {timeframe!r}; have {sorted(TF_MS)}")
    return TF_MS[timeframe]


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def validate_bar(bar: Dict, *, prev_open_time: Optional[int]) -> Tuple[bool, str]:
    """Validate a single completed bar in isolation. Returns (ok, reason)."""
    if not isinstance(bar, dict):
        return False, "bar is not a dict"
    ot = bar.get("open_time")
    if not (isinstance(ot, int) and not isinstance(ot, bool) and ot > 0):
        return False, "open_time must be a positive int (UTC epoch ms)"
    if bar.get("is_complete", True) is not True:
        return False, "incomplete bar cannot generate an executable signal"
    for k in ("open", "high", "low", "close"):
        if not _finite(bar.get(k)) or float(bar[k]) <= 0:
            return False, f"{k} must be finite and positive"
    if "volume" in bar and (not _finite(bar["volume"]) or float(bar["volume"]) < 0):
        return False, "volume must be finite and >= 0"
    o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
    if not (l <= h and l <= o <= h and l <= c <= h):
        return False, "invalid OHLC geometry (require low <= open/close <= high)"
    if prev_open_time is not None and ot <= prev_open_time:
        return False, "open_time not strictly increasing / unique"
    return True, ""


def count_missing_trading_bars(prev_open_time: int, open_time: int, *, timeframe: str,
                               profile: Dict) -> int:
    """Number of TRADING-calendar bars missing strictly between the two timestamps. Weekends /
    closed sessions are not counted, so an expected overnight/weekend gap is not a data stop."""
    step = timeframe_ms(timeframe)
    missing = 0
    t = prev_open_time + step
    guard = 0
    while t < open_time and guard < 1_000_000:
        if is_trading_time(profile, t):
            missing += 1
        t += step
        guard += 1
    return missing


def classify_gap(prev_open_time: Optional[int], open_time: int, *, timeframe: str,
                 profile: Dict, gap_policy: Dict) -> Dict:
    """Decide how a gap between bars is handled under the frozen policy. Returns
    {missing_trading_bars, data_gap_ms, action}. ``action`` is 'ok' or 'DATA_STOPPED'."""
    if prev_open_time is None:
        return {"missing_trading_bars": 0, "data_gap_ms": 0, "action": "ok"}
    missing = count_missing_trading_bars(prev_open_time, open_time, timeframe=timeframe, profile=profile)
    max_gap = int(gap_policy.get("max_gap_bars", 0))
    action = "DATA_STOPPED" if missing > max_gap else "ok"
    return {"missing_trading_bars": missing, "data_gap_ms": int(open_time - prev_open_time),
            "action": action}


def frame_to_bars(df: pd.DataFrame) -> List[Dict]:
    """Convert a canonical OHLCV frame into an ordered list of completed-bar dicts. Deterministic
    (row order preserved), no fill, no reorder."""
    cols = ["open_time", "open", "high", "low", "close", "volume"]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"frame missing columns {missing}")
    bars: List[Dict] = []
    for _, row in df.iterrows():
        bars.append({
            "open_time": int(row["open_time"]),
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row["volume"]), "is_complete": True,
        })
    return bars


def replay(bars: List[Dict]) -> Iterator[Dict]:
    """Yield completed bars one at a time, in order (deterministic historical replay)."""
    for bar in bars:
        yield bar


def dataset_hash(df: pd.DataFrame) -> str:
    """Deterministic content hash of an OHLCV dataset, so the runtime data can be bound to the
    frozen manifest. Uses the canonical columns and stable numeric formatting."""
    from app.forward.manifest import canonical_json, sha256_text
    cols = ["open_time", "open", "high", "low", "close", "volume"]
    rows = [[int(r["open_time"]), float(r["open"]), float(r["high"]), float(r["low"]),
             float(r["close"]), float(r["volume"])] for _, r in df[cols].iterrows()]
    return sha256_text(canonical_json(rows))
