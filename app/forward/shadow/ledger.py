"""Tamper-evident, append-only event ledger for shadow-forward experiments.

Every event carries a hash-chain link. `event_hash` is computed deterministically from the
event's canonical JSON with the `event_hash` field itself excluded; `previous_event_hash`
points at the prior event's `event_hash` (genesis = 64 zeros). Verification therefore
detects changed, deleted or reordered INTERIOR events, broken previous-links, malformed
records, non-sequential ids and manifest mismatch.

A local chain alone CANNOT detect deletion of its own final event (the truncated file is
still internally consistent). To close that gap, `verify()` accepts an expected chain-head
hash and expected event count — typically taken from an independently-hashed checkpoint
receipt — and reports a mismatch. A local hash chain is TAMPER-EVIDENT, not immutable:
anchor a receipt's chain-head hash in Git or another external system MANUALLY. This module
never performs an automatic commit or external write.

Appends are serialized with an advisory file lock and refuse to extend a corrupt ledger.

Research and simulation only — no broker, no real orders, no private keys.
"""
from __future__ import annotations

import fcntl
import json
import os
from typing import Dict, List, Optional

from app.forward.manifest import canonical_json, sha256_text

GENESIS = "0" * 64
LEDGER_VERSION = "shadow-ledger-v1"

EVENT_FIELDS = ("event_id", "timestamp", "actor", "event_type", "old_status",
                "new_status", "reason", "manifest_hash", "evidence_hash",
                "previous_event_hash", "event_hash")
_REQUIRED_NONEMPTY = ("timestamp", "actor", "event_type", "reason", "manifest_hash")
_REQUIRED_KEYS = ("event_id", "timestamp", "actor", "event_type", "reason",
                  "manifest_hash", "previous_event_hash", "event_hash")


class LedgerError(RuntimeError):
    """Raised when the ledger is corrupt or an append would be unsafe."""


def compute_event_hash(event: Dict) -> str:
    """Deterministic hash of the event's canonical JSON EXCLUDING event_hash itself."""
    payload = {k: v for k, v in event.items() if k != "event_hash"}
    return sha256_text(canonical_json(payload))


def _event_id(seq: int) -> str:
    return f"E-{seq:05d}"


