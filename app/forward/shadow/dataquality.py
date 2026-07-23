"""Market-data quality validation for shadow-forward observations.

Fail-closed by design. No silent repair (interpolation, fill, NaN->0, row dropping), the
source frame is never mutated, and no conversion error escapes as an unstructured
exception. Frozen-evidence integrity fails closed: if deterministic content hashing cannot
be completed, the result is not PASS and does not count as evidence.

Classifications: PASS | NOT_AVAILABLE | INVALID | INVALIDATED.
All time checks operate on one documented unit: integer UTC epoch milliseconds.

Research and simulation only — no broker, no real orders, no private keys.
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

PASS = "PASS"
NOT_AVAILABLE = "NOT_AVAILABLE"
INVALID = "INVALID"
INVALIDATED = "INVALIDATED"

CANONICAL_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")
_OHLC = ("open", "high", "low", "close")
_RANK = {PASS: 0, NOT_AVAILABLE: 1, INVALID: 2, INVALIDATED: 3}

MIN_TS_MS = 946_684_800_000        # 2000-01-01 UTC
MAX_TS_MS = 4_102_444_800_000      # 2100-01-01 UTC
_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?([+-])(\d{2}):?(\d{2})$")
_MISSING = object()


def _strict_int_ms(v) -> Optional[int]:
    """A losslessly-parsed integer epoch-ms in range. Rejects bool, float, numeric strings
    and out-of-range values (no permissive int() conversion)."""
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    if v < MIN_TS_MS or v > MAX_TS_MS:
        return None
    return v


def _strict_pos_int(v) -> Optional[int]:
    """A strict positive int (rejects bool, float, numeric strings)."""
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v if v > 0 else None


def _valid_iso_dates(entries) -> Optional[set]:
    """Return a set of exact ISO YYYY-MM-DD dates, or None if ANY entry is not a canonical,
    real calendar date. Rejects non-strings (incl. datetime objects), impossible dates
    (2018-99-99), non-canonical strings, and mixed valid/invalid lists. Never raises."""
    out: set = set()
    for e in entries:
        # reject non-strings explicitly (datetime/date objects are not str)
        if not isinstance(e, str):
            return None
        if len(e) != 10 or e[4] != "-" or e[7] != "-":     # exact canonical shape
            return None
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
        if d.strftime("%Y-%m-%d") != e:                     # canonical round-trip (no 2018-1-1 etc.)
            return None
        out.add(e)
    return out


def is_finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _chk(check: str, status: str, *, asset=None, columns=None, rows=None, timestamps=None,
         measured=None, expected=None, explanation: str = "", blocking: Optional[bool] = None) -> Dict:
    if blocking is None:
        blocking = status in (INVALID, INVALIDATED)
    return {"check": check, "status": status, "asset": asset,
            "columns": list(columns) if columns else [], "rows": list(rows) if rows else [],
            "timestamps": list(timestamps) if timestamps else [], "measured": measured,
            "expected": expected, "explanation": explanation, "blocking": bool(blocking)}


def frame_content_sha256(df: pd.DataFrame) -> Optional[str]:
    """Deterministic, VALUE-sensitive content fingerprint. Never a weak columns+row-count
    hash. Returns None only if no deterministic content hash could be computed at all."""
    try:
        h = hashlib.sha256()
        h.update("|".join(map(str, df.columns)).encode())
        h.update(pd.util.hash_pandas_object(df, index=True).values.tobytes())
        return "hpo:" + h.hexdigest()
    except Exception:
        pass
    try:                                                # value-inclusive fallback (still content)
        return "csv:" + hashlib.sha256(df.to_csv(index=True).encode("utf-8")).hexdigest()
    except Exception:
        return None                                     # cannot hash -> caller fails closed


def classify_observation(value) -> str:
    if value is None:
        return NOT_AVAILABLE
    return PASS if is_finite(value) else NOT_AVAILABLE


# --------------------------------------------------------------------------- #
# timestamp / cutoff normalisation
# --------------------------------------------------------------------------- #
def normalize_timestamps(col: pd.Series, *, asset=None, tz_naive_policy: str = INVALID,
                         allow_float_ms: bool = False) -> Tuple[Dict, Optional[np.ndarray]]:
    """Normalise a timestamp column to int64 UTC epoch milliseconds. Returns a structured
    check plus the ms array (or None). No conversion error escapes."""
    try:
        s = col
        dtype = s.dtype
        if isinstance(dtype, pd.DatetimeTZDtype):
            if s.isna().any():
                return _ts_fail("NaT in a timezone-aware datetime column", asset), None
            idx = pd.DatetimeIndex(s).tz_convert("UTC")
            raw = np.asarray(idx.asi8, dtype="int64")
            unit = getattr(idx, "unit", "ns")
            if unit == "s":
                ms = raw * 1000
            elif unit == "ms":
                ms = raw
            elif unit in ("us", "ns"):
                div = 1000 if unit == "us" else 1_000_000
                if np.any(raw % div != 0):
                    return _ts_fail("sub-millisecond datetime precision (lossy to ms)", asset), None
                ms = raw // div
            else:
                return _ts_fail(f"unsupported datetime resolution {unit!r}", asset), None
            return _ts_range(ms, asset)
        if pd.api.types.is_datetime64_any_dtype(s):     # tz-naive
            if s.isna().any():
                return _ts_fail("NaT in a datetime column", asset), None
            status = tz_naive_policy if tz_naive_policy in (INVALID, NOT_AVAILABLE) else INVALID
            return _chk("timestamp_normalization", status, asset=asset, columns=["open_time"],
                        explanation="timezone-naive datetime; timezone cannot be asserted"), None
        if pd.api.types.is_integer_dtype(s) and not pd.api.types.is_bool_dtype(s):
            if s.isna().any():
                return _ts_fail("missing integer timestamp", asset), None
            return _ts_range(s.to_numpy("int64"), asset)
        if pd.api.types.is_float_dtype(s):
            arr = s.to_numpy(dtype=float)
            if not np.all(np.isfinite(arr)):
                return _ts_fail("NaN/infinite float timestamp", asset), None
            if not allow_float_ms:
                return _ts_fail("floating timestamps not permitted without an explicit "
                                "lossless conversion policy", asset), None
            if np.any(arr != np.floor(arr)):
                return _ts_fail("non-integer float timestamps (lossy to integer ms)", asset), None
            return _ts_range(arr.astype("int64"), asset)
        return _ts_fail("string / mixed / object timestamps are not accepted", asset), None
    except Exception as exc:
        return _ts_fail(f"timestamp normalization error: {type(exc).__name__}", asset), None


def _ts_fail(explanation: str, asset) -> Dict:
    return _chk("timestamp_normalization", INVALID, asset=asset, columns=["open_time"],
                explanation=explanation)


def _ts_range(ms: np.ndarray, asset) -> Tuple[Dict, Optional[np.ndarray]]:
    ms = ms.astype("int64")
    if np.any((ms < MIN_TS_MS) | (ms > MAX_TS_MS)):
        bad = np.where((ms < MIN_TS_MS) | (ms > MAX_TS_MS))[0]
        return _chk("timestamp_normalization", INVALID, asset=asset, columns=["open_time"],
                    rows=[int(i) for i in bad[:50]], measured=int(ms[bad[0]]),
                    expected=f"epoch ms in [{MIN_TS_MS}, {MAX_TS_MS}]",
                    explanation="timestamp outside a reasonable range"), None
    return _chk("timestamp_normalization", PASS, asset=asset, columns=["open_time"],
                explanation="normalised to integer UTC epoch milliseconds"), ms


def _normalize_cutoff(cutoff, asset) -> Tuple[Optional[int], Optional[Dict]]:
    """Safely normalise cutoff_ms. Malformed / floating / non-finite / out-of-range values
    return a structured INVALID check; no exception escapes."""
    if cutoff is None:
        return None, None
    try:
        if isinstance(cutoff, bool):
            return None, _chk("cutoff_normalization", INVALID, asset=asset,
                              explanation="boolean cutoff is not a valid timestamp")
        if isinstance(cutoff, float):
            return None, _chk("cutoff_normalization", INVALID, asset=asset, measured=cutoff,
                              explanation="floating cutoff not permitted (supply integer epoch ms)")
        if isinstance(cutoff, int):
            if cutoff < MIN_TS_MS or cutoff > MAX_TS_MS:
                return None, _chk("cutoff_normalization", INVALID, asset=asset, measured=cutoff,
                                  expected=f"epoch ms in [{MIN_TS_MS}, {MAX_TS_MS}]",
                                  explanation="cutoff out of a reasonable range")
            return cutoff, _chk("cutoff_normalization", PASS, asset=asset)
        return None, _chk("cutoff_normalization", INVALID, asset=asset,
                          explanation=f"non-numeric cutoff of type {type(cutoff).__name__}")
    except Exception:
        return None, _chk("cutoff_normalization", INVALID, asset=asset,
                          explanation="cutoff normalization error")


# --------------------------------------------------------------------------- #
# numeric source validation
# --------------------------------------------------------------------------- #
def _safe_numeric(col: pd.Series) -> np.ndarray:
    try:
        return pd.to_numeric(col, errors="coerce").to_numpy(dtype=float)
    except Exception:
        return np.full(len(col), np.nan, dtype=float)


def _check_prices(df: pd.DataFrame, ot: Optional[np.ndarray], asset, allow_negative_volume: bool) -> List[Dict]:
    out: List[Dict] = []
    arrs = {c: _safe_numeric(df[c]) for c in _OHLC}
    nonfinite = [c for c in _OHLC if not np.all(np.isfinite(arrs[c]))]
    if nonfinite:
        rows = sorted({int(i) for c in nonfinite for i in np.where(~np.isfinite(arrs[c]))[0]})[:50]
        out.append(_chk("finite_prices", INVALID, asset=asset, columns=nonfinite, rows=rows,
                        explanation="non-numeric or non-finite price(s): text, NaN or +/-infinity"))
    else:
        out.append(_chk("finite_prices", PASS, asset=asset, columns=list(_OHLC)))
    finite = np.all([np.isfinite(arrs[c]) for c in _OHLC], axis=0)
    nonpos = [c for c in _OHLC if np.any(finite & (arrs[c] <= 0))]
    if nonpos:
        rows = sorted({int(i) for c in nonpos for i in np.where(finite & (arrs[c] <= 0))[0]})[:50]
        out.append(_chk("positive_prices", INVALID, asset=asset, columns=nonpos, rows=rows,
                        expected="price > 0", explanation="zero or negative price where invalid"))
    else:
        out.append(_chk("positive_prices", PASS, asset=asset, columns=list(_OHLC)))
    o, h, low, c = arrs["open"], arrs["high"], arrs["low"], arrs["close"]
    fm = np.isfinite(o) & np.isfinite(h) & np.isfinite(low) & np.isfinite(c)
    bad = np.where(fm & ((h < low) | (h < np.maximum(o, c)) | (low > np.minimum(o, c))))[0]
    if bad.size:
        out.append(_chk("ohlc_relationships", INVALID, asset=asset, columns=list(_OHLC),
                        rows=[int(i) for i in bad[:50]],
                        expected="low <= min(open,close) <= max(open,close) <= high",
                        explanation="invalid OHLC relationship"))
    else:
        out.append(_chk("ohlc_relationships", PASS, asset=asset, columns=list(_OHLC)))
    vol = _safe_numeric(df["volume"])
    if not np.all(np.isfinite(vol)):
        out.append(_chk("finite_volume", INVALID, asset=asset, columns=["volume"],
                        rows=[int(i) for i in np.where(~np.isfinite(vol))[0][:50]],
                        explanation="non-numeric or non-finite volume"))
    elif (not allow_negative_volume) and np.any(vol < 0):
        out.append(_chk("nonnegative_volume", INVALID, asset=asset, columns=["volume"],
                        rows=[int(i) for i in np.where(vol < 0)[0][:50]],
                        expected="volume >= 0 unless the frozen source policy permits otherwise",
                        explanation="negative volume where invalid"))
    else:
        out.append(_chk("volume", PASS, asset=asset, columns=["volume"]))
    return out


# --------------------------------------------------------------------------- #
# cadence / session gap validation
# --------------------------------------------------------------------------- #
def _parse_tz(tz) -> Optional[tzinfo]:
    """Parse a frozen session timezone. Accepts a fixed offset ('-05:00', '+09:30', minutes
    int), 'UTC', or an IANA name via zoneinfo. Returns None if unusable (no raw exception)."""
    if tz is None:
        return None
    try:
        if isinstance(tz, tzinfo):
            return tz
        if isinstance(tz, int) and not isinstance(tz, bool):
            return timezone(timedelta(minutes=tz))
        s = str(tz).strip()
        m = _OFFSET_RE.match(s)
        if m:
            sign = 1 if m.group(1) == "+" else -1
            return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))
        if s.upper() in ("UTC", "GMT", "Z"):
            return timezone.utc
        from zoneinfo import ZoneInfo
        return ZoneInfo(s)
    except Exception:
        return None


def _cadence_checks(ot: np.ndarray, policy, asset, require_gap_validation: bool) -> List[Dict]:
    """Never raises. A missing / non-mapping / malformed policy returns a structured
    NOT_AVAILABLE (or INVALID) report; nothing escapes as an exception."""
    block = bool(require_gap_validation)
    if policy is None:
        return [_chk("missing_candles", NOT_AVAILABLE, asset=asset, blocking=block,
                     expected="an explicit, frozen cadence/session policy",
                     explanation="no cadence/session policy supplied; candle completeness "
                                 "not validated (a weekend/holiday is not assumed a gap)")]
    if not isinstance(policy, dict):
        return [_chk("missing_candles", NOT_AVAILABLE, asset=asset, blocking=True,
                     measured=type(policy).__name__, expected="a mapping (dict)",
                     explanation="cadence policy is not a mapping; candle completeness "
                                 "cannot be validated")]
    try:
        return _cadence_checks_impl(ot, policy, asset, block)
    except Exception as exc:                             # defensive: never raise
        return [_chk("missing_candles", NOT_AVAILABLE, asset=asset, blocking=True,
                     explanation=f"cadence policy error: {type(exc).__name__}")]


def _cadence_checks_impl(ot: np.ndarray, policy: Dict, asset, block: bool) -> List[Dict]:
    out: List[Dict] = []
    mode = policy.get("mode")
    cad = _strict_pos_int(policy.get("cadence_ms"))
    if cad is None:
        # a policy was SUPPLIED but its cadence is malformed -> fail closed (blocking)
        out.append(_chk("missing_candles", NOT_AVAILABLE, asset=asset, blocking=True,
                        measured=repr(policy.get("cadence_ms")),
                        expected="a positive integer cadence_ms",
                        explanation="malformed cadence_ms (must be a positive int; no permissive "
                                    "conversion of float/bool/string)"))
        return out
    if ot is None or ot.size < 2:
        out.append(_chk("missing_candles", NOT_AVAILABLE, asset=asset, blocking=block,
                        explanation="data insufficient to assess candle completeness"))
        return out
    diffs = np.diff(ot)
    short = np.where((diffs > 0) & (diffs < cad))[0]
    nonmult = np.where((diffs > cad) & (diffs % cad != 0))[0]
    if short.size or nonmult.size:
        idx = sorted(set(int(i) for i in list(short) + list(nonmult)))[:50]
        out.append(_chk("cadence_alignment", INVALID, asset=asset, columns=["open_time"],
                        rows=[i + 1 for i in idx], timestamps=[int(ot[i + 1]) for i in idx],
                        measured={"short_intervals": int(short.size),
                                  "non_multiple_intervals": int(nonmult.size)},
                        expected=f"every interval a positive multiple of cadence {cad} ms",
                        explanation="interval not a positive multiple of the cadence "
                                    "(short or non-multiple, e.g. a 1.5x-cadence gap)"))
    else:
        out.append(_chk("cadence_alignment", PASS, asset=asset, columns=["open_time"]))

    # ABSOLUTE grid alignment against a strictly-parsed frozen anchor. Under strict
    # validation (block) the anchor is REQUIRED: missing/malformed -> NOT_AVAILABLE.
    anchor_raw = policy.get("anchor_ms", _MISSING)
    anchor = _strict_int_ms(anchor_raw) if anchor_raw is not _MISSING else None
    if anchor is None:
        if block:
            out.append(_chk("cadence_grid_alignment", NOT_AVAILABLE, asset=asset, blocking=True,
                            measured=(None if anchor_raw is _MISSING else repr(anchor_raw)),
                            expected="explicit integer epoch-ms anchor_ms, lossless and in range",
                            explanation="missing or malformed anchor_ms under strict validation; "
                                        "absolute cadence-grid alignment cannot be validated"))
        elif anchor_raw is not _MISSING:
            out.append(_chk("cadence_grid_alignment", NOT_AVAILABLE, asset=asset, blocking=False,
                            measured=repr(anchor_raw),
                            explanation="malformed anchor_ms; absolute grid alignment not validated"))
    else:
        off = (ot - anchor) % cad
        mis = np.where(off != 0)[0]
        if mis.size:
            out.append(_chk("cadence_grid_alignment", INVALID, asset=asset, columns=["open_time"],
                            rows=[int(i) for i in mis[:50]], timestamps=[int(ot[i]) for i in mis[:50]],
                            measured=int(off[mis[0]]), expected=f"(open_time - {anchor}) % {cad} == 0",
                            explanation="timestamps not aligned to the frozen absolute cadence grid "
                                        "(e.g. the whole series shifted by a fraction of a cadence)"))
        else:
            out.append(_chk("cadence_grid_alignment", PASS, asset=asset, columns=["open_time"]))

    missing: List[int] = []
    if mode == "continuous":
        for i in range(diffs.size):
            if diffs[i] % cad == 0 and diffs[i] > cad:
                for k in range(1, int(diffs[i] // cad)):
                    missing.append(int(ot[i] + k * cad))
    elif mode == "sessions":
        tzobj = _parse_tz(policy.get("session_timezone"))
        if tzobj is None:
            out.append(_chk("missing_candles", NOT_AVAILABLE, asset=asset, blocking=block,
                            expected="an explicit, frozen session_timezone",
                            explanation="session policy without a usable frozen session_timezone; "
                                        "weekday/holiday calendar cannot be evaluated locally"))
            return out
        if "holidays" not in policy:
            out.append(_chk("missing_candles", NOT_AVAILABLE, asset=asset, blocking=block,
                            expected="an explicit holiday / non-session calendar",
                            explanation="session policy without an explicit holiday calendar "
                                        "cannot claim complete calendar validation"))
            return out
        wd_raw = policy.get("session_weekdays", [0, 1, 2, 3, 4])
        if not (isinstance(wd_raw, (list, tuple, set))
                and all(isinstance(w, int) and not isinstance(w, bool) and 0 <= w <= 6 for w in wd_raw)):
            out.append(_chk("missing_candles", NOT_AVAILABLE, asset=asset, blocking=True,
                            measured=repr(wd_raw), expected="a list of weekday ints in [0,6]",
                            explanation="malformed session_weekdays (fail closed)"))
            return out
        hol_raw = policy.get("holidays")
        if not isinstance(hol_raw, (list, tuple, set)):
            out.append(_chk("missing_candles", NOT_AVAILABLE, asset=asset, blocking=True,
                            measured=type(hol_raw).__name__, expected="a list of YYYY-MM-DD dates",
                            explanation="malformed holidays calendar (fail closed)"))
            return out
        holidays = _valid_iso_dates(hol_raw)
        if holidays is None:
            out.append(_chk("missing_candles", NOT_AVAILABLE, asset=asset, blocking=True,
                            measured=repr(list(hol_raw)[:5]),
                            expected="every entry an exact ISO calendar date YYYY-MM-DD",
                            explanation="malformed holiday entry (non-string, impossible or "
                                        "non-canonical date); fail closed"))
            return out
        weekdays = set(int(w) for w in wd_raw)
        for i in range(ot.size - 1):
            t = int(ot[i]) + cad
            while t < int(ot[i + 1]):
                local = datetime.fromtimestamp(t / 1000, tz=tzobj)
                if local.weekday() in weekdays and local.strftime("%Y-%m-%d") not in holidays:
                    missing.append(t)
                t += cad
    else:
        out.append(_chk("missing_candles", NOT_AVAILABLE, asset=asset, blocking=True,
                        measured=repr(mode), expected="'continuous' or 'sessions'",
                        explanation=f"unknown / missing cadence policy mode {mode!r} (fail closed)"))
        return out
    if missing:
        out.append(_chk("missing_candles", NOT_AVAILABLE, asset=asset, blocking=True,
                        timestamps=missing[:50], measured={"n_missing": len(missing)},
                        expected=f"contiguous {mode} candles at {cad} ms",
                        explanation=f"{len(missing)} missing candle(s) under the frozen {mode} "
                                    f"policy (data incomplete; never interpolated)"))
    else:
        out.append(_chk("missing_candles", PASS, asset=asset,
                        explanation=f"no missing candles under the frozen {mode} policy"))
    return out


# --------------------------------------------------------------------------- #
# top-level validation
# --------------------------------------------------------------------------- #
def validate_frame(df: pd.DataFrame, *, asset: Optional[str] = None,
                   expected_asset: Optional[str] = None, timeframe: str = "1d",
                   cutoff_ms=None, cadence_policy: Optional[Dict] = None,
                   require_gap_validation: bool = False,
                   required_columns: Optional[Sequence[str]] = None,
                   tz_naive_policy: str = INVALID, allow_float_ms: bool = False,
                   allow_negative_volume: bool = False, asset_identity_policy: str = INVALID,
                   frozen_reference_sha256: Optional[str] = None) -> Dict:
    checks: List[Dict] = []

    # frozen-evidence integrity FIRST (precedence), and FAIL CLOSED if it cannot hash
    if frozen_reference_sha256 is not None:
        cur = frame_content_sha256(df)
        if cur is None:
            checks.append(_chk("frozen_evidence_integrity", INVALID, asset=asset, blocking=True,
                               explanation="deterministic content hashing could not be completed; "
                                           "failing closed (frozen integrity unverifiable)"))
        elif cur != frozen_reference_sha256:
            checks.append(_chk("frozen_evidence_integrity", INVALIDATED, asset=asset,
                               measured=cur, expected=frozen_reference_sha256,
                               explanation="frame differs from previously-accepted frozen evidence "
                                           "(altered, truncated, emptied, column-dropped or replaced)"))
        else:
            checks.append(_chk("frozen_evidence_integrity", PASS, asset=asset))

    n = int(len(df))
    if n == 0:
        checks.append(_chk("non_empty", NOT_AVAILABLE, asset=asset, blocking=True,
                           measured=0, expected=">= 1 row", explanation="empty dataset"))
        return _report(asset, checks, n)

    req_cols = list(CANONICAL_COLUMNS) + [c for c in (required_columns or [])
                                          if c not in CANONICAL_COLUMNS]
    missing_cols = [c for c in req_cols if c not in df.columns]
    if missing_cols:
        checks.append(_chk("required_columns", INVALID, asset=asset, columns=missing_cols,
                           expected=f"columns {req_cols}",
                           explanation=f"missing required source columns {missing_cols}"))
        return _report(asset, checks, n)
    checks.append(_chk("required_columns", PASS, asset=asset))

    if asset is None or not str(asset).strip():
        # complete evidence requires a non-empty explicit asset identity (fail closed)
        if expected_asset is not None:
            checks.append(_chk("asset_identity", INVALID, asset=asset, expected=expected_asset,
                               explanation="expected_asset supplied but the asset identity is "
                                           "missing or blank"))
        else:
            status = asset_identity_policy if asset_identity_policy in (INVALID, NOT_AVAILABLE) else INVALID
            checks.append(_chk("asset_identity", status, asset=asset, blocking=True,
                               expected="a non-empty explicit asset identity",
                               explanation="missing or blank asset identity"))
    elif expected_asset is not None and expected_asset != asset:
        checks.append(_chk("asset_identity", INVALID, asset=asset, measured=asset,
                           expected=expected_asset, explanation="asset identity mismatch"))
    else:
        checks.append(_chk("asset_identity", PASS, asset=asset))

    ts_check, ot = normalize_timestamps(df["open_time"], asset=asset,
                                        tz_naive_policy=tz_naive_policy, allow_float_ms=allow_float_ms)
    checks.append(ts_check)

    if ot is not None:
        diffs = np.diff(ot) if ot.size > 1 else np.array([], dtype="int64")
        if diffs.size and np.any(diffs < 0):
            bad = [int(i + 1) for i in np.where(diffs < 0)[0][:50]]
            checks.append(_chk("chronological", INVALID, asset=asset, columns=["open_time"],
                               rows=bad, timestamps=[int(ot[i]) for i in bad],
                               expected="strictly increasing open_time", explanation="out-of-order timestamps"))
        else:
            checks.append(_chk("chronological", PASS, asset=asset, columns=["open_time"]))
        if ot.size != np.unique(ot).size:
            uniq, counts = np.unique(ot, return_counts=True)
            checks.append(_chk("unique_timestamps", INVALID, asset=asset, columns=["open_time"],
                               timestamps=[int(x) for x in uniq[counts > 1][:50]],
                               explanation="duplicate timestamps"))
        else:
            checks.append(_chk("unique_timestamps", PASS, asset=asset, columns=["open_time"]))
        cutoff_val, cutoff_check = _normalize_cutoff(cutoff_ms, asset)
        if cutoff_check is not None:
            checks.append(cutoff_check)
        if cutoff_val is not None:
            leak = np.where(ot <= int(cutoff_val))[0]
            if leak.size:
                checks.append(_chk("cutoff_separation", INVALID, asset=asset, columns=["open_time"],
                                   rows=[int(i) for i in leak[:50]],
                                   timestamps=[int(ot[i]) for i in leak[:50]], measured=int(ot[leak[0]]),
                                   expected=f"open_time > {int(cutoff_val)}",
                                   explanation="forward observation at or before the historical cutoff"))
            else:
                checks.append(_chk("cutoff_separation", PASS, asset=asset, columns=["open_time"]))
        checks.extend(_cadence_checks(ot, cadence_policy, asset, require_gap_validation))
    else:
        checks.append(_chk("chronological", NOT_AVAILABLE, asset=asset, blocking=True,
                           explanation="timestamps not normalisable; chronology not checked"))
        cutoff_val, cutoff_check = _normalize_cutoff(cutoff_ms, asset)
        if cutoff_check is not None:
            checks.append(cutoff_check)

    checks.extend(_check_prices(df, ot, asset, allow_negative_volume))
    return _report(asset, checks, n)


def _report(asset, checks: List[Dict], n_rows: int) -> Dict:
    overall = PASS
    for c in checks:
        blocking = c["status"] in (INVALID, INVALIDATED) or (
            c["status"] == NOT_AVAILABLE and c["blocking"])
        if blocking and _RANK[c["status"]] > _RANK[overall]:
            overall = c["status"]
    all_required = all(c["status"] == PASS for c in checks)
    return {
        "kind": "data_quality_report", "classification": overall, "asset": asset,
        "n_rows": int(n_rows), "all_required_checks_passed": bool(all_required),
        "counts_as_evidence": bool(overall == PASS and all_required),
        "pass_with_limitations": bool(overall == PASS and not all_required),
        "limitations": [f"{c['check']}: {c['explanation']}" for c in checks if c["status"] != PASS],
        "missing_checks": [c["check"] for c in checks if c["status"] == NOT_AVAILABLE],
        "checks": checks,
        "note": ("Failures are never silently repaired and the source frame is never modified. "
                 "Frozen integrity fails closed. counts_as_evidence requires PASS AND all "
                 "required checks passed."),
    }
