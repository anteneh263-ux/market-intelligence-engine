"""Download the frozen H-030 universe ONCE and seal the holdout (docs/H-030_PREREGISTRATION.md).

Run from the repository root:  python scripts/fetch_h030_data.py
Writes  data_csv_h030/<TICKER>_1d.csv      (first bar .. 2024-12-31, evaluation)
        data_holdout_h030/<TICKER>_1d.csv  (2025-01-01 .. 2026-09-30, sealed; do not open)
        data_csv_h030/MANIFEST.json        (provenance, quality checks, SHA-256 of every file)
Refuses to run if either folder already exists: data is downloaded once, never refreshed.
Data files are not committed to Git (vendor data); commit only MANIFEST.json.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys

import pandas as pd

UNIVERSE = ["SPY", "IWM", "EFA", "EEM", "EWJ", "VGK",
            "SHY", "IEF", "TLT", "TIP",
            "LQD", "HYG", "EMB",
            "GLD", "SLV", "USO", "DBC", "DBA", "UNG",
            "VNQ", "RWX",
            "UUP", "FXE", "FXY"]
EVAL_END = "2024-12-31"
HOLDOUT_START, HOLDOUT_END = "2025-01-01", "2026-09-30"
EVAL_DIR, HOLD_DIR = "data_csv_h030", "data_holdout_h030"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def quality(df: pd.DataFrame) -> dict:
    r = df["close"].pct_change().abs()
    return {
        "rows": int(len(df)),
        "first": str(df["date"].iloc[0]) if len(df) else None,
        "last": str(df["date"].iloc[-1]) if len(df) else None,
        "nan_ohlc": int(df[["open", "high", "low", "close"]].isna().sum().sum()),
        "nonpositive_close": int((df["close"] <= 0).sum()),
        "dates_strictly_increasing": bool(df["date"].is_monotonic_increasing and df["date"].is_unique),
        "abs_daily_return_gt_25pct": int((r > 0.25).sum()),   # reported, never edited
    }


def main() -> int:
    for d in (EVAL_DIR, HOLD_DIR):
        if os.path.exists(d):
            print(f"{d} already exists — refusing to re-download (data is frozen).")
            return 1
    import yfinance as yf

    os.makedirs(EVAL_DIR)
    os.makedirs(HOLD_DIR)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest = {"hypothesis": "H-030", "source": "Yahoo Finance via yfinance, auto_adjust=True",
                "yfinance_version": yf.__version__, "pandas_version": pd.__version__,
                "download_started_utc": started, "eval_end": EVAL_END,
                "holdout": [HOLDOUT_START, HOLDOUT_END], "universe": UNIVERSE, "files": {}}
    for t in UNIVERSE:
        raw = yf.download(t, start="1990-01-01", end="2026-10-01", interval="1d",
                          auto_adjust=True, actions=False, progress=False, threads=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw.reset_index().rename(columns=str.lower)[["date", "open", "high", "low", "close", "volume"]]
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        # Epoch-seconds timestamp column (same convention as data_csv/*.csv); the CSV
        # provider's string-date path relies on Series.view, which pandas 3 removed.
        df.insert(0, "timestamp", (pd.to_datetime(df["date"]) - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1))
        parts = {EVAL_DIR: df[df["date"] <= EVAL_END],
                 HOLD_DIR: df[(df["date"] >= HOLDOUT_START) & (df["date"] <= HOLDOUT_END)]}
        for folder, part in parts.items():
            path = os.path.join(folder, f"{t}_1d.csv")
            part.drop(columns=["date"]).to_csv(path, index=False)
            manifest["files"][path] = {"sha256": sha256(path), **quality(part)}
        print(t, manifest["files"][os.path.join(EVAL_DIR, f"{t}_1d.csv")]["first"],
              len(parts[EVAL_DIR]), "eval rows,", len(parts[HOLD_DIR]), "holdout rows")
    manifest["download_finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    with open(os.path.join(EVAL_DIR, "MANIFEST.json"), "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    bad = {p: q for p, q in manifest["files"].items()
           if q["rows"] == 0 or q["nan_ohlc"] or q["nonpositive_close"] or not q["dates_strictly_increasing"]}
    print("quality problems:" if bad else "quality checks passed", json.dumps(bad, indent=1) if bad else "")
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
