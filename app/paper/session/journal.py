"""Append-only, hash-chained evidence journal (``journal.jsonl``).

Every session event is one JSON line carrying: ``event_id``, ``seq`` (0-based, contiguous),
``timestamp`` (market time, ms — never a wall clock, so replay is deterministic),
``event_type``, ``payload``, ``parent_hash`` and ``event_hash``. ``event_hash`` is the
SHA-256 of the canonical event (excluding ``event_hash``); ``parent_hash`` is the previous
event's hash, and the genesis event is anchored to the session ``manifest_hash``. This binds
the whole chain to the frozen manifest.

Integrity model — read this honestly: the hash chain proves that no event was inserted,
dropped, reordered or edited *in isolation*, and it detects accidental corruption and naive
tampering. It is NOT a cryptographic signature: an attacker who can rewrite the entire file
can also recompute every hash and forge a self-consistent chain. For tamper-*evidence*
against such an adversary you need a signature or an external anchor; that is out of scope
for Phase 4 and is documented as a known limitation.

Fail-closed: a journal that does not verify (bad JSON, non-contiguous seq, broken parent
link, or a hash that does not reproduce) is rejected — no reader silently repairs it, and no
report is produced from it. Appends never overwrite; recovery is idempotent via ``event_id``.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

from app.forward.manifest import canonical_json, sha256_text

GENESIS_PARENT = "0" * 64


def compute_event_hash(event: Dict) -> str:
    """SHA-256 over the canonical event, excluding the ``event_hash`` field itself."""
    body = {k: v for k, v in event.items() if k != "event_hash"}
    return sha256_text(canonical_json(body))


class JournalError(Exception):
    """Raised when a journal fails closed (corruption / broken chain / misuse)."""


class Journal:
    """Append-only, hash-chained JSONL journal bound to a session manifest_hash."""

    def __init__(self, path: str, manifest_hash: str):
        if not isinstance(path, str) or not path:
            raise JournalError("journal path must be a non-empty string")
        if not isinstance(manifest_hash, str) or not manifest_hash:
            raise JournalError("manifest_hash must be a non-empty string")
        self.path = path
        self.manifest_hash = manifest_hash
        self._events: List[Dict] = []
        self._ids: set = set()
        self._last_hash: str = GENESIS_PARENT
        if os.path.exists(path):
            self._load_existing()

    # ------------------------------------------------------------------ #
    def _genesis_parent(self) -> str:
        # the first event's parent is the manifest hash, binding the chain to the manifest
        return sha256_text(canonical_json({"anchor": "manifest", "manifest_hash": self.manifest_hash}))

    def _load_existing(self) -> None:
        events = _read_raw(self.path)
        ok, reason, _ = _verify(events, self._genesis_parent())
        if not ok:
            raise JournalError(f"existing journal failed verification: {reason}")
        self._events = events
        self._ids = {e["event_id"] for e in events}
        self._last_hash = events[-1]["event_hash"] if events else GENESIS_PARENT

    # ------------------------------------------------------------------ #
    def append(self, event_type: str, payload: Dict, *, timestamp_ms: int,
               event_id: Optional[str] = None) -> Dict:
        """Append one event and return it. Idempotent: appending an ``event_id`` that already
        exists is a no-op that returns the existing event (so replay/recovery never
        duplicates)."""
        if not isinstance(event_type, str) or not event_type:
            raise JournalError("event_type must be a non-empty string")
        if not isinstance(payload, dict):
            raise JournalError("payload must be a dict")
        if not (isinstance(timestamp_ms, int) and not isinstance(timestamp_ms, bool)):
            raise JournalError("timestamp_ms must be an int (market time)")
        seq = len(self._events)
        eid = event_id if event_id is not None else f"{event_type}:{seq}"
        if eid in self._ids:
            for e in self._events:  # idempotent replay
                if e["event_id"] == eid:
                    return e
        parent = self._events[-1]["event_hash"] if self._events else self._genesis_parent()
        event = {
            "event_id": eid,
            "seq": seq,
            "timestamp": int(timestamp_ms),
            "event_type": event_type,
            "payload": payload,
            "parent_hash": parent,
        }
        event["event_hash"] = compute_event_hash(event)
        with open(self.path, "a", encoding="utf-8") as f:  # append-only, never truncates
            f.write(canonical_json(event) + "\n")
        self._events.append(event)
        self._ids.add(eid)
        self._last_hash = event["event_hash"]
        return event

    def has_event(self, event_id: str) -> bool:
        return event_id in self._ids

    def events(self) -> List[Dict]:
        return list(self._events)

    def verify(self) -> Tuple[bool, str, Optional[int]]:
        """Re-verify the in-memory chain. Returns (ok, reason, first_bad_seq)."""
        return _verify(self._events, self._genesis_parent())

    def summary(self) -> Dict:
        return {
            "manifest_hash": self.manifest_hash,
            "event_count": len(self._events),
            "first_event_hash": self._events[0]["event_hash"] if self._events else None,
            "final_event_hash": self._events[-1]["event_hash"] if self._events else None,
        }


# --------------------------------------------------------------------------- #
# module-level readers / verifiers (used by reports; never mutate the file)    #
# --------------------------------------------------------------------------- #
def _read_raw(path: str) -> List[Dict]:
    events: List[Dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise JournalError(f"corrupt JSON on line {i}: {exc}")
    return events


def _verify(events: List[Dict], genesis_parent: str) -> Tuple[bool, str, Optional[int]]:
    parent = genesis_parent
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            return False, f"event {i} is not an object", i
        for k in ("event_id", "seq", "timestamp", "event_type", "payload", "parent_hash", "event_hash"):
            if k not in e:
                return False, f"event {i} missing field {k!r}", i
        if e["seq"] != i:
            return False, f"event {i} has non-contiguous seq {e['seq']}", i
        if e["parent_hash"] != parent:
            return False, f"event {i} parent_hash breaks the chain", i
        if compute_event_hash(e) != e["event_hash"]:
            return False, f"event {i} event_hash does not reproduce (tampered/corrupt)", i
        parent = e["event_hash"]
    return True, "", None


def verify_journal_file(path: str, manifest_hash: str) -> Tuple[bool, str, Optional[int]]:
    """Verify an on-disk journal against a manifest hash WITHOUT mutating it. Fail-closed:
    any corruption returns (False, reason, first_bad_seq)."""
    if not os.path.exists(path):
        return False, "journal file does not exist", None
    try:
        events = _read_raw(path)
    except JournalError as exc:
        return False, str(exc), None
    genesis_parent = sha256_text(canonical_json({"anchor": "manifest", "manifest_hash": manifest_hash}))
    return _verify(events, genesis_parent)


def read_verified_events(path: str, manifest_hash: str) -> List[Dict]:
    """Return the events only if the whole chain verifies; otherwise raise JournalError.
    Reports MUST go through this so a corrupted journal can never yield a performance
    conclusion."""
    ok, reason, bad = _verify_and_read(path, manifest_hash)
    if not ok:
        raise JournalError(f"journal integrity failure at event #{bad}: {reason}")
    return _read_raw(path)


def _verify_and_read(path: str, manifest_hash: str) -> Tuple[bool, str, Optional[int]]:
    return verify_journal_file(path, manifest_hash)
