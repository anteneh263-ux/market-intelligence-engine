"""Append-only JSONL ledger.

One line per PROCESSED candle (an "event"). A line with traded=true is an executed
virtual trade; traded=false is a pure candle observation (watermark advance + new
pending signal). Because every line carries the full post-candle snapshot
(cash, units, position, pending signal, watermark), the JSONL alone can rebuild
state.json if it is deleted -- the log is the source of truth.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional


def account_key(strategy_id: str, market: str) -> str:
    return f"{strategy_id}__{market.replace('/', '-')}"


def log_path(log_dir: str, strategy_id: str, market: str) -> str:
    return os.path.join(log_dir, account_key(strategy_id, market) + ".jsonl")


def append_event(log_dir: str, strategy_id: str, market: str, event: Dict) -> None:
    os.makedirs(log_dir, exist_ok=True)
    path = log_path(log_dir, strategy_id, market)
    # Append-only: open in "a", write one compact JSON line, flush+fsync for durability.
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_events(log_dir: str, strategy_id: str, market: str) -> List[Dict]:
    path = log_path(log_dir, strategy_id, market)
    if not os.path.exists(path):
        return []
    out: List[Dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def reconstruct_account(log_dir: str, strategy_id: str, market: str) -> Optional[Dict]:
    """Rebuild an account's live state from its append-only log. Returns None if the
    account has no log yet. The last event holds the authoritative post-candle
    snapshot (cash, total_units, position, pending signal, watermark)."""
    events = read_events(log_dir, strategy_id, market)
    if not events:
        return None
    last = events[-1]
    return {
        "cash": last["cash"],
        "total_units": last["total_units"],
        "position": last["new_position"],
        "pending_signal": last["pending_signal"],
        "pending_signal_time_ms": last["pending_signal_time_ms"],
        "watermark_open_ms": last["candle_open_time_ms"],
        "last_close": last["close"],
        "n_events": len(events),
    }
