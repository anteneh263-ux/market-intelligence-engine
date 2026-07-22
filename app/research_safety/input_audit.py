"""Declared-versus-actual input audit.

Deterministic instrumentation (NOT a source-text search): the strategy runs against a
TrackingFrame that records which OHLCV columns it ACTUALLY reads via df[col]. We then
compare three sets:

  * declared  - columns the strategy claims it uses (Strategy.declared_inputs or explicit)
  * prepared  - columns actually present in the data frame
  * read      - columns the strategy actually accessed at run time

and report deviations. A candidate is NOT promotable if it reads undeclared data, if a
declared source is missing, or if a *central* declared input is never read.
"""
from __future__ import annotations

from typing import Dict, Optional, Set

import pandas as pd


class TrackingFrame:
    """Transparent wrapper over an OHLCV DataFrame that records string column reads via
    __getitem__. Every other attribute/method is delegated to the real frame, so the
    strategy (and any indicator helper it passes the frame to) runs unchanged."""

    def __init__(self, df: pd.DataFrame) -> None:
        object.__setattr__(self, "_df", df)
        object.__setattr__(self, "_read", set())

    def __getitem__(self, key):
        rd = object.__getattribute__(self, "_read")
        if isinstance(key, str):
            rd.add(key)
        elif isinstance(key, (list, tuple)):
            for k in key:
                if isinstance(k, str):
                    rd.add(k)
        return object.__getattribute__(self, "_df")[key]

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_df"), name)

    def __len__(self) -> int:
        return len(object.__getattribute__(self, "_df"))

    @property
    def reads(self) -> Set[str]:
        return set(object.__getattribute__(self, "_read"))


def run_audit(strategy, df: pd.DataFrame, declared: Optional[Set[str]] = None,
              central: Optional[Set[str]] = None) -> Dict:
    """Run `strategy.positions` under instrumentation and compare declared/prepared/read.

    declared: columns the strategy claims to use (defaults to strategy.declared_inputs).
    central:  subset of declared whose reading a key claim depends on (must be read)."""
    prepared: Set[str] = set(map(str, df.columns))
    tf = TrackingFrame(df)
    strategy.positions(tf)                               # deterministic instrumentation
    read: Set[str] = tf.reads & prepared                 # only real data columns count

    if declared is None:
        declared = getattr(strategy, "declared_inputs", None)
    declared_set: Optional[Set[str]] = set(declared) if declared is not None else None
    central_set: Set[str] = set(central) if central else set()

    deviations = {
        "declared_not_used": sorted(declared_set - read) if declared_set is not None else [],
        "used_not_declared": sorted(read - declared_set) if declared_set is not None else sorted(read),
        "missing_source": sorted(declared_set - prepared) if declared_set is not None else [],
        "central_not_read": sorted(central_set - read),
    }
    declared_present = declared_set is not None
    promotable = bool(
        declared_present
        and not deviations["used_not_declared"]
        and not deviations["missing_source"]
        and not deviations["central_not_read"]
    )
    return {
        "kind": "input_audit",
        "strategy": getattr(strategy, "strategy_id", "?"),
        "declared": sorted(declared_set) if declared_set is not None else None,
        "prepared": sorted(prepared),
        "read": sorted(read),
        "central": sorted(central_set),
        "deviations": deviations,
        "declared_present": declared_present,
        "promotable": promotable,
    }