def make_event(*, seq: int, timestamp: str, actor: str, event_type: str,
               old_status: Optional[str], new_status: Optional[str], reason: str,
               manifest_hash: str, evidence_hash: Optional[str],
               previous_event_hash: str) -> Dict:
    event = {
        "event_id": _event_id(seq),
        "timestamp": timestamp,
        "actor": actor,
        "event_type": event_type,
        "old_status": old_status,
        "new_status": new_status,
        "reason": reason,
        "manifest_hash": manifest_hash,
        "evidence_hash": evidence_hash,
        "previous_event_hash": previous_event_hash,
    }
    event["event_hash"] = compute_event_hash(event)
    return event


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class ShadowLedger:
    """Append-only, hash-chained event log for one experiment."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.lock_path = path + ".lock"

    # ------------------------------------------------------------------ read --
    def read(self) -> List[Dict]:
        if not os.path.exists(self.path):
            return []
        out: List[Dict] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise LedgerError(f"malformed ledger record on line {lineno + 1}: {exc}")
        return out

    def head_hash(self) -> str:
        events = self.read()
        return events[-1].get("event_hash", GENESIS) if events else GENESIS

    # ---------------------------------------------------------------- verify --
    def verify(self, *, manifest_hash: Optional[str] = None,
               expected_head: Optional[str] = None,
               expected_count: Optional[int] = None) -> Dict:
        """Verify chain integrity. Detects mutated events, deleted/reordered interior
        events (broken previous-link), malformed records, non-sequential ids, missing/empty
        manifest_hash and manifest mismatch. Final-event deletion is caught only when
        `expected_head`/`expected_count` are supplied (e.g. from a checkpoint receipt)."""
        events = self.read()
        errors: List[Dict] = []
        prev = GENESIS
        for i, e in enumerate(events):
            missing = [k for k in _REQUIRED_KEYS if k not in e]
            if missing:
                errors.append({"index": i, "kind": "malformed_record",
                               "detail": f"missing fields {missing}"})
                prev = e.get("event_hash", prev)
                continue
            if e.get("event_id") != _event_id(i + 1):
                errors.append({"index": i, "kind": "non_sequential_event_id",
                               "detail": f"expected {_event_id(i + 1)}, got {e.get('event_id')}"})
            for field in _REQUIRED_NONEMPTY:
                if not e.get(field):
                    errors.append({"index": i, "kind": "empty_required_field",
                                   "detail": f"{field} is missing or empty"})
            if compute_event_hash(e) != e.get("event_hash"):
                errors.append({"index": i, "kind": "event_hash_mismatch",
                               "detail": "event content was altered after recording"})
            if e.get("previous_event_hash") != prev:
                errors.append({"index": i, "kind": "broken_previous_hash",
                               "detail": "previous_event_hash does not match the prior event "
                                         "(deletion, reordering or a broken link)"})
            if manifest_hash is not None and e.get("manifest_hash") != manifest_hash:
                errors.append({"index": i, "kind": "manifest_mismatch",
                               "detail": "event references a different manifest"})
            prev = e.get("event_hash")
        if expected_count is not None and len(events) != expected_count:
            errors.append({"index": None, "kind": "event_count_mismatch",
                           "detail": f"expected {expected_count} events, found {len(events)} "
                                     f"(possible final-event deletion or truncation)"})
        if expected_head is not None and prev != expected_head:
            errors.append({"index": None, "kind": "chain_head_mismatch",
                           "detail": "chain head differs from the expected/anchored head "
                                     "(possible final-event deletion or rewrite)"})
        return {"ok": not errors, "errors": errors, "head": prev, "n_events": len(events)}

    def verify_against_receipt(self, receipt: Dict, *, expected_forward_test_id: str,
                               expected_manifest_hash: str) -> Dict:
        """Verify the ledger against an independently-hashed checkpoint receipt AND bind it
        to the expected experiment. Validates: receipt hash, receipt kind, ledger version,
        expected forward_test_id, expected manifest hash, then chain-head + event count.
        A valid receipt from a DIFFERENT experiment is rejected."""
        errors: List[Dict] = []
        if not verify_receipt(receipt):
            errors.append({"index": None, "kind": "receipt_hash_mismatch",
                           "detail": "checkpoint receipt was altered"})
        if receipt.get("kind") != "shadow_checkpoint_receipt":
            errors.append({"index": None, "kind": "receipt_kind_mismatch",
                           "detail": f"unexpected receipt kind {receipt.get('kind')!r}"})
        if receipt.get("ledger_version") != LEDGER_VERSION:
            errors.append({"index": None, "kind": "ledger_version_mismatch",
                           "detail": f"unexpected ledger version {receipt.get('ledger_version')!r}"})
        if receipt.get("forward_test_id") != expected_forward_test_id:
            errors.append({"index": None, "kind": "receipt_experiment_mismatch",
                           "detail": "receipt belongs to a different experiment"})
        if receipt.get("manifest_hash") != expected_manifest_hash:
            errors.append({"index": None, "kind": "receipt_manifest_mismatch",
                           "detail": "receipt manifest hash does not match the expected manifest"})
        if errors:
            return {"ok": False, "errors": errors, "head": None, "n_events": None}
        chain = self.verify(manifest_hash=expected_manifest_hash,
                            expected_head=receipt.get("chain_head_hash"),
                            expected_count=receipt.get("n_events"))
        return chain

    # ---------------------------------------------------------------- append --
    def append(self, *, timestamp: str, actor: str, event_type: str,
               old_status: Optional[str], new_status: Optional[str], reason: str,
               manifest_hash: str, evidence_hash: Optional[str] = None) -> Dict:
        """Append one event. Serialized by an advisory file lock; verifies the existing
        chain first and refuses to extend a corrupt ledger; requires the mandatory fields."""
        for field, val in (("timestamp", timestamp), ("actor", actor),
                           ("event_type", event_type), ("reason", reason),
                           ("manifest_hash", manifest_hash)):
            if not val or not str(val).strip():
                raise LedgerError(f"event field {field!r} must be non-empty")

        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)          # serialize concurrent writers
            try:
                # bind the ledger to exactly one manifest: an existing chain must match
                # the incoming manifest hash, so a valid M1 ledger refuses an M2 append.
                current = self.verify(manifest_hash=manifest_hash)
                if not current["ok"]:
                    raise LedgerError(
                        f"refusing to append to a corrupt or manifest-mismatched ledger: "
                        f"{current['errors']}")
                events = self.read()
                prev = events[-1]["event_hash"] if events else GENESIS
                event = make_event(seq=len(events) + 1, timestamp=timestamp, actor=actor,
                                   event_type=event_type, old_status=old_status,
                                   new_status=new_status, reason=reason,
                                   manifest_hash=manifest_hash, evidence_hash=evidence_hash,
                                   previous_event_hash=prev)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(canonical_json(event) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                return event
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


# ------------------------------------------------------------- checkpoint receipts --
def compute_receipt_hash(receipt: Dict) -> str:
    payload = {k: v for k, v in receipt.items() if k != "receipt_hash"}
    return sha256_text(canonical_json(payload))


def checkpoint_receipt(*, forward_test_id: str, checkpoint: str, timestamp: str,
                       chain_head_hash: str, manifest_hash: str, n_events: int,
                       n_observations: int) -> Dict:
    """An independently-hashable checkpoint receipt that pins the current chain-head. Anchor
    it manually in Git or another external system — no automatic external write is done."""
    receipt = {
        "kind": "shadow_checkpoint_receipt",
        "ledger_version": LEDGER_VERSION,
        "forward_test_id": forward_test_id,
        "checkpoint": checkpoint,
        "timestamp": timestamp,
        "chain_head_hash": chain_head_hash,
        "manifest_hash": manifest_hash,
        "n_events": int(n_events),
        "n_observations": int(n_observations),
        "anchoring_note": ("Tamper-evident, not immutable. Anchor chain_head_hash in Git or "
                           "another external system manually; no automatic external write is "
                           "performed."),
    }
    receipt["receipt_hash"] = compute_receipt_hash(receipt)
    return receipt


def verify_receipt(receipt: Dict) -> bool:
    """True iff the receipt's own hash is intact."""
    return receipt.get("receipt_hash") == compute_receipt_hash(receipt)


def write_receipt(path: str, receipt: Dict) -> None:
    _atomic_write(path, canonical_json(receipt))


def verified_current_status(ledger: "ShadowLedger", *, manifest_hash: str,
                            initial_status: str) -> str:
    """Verify the ledger hash chain FIRST (bound to `manifest_hash`), then derive the
    current lifecycle status from the authenticated events. Hash verification always
    precedes lifecycle derivation — a corrupt or manifest-mismatched ledger raises before
    any status is derived."""
    from app.forward.shadow.lifecycle import derive_current_status
    v = ledger.verify(manifest_hash=manifest_hash)
    if not v["ok"]:
        raise LedgerError(f"cannot derive status from an unverified ledger: {v['errors']}")
    return derive_current_status(initial_status, ledger.read())
