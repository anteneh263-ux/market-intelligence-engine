"""Full DSR trial-ledger.

The deflated Sharpe must be deflated by ALL real research attempts, not only the
strategies that survived. This append-only ledger records every trial - candidates,
controls, rejected rounds, parameter combinations, discovery variants - and marks
which ones count toward the data-snooping budget. Technical failures that never
produced a valid result are recorded but EXCLUDED.

Existing per-strategy DSR (the evaluation pipeline's `grid_size` budget) is unchanged;
`family_dsr()` here is an additional, stricter view over the whole ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

from app.strategy_research.dossier import _KB_ROOT
from app.strategy_research.evaluation.stats import deflated_sharpe

TRIAL_TYPES = {"candidate", "control", "rejected_round", "param_combo", "discovery_variant"}
STATUS_VALID = "valid"                     # produced a valid result -> counts toward DSR
STATUS_TECHNICAL_FAILURE = "technical_failure"  # never produced a valid result -> excluded


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _params_hash(params: Optional[Dict]) -> str:
    blob = json.dumps(params or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class TrialLedger:
    """Append-only JSONL ledger of every real research trial + an atomic summary JSON."""

    def __init__(self, root: Optional[str] = None,
                 clock: Callable[[], str] = _utc_now) -> None:
        base = os.path.join(root or _KB_ROOT, "research_safety")
        os.makedirs(base, exist_ok=True)
        self.path = os.path.join(base, "trial_ledger.jsonl")
        self.summary_path = os.path.join(base, "trial_ledger_summary.json")
        self._clock = clock

    def _read(self) -> List[Dict]:
        if not os.path.exists(self.path):
            return []
        out: List[Dict] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def record(self, strategy: str, trial_type: str, status: str = STATUS_VALID,
               params: Optional[Dict] = None, in_dsr: Optional[bool] = None,
               reason: str = "", ts: Optional[str] = None,
               trial_id: Optional[str] = None,
               economic_id: Optional[str] = None) -> Dict:
        """Record ONE trial. `in_dsr` defaults to True for a valid trial and is forced
        False for any non-valid trial (a technical failure can never count). `reason`
        explains any exclusion.

        `trial_id` is an OPTIONAL content-derived id for idempotency: if given and a
        trial with that id already exists, the existing record is returned unchanged and
        NOTHING is appended (so re-running an identical batch never duplicates trials).
        When omitted the id keeps its original append-order form ``T-00001`` and existing
        callers are unaffected."""
        if trial_type not in TRIAL_TYPES:
            raise ValueError(f"unknown trial_type {trial_type!r}; expected {sorted(TRIAL_TYPES)}")
        entries = self._read()
        if trial_id is not None:
            for e in entries:                          # idempotent: already recorded
                if e.get("trial_id") == trial_id:
                    return e
        if in_dsr is None:
            in_dsr = (status == STATUS_VALID)
        if status != STATUS_VALID:
            in_dsr = False                 # invalid trials never count toward DSR
        if not in_dsr and not reason:
            reason = ("technical failure produced no valid result"
                      if status != STATUS_VALID else "explicitly excluded from the DSR budget")
        rec: Dict = {
            "trial_id": trial_id if trial_id is not None else f"T-{len(entries) + 1:05d}",
            "strategy": strategy,
            "trial_type": trial_type,
            "status": status,
            "timestamp": ts or self._clock(),
            "params": params or {},
            "params_hash": _params_hash(params),
            "in_dsr": bool(in_dsr),
            "excluded_reason": "" if in_dsr else reason,
        }
        if economic_id is not None:
            rec["economic_id"] = economic_id
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._write_summary()
        return rec

    def entries(self) -> List[Dict]:
        return self._read()

    def count_dsr_trials(self) -> int:
        """Number of trials that count toward the data-snooping budget."""
        return sum(1 for e in self._read() if e.get("in_dsr"))

    @staticmethod
    def effective_key(entry: Dict) -> str:
        """Economic identity used for multiplicity accounting.

        New records carry an explicit economic_id. Legacy candidate/control/parameter
        records are conservatively reconciled by strategy + parameter hash, which
        removes exact reruns and candidate/default-parameter double counting without
        deleting or rewriting a historical line.
        """
        if entry.get("economic_id"):
            return str(entry["economic_id"])
        if entry.get("trial_type") in {"candidate", "control", "param_combo"}:
            return "legacy:" + str(entry.get("strategy")) + ":" + str(entry.get("params_hash"))
        return "trial:" + str(entry.get("trial_id"))

    def count_effective_dsr_trials(self) -> int:
        return len(self._effective_keys(self._read()))

    @classmethod
    def _effective_keys(cls, entries: Sequence[Dict]) -> set[str]:
        valid = [e for e in entries if e.get("in_dsr")]
        explicit_groups = {(str(e.get("strategy")), str(e.get("params_hash")))
                           for e in valid if e.get("economic_id")}
        keys: set[str] = set()
        for entry in valid:
            if entry.get("economic_id"):
                keys.add(str(entry["economic_id"]))
                continue
            group = (str(entry.get("strategy")), str(entry.get("params_hash")))
            if (entry.get("trial_type") in {"candidate", "control", "param_combo"}
                    and group in explicit_groups):
                # The first explicit economic identity supersedes the ambiguous legacy
                # representation for this strategy/config; history stays untouched.
                continue
            keys.add(cls.effective_key(entry))
        return keys

    def summary(self) -> Dict:
        ents = self._read()
        by_type: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        for e in ents:
            by_type[e["trial_type"]] = by_type.get(e["trial_type"], 0) + 1
            by_status[e["status"]] = by_status.get(e["status"], 0) + 1
        return {
            "total_trials": len(ents),
            "dsr_trials": sum(1 for e in ents if e.get("in_dsr")),
            "raw_dsr_trials": sum(1 for e in ents if e.get("in_dsr")),
            "effective_dsr_trials": len(self._effective_keys(ents)),
            "duplicate_dsr_records": (sum(1 for e in ents if e.get("in_dsr")) -
                                      len(self._effective_keys(ents))),
            "excluded_trials": sum(1 for e in ents if not e.get("in_dsr")),
            "by_type": by_type,
            "by_status": by_status,
        }

    def _write_summary(self) -> None:
        _atomic_write(self.summary_path, json.dumps(self.summary(), indent=2, sort_keys=True))

    def family_dsr(self, returns: Sequence[float], sr_trials: Sequence[float],
                   *, effective: bool = True) -> Dict:
        """Deflated Sharpe deflated by the FULL trial count from this ledger. The
        pipeline's per-strategy DSR is unchanged; this is a stricter, honest view."""
        n = max(self.count_effective_dsr_trials() if effective
                else self.count_dsr_trials(), 1)
        out = deflated_sharpe(returns, sr_trials, n)
        out["n_trials_source"] = ("trial_ledger_effective" if effective
                                  else "trial_ledger_raw")
        return out
