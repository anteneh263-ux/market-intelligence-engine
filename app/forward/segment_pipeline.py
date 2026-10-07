"""WP2B-1 — Segment binding & metrics pipeline (deterministic, fail-closed, read-only) — hardened R1.

Binds VERIFIED journal evidence to bar-grid-aligned reporting segments and feeds SEGMENT-LOCAL inputs to
the frozen WP2B-0A ``metrics-v2`` functions. Pure: no wall clock, no RNG, no network, no live execution,
no mutable hidden state; inputs are never mutated; frozen Stage 1 / Stage 2A / WP2B-0A are consumed
unchanged.

Contract: WP2B-1 Control Matrix R2 + R3 mathematical corrections + R4 traceability addendum.

The ONLY public production entry point is ``build_segment_report_from_journal(journal_path, policy,
manifest)``: it reads events through the frozen ``read_verified_events(path, manifest_hash)`` (hash chain
verified) before any economic calculation, and rejects anything that is not a filesystem path (an event
list can never enter through it). ``_build_segment_report_from_events(...)`` is a PRIVATE, test-only
builder over already-verified fixture events; there is no public verification-bypass flag.

Evidence roles (disjoint, de-duplicated by event_id; all lists in journal ``seq`` order):
  A. segment_member_events        binding open_time in [start, end); the ONLY events for bar-local metrics
  B. carry_in_boundary_evidence   the single snapshot at start-timeframe, or initial_capital @ session_created
  C. historical_support_evidence  read-only audit: prefix-reconciliation orders + cross-boundary cycle lineage

Membership is keyed on the payload bar open_time (never the wrapper timestamp); causal order is ``seq``
(required exactly contiguous, 0-based: events[i].seq == i). No event with binding open_time >= end enters
any role.
"""
from __future__ import annotations

import hashlib
import json
import math
import numbers
import os
from typing import Dict, List, Optional, Tuple

from app.paper.session.ingest import timeframe_ms as _timeframe_ms
from app.forward.metrics_v2 import (
    average_holding_period,
    completed_trade_count_metric,
    exposure_time,
    net_return,
    segment_local_max_drawdown,
    turnover,
    win_rate,
    NOT_AVAILABLE,
)
from app.forward.trade_cycle_ledger import (
    LedgerError,
    build_cycle_ledger_from_orders,
    canonical_lots,
    position_tolerance,
    reconcile_against_snapshot,
)

_CASH_TOL = 1e-6
_PERMITTED_NONZERO = ("filled", "partial")
_BINDING_KEY = {"bar_ingested": "open_time", "order": "exec_open_time",
                "portfolio_snapshot": "open_time", "safe_exit": "open_time"}
_POLICY_KEYS = {"policy_version", "segment_type", "timeframe_ms", "boundary_rule", "allow_gaps", "segments"}
_SEG_KEYS = {"segment_id", "start_ms", "end_ms"}
_SNAPSHOT_REQUIRED = ("cash", "position_units", "equity", "realized_pnl", "fees_paid", "mark_price")
_MISSING_SEQ = 1 << 62   # sort key for a lineage fill id with no journal seq (kept deterministic)


class SegmentPipelineError(Exception):
    """Raised on a hard fail-closed condition (malformed journal, invalid policy/manifest)."""


# --------------------------------------------------------------------------- #
# numeric / hashing helpers                                                   #
# --------------------------------------------------------------------------- #
def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _nonneg_int(v) -> bool:
    return _is_int(v) and v >= 0


def _is_real(v) -> bool:
    return (not isinstance(v, bool)) and isinstance(v, numbers.Real)


def _finite(v) -> bool:
    return _is_real(v) and math.isfinite(float(v))


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha(obj) -> str:
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()


def _validate_snapshot_payload(sp) -> Optional[str]:
    """Full payload validation for EVERY portfolio_snapshot a valid segment consumes (interior AND
    boundary): cash, position_units, equity, realized_pnl, fees_paid, mark_price must all be present and
    finite (bool rejected), and mark_price must be positive. Returns None if valid, else a reason."""
    if not isinstance(sp, dict):
        return "snapshot payload is not a dict"
    for k in _SNAPSHOT_REQUIRED:
        if k not in sp:
            return f"missing required field {k!r}"
        if not _finite(sp[k]):
            return f"field {k!r} must be finite (bool rejected)"
    if float(sp["mark_price"]) <= 0:
        return "mark_price must be positive"
    return None


def _validate_safe_exit_payload(sp) -> Optional[str]:
    """Structural validation for a safe_exit payload, applied to EVERY safe_exit (terminal or not)
    before it is indexed. Returns None if valid, else a reason string."""
    if not isinstance(sp, dict):
        return "payload is not a dict"
    st = sp.get("order_status")
    if not (isinstance(st, str) and st):
        return "order_status must be a non-empty string"
    if not _finite(sp.get("position_units")):
        return "position_units must be finite (bool rejected)"
    mp = sp.get("mark_price")
    if not (_finite(mp) and float(mp) > 0):
        return "mark_price must be finite positive"
    if not _finite(sp.get("equity")):
        return "equity must be finite (bool rejected)"
    return None


def _cycle_fill_lineage(c) -> List[str]:
    """Complete fill-identity lineage a completed cycle owns: its direct ``fill_ids`` PLUS any inherited
    dust identities folded in via ``residual_carried_in.transferred_fill_ids`` (mirrors the frozen
    ledger's own ownership partition). Order preserved for the caller to seq-sort; may contain repeats
    which the caller de-duplicates."""
    ids = list(c.get("fill_ids", []))
    ci = c.get("residual_carried_in")
    if isinstance(ci, dict):
        ids.extend(ci.get("transferred_fill_ids", []))
    return ids


# --------------------------------------------------------------------------- #
# journal validation (identity / order / anchoring)                           #
# --------------------------------------------------------------------------- #
def _validate_journal(events, manifest_hash: str) -> str:
    if not isinstance(events, (list, tuple)) or not events:
        raise SegmentPipelineError("events must be a non-empty list of verified journal events")
    seen_ids = set()
    n_created = 0
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            raise SegmentPipelineError("journal event must be a dict")
        eid = e.get("event_id")
        if not (isinstance(eid, str) and eid):
            raise SegmentPipelineError("event_id must be a non-empty string")
        if eid in seen_ids:
            raise SegmentPipelineError(f"duplicate event_id {eid!r}")
        seen_ids.add(eid)
        seq = e.get("seq")
        if not _is_int(seq):
            raise SegmentPipelineError("seq must be an int (bool invalid)")
        if seq != i:
            raise SegmentPipelineError(f"seq must be contiguous 0-based (events[{i}].seq == {seq})")
        if not (isinstance(e.get("event_type"), str) and e["event_type"]):
            raise SegmentPipelineError("event_type must be a non-empty string")
        if not isinstance(e.get("payload"), dict):
            raise SegmentPipelineError("payload must be a dict")
        if not (isinstance(e.get("event_hash"), str) and e["event_hash"]):
            raise SegmentPipelineError("event_hash must be a non-empty string")
        if e["event_type"] == "session_created":
            n_created += 1
    if n_created != 1:
        raise SegmentPipelineError(f"exactly one session_created event required (found {n_created})")
    sc = next(e for e in events if e["event_type"] == "session_created")
    if sc["payload"].get("manifest_hash") != manifest_hash:
        raise SegmentPipelineError("session_created.manifest_hash does not match the supplied manifest")
    if sc["payload"].get("live_execution_enabled") is not False:
        raise SegmentPipelineError("live_execution_enabled must be False")
    return events[-1]["event_hash"]


def _extract_manifest(manifest) -> Tuple[float, float, int, str, int]:
    """Read the REAL frozen manifest schema strictly (as produced by build_session_manifest), including
    the bound ``timeframe`` string, which is converted to milliseconds by the FROZEN
    ``app.paper.session.ingest.timeframe_ms`` (deterministic table lookup; no wall clock/calendar). An
    unknown or malformed timeframe string fails closed."""
    if not isinstance(manifest, dict):
        raise SegmentPipelineError("manifest must be a dict")
    cap = manifest.get("initial_capital")
    if not (_finite(cap) and float(cap) > 0):
        raise SegmentPipelineError("manifest.initial_capital must be finite positive")
    ep = manifest.get("execution_profile")
    if not isinstance(ep, dict):
        raise SegmentPipelineError("manifest.execution_profile must be a dict")
    qs = ep.get("qty_step")
    if not (_finite(qs) and float(qs) > 0):
        raise SegmentPipelineError("manifest.execution_profile.qty_step must be finite positive")
    ss = manifest.get("start_timestamp_ms")
    if not _nonneg_int(ss):
        raise SegmentPipelineError("manifest.start_timestamp_ms must be a non-negative int")
    mh = manifest.get("manifest_hash")
    if not (isinstance(mh, str) and mh):
        raise SegmentPipelineError("manifest.manifest_hash must be a non-empty string")
    tf_str = manifest.get("timeframe")
    if not (isinstance(tf_str, str) and tf_str):
        raise SegmentPipelineError("manifest.timeframe must be a non-empty string")
    try:
        tf_ms = int(_timeframe_ms(tf_str))
    except (ValueError, KeyError, TypeError) as exc:
        raise SegmentPipelineError(f"manifest.timeframe {tf_str!r} is not a supported timeframe: {exc}")
    return float(cap), float(qs), int(ss), mh, tf_ms


# --------------------------------------------------------------------------- #
# policy validation + bar-grid alignment                                      #
# --------------------------------------------------------------------------- #
def _validate_policy(policy, bar_open_times: List[int], session_start: int,
                     manifest_tf: int) -> Tuple[int, List[Dict], bool]:
    if not isinstance(policy, dict):
        raise SegmentPipelineError("policy must be a dict")
    extra = set(policy.keys()) - _POLICY_KEYS
    if extra:
        raise SegmentPipelineError(f"unknown policy keys: {sorted(extra)}")
    if policy.get("policy_version") != "segment-policy-v1":
        raise SegmentPipelineError("policy_version must equal 'segment-policy-v1'")
    if policy.get("segment_type") != "bar_grid_intervals":
        raise SegmentPipelineError("segment_type must equal 'bar_grid_intervals'")
    if policy.get("boundary_rule") != "half_open[start,end)":
        raise SegmentPipelineError("boundary_rule must equal 'half_open[start,end)'")
    if not isinstance(policy.get("allow_gaps"), bool):
        raise SegmentPipelineError("allow_gaps must be an actual bool")
    allow_gaps = policy["allow_gaps"]
    tf = policy.get("timeframe_ms")
    if not (_is_int(tf) and tf > 0):
        raise SegmentPipelineError("timeframe_ms must be a positive int")
    if tf != manifest_tf:
        raise SegmentPipelineError(
            f"policy.timeframe_ms {tf} does not match the manifest timeframe {manifest_tf} ms")
    segments = policy.get("segments")
    if not (isinstance(segments, list) and segments):
        raise SegmentPipelineError("segments must be a non-empty list")
    O = sorted(set(bar_open_times))
    grid = set(O) | {session_start}
    if O:
        grid |= {O[-1] + tf}
    seen_ids = set()
    prev_end: Optional[int] = None
    norm: List[Dict] = []
    for s in segments:
        if not isinstance(s, dict):
            raise SegmentPipelineError("segment spec must be a dict")
        if set(s.keys()) - _SEG_KEYS:
            raise SegmentPipelineError(f"unknown segment keys: {sorted(set(s.keys()) - _SEG_KEYS)}")
        sid = s.get("segment_id")
        if not (isinstance(sid, str) and sid):
            raise SegmentPipelineError("segment_id must be a non-empty string")
        if sid in seen_ids:
            raise SegmentPipelineError(f"duplicate segment_id {sid!r}")
        seen_ids.add(sid)
        start, end = s.get("start_ms"), s.get("end_ms")
        if not (_is_int(start) and _is_int(end)):
            raise SegmentPipelineError("segment start_ms/end_ms must be ints (bool/float/NaN invalid)")
        if end <= start:
            raise SegmentPipelineError(f"segment {sid!r}: end_ms must be > start_ms")
        if start not in grid or end not in grid:
            raise SegmentPipelineError(f"segment {sid!r}: boundaries not aligned to the bar grid")
        if prev_end is not None:
            if start < prev_end:
                raise SegmentPipelineError("segments overlap / not strictly increasing")
            if (not allow_gaps) and start != prev_end:
                raise SegmentPipelineError("segments must be contiguous when allow_gaps is False")
        prev_end = end
        norm.append({"segment_id": sid, "start_ms": int(start), "end_ms": int(end)})
    for ot in O:
        hits = [s for s in norm if s["start_ms"] <= ot < s["end_ms"]]
        if len(hits) != 1:
            raise SegmentPipelineError(f"bar open_time {ot} maps to {len(hits)} segments (must be exactly 1)")
    return tf, norm, allow_gaps


# --------------------------------------------------------------------------- #
# public entry points                                                         #
# --------------------------------------------------------------------------- #
def build_segment_report_from_journal(journal_path, policy, manifest) -> Dict:
    """The ONLY public production entry point. ``journal_path`` MUST be a filesystem path; an event list
    (or any non-path object) is rejected fail-closed, so unverified events can never enter production.
    Reads events via the frozen ``read_verified_events`` (hash chain + manifest anchoring verified)
    before any economic calculation, then builds the report."""
    if not isinstance(journal_path, (str, bytes, os.PathLike)):
        raise SegmentPipelineError(
            "journal_path must be a filesystem path; an event list cannot enter the production API")
    _cap, _qs, _ss, manifest_hash, _tf = _extract_manifest(manifest)
    from app.paper.session.journal import read_verified_events, JournalError
    try:
        events = read_verified_events(journal_path, manifest_hash)
    except JournalError as exc:
        raise SegmentPipelineError(f"journal failed verification: {exc}")
    return _build(events, policy, manifest)


def _build_segment_report_from_events(events, policy, manifest) -> Dict:
    """PRIVATE, test-only builder over ALREADY-verified fixture events. Not part of the public API: no
    verification-bypass flag is exposed to production callers. Production code must use
    ``build_segment_report_from_journal``."""
    return _build(events, policy, manifest)


def _build(events, policy, manifest) -> Dict:
    cap, qs, session_start, manifest_hash, manifest_tf = _extract_manifest(manifest)
    final_hash = _validate_journal(events, manifest_hash)

    # binding-key validation (non-negative ints; bool invalid) + bar/snapshot/safe_exit duplicate counts
    bar_open_times: List[int] = []
    bar_ct: Dict[int, int] = {}
    snap_ct: Dict[int, int] = {}
    se_ct: Dict[int, int] = {}
    for e in events:
        et = e["event_type"]
        if et not in _BINDING_KEY:
            continue
        key = e["payload"].get(_BINDING_KEY[et])
        if not _nonneg_int(key):
            raise SegmentPipelineError(f"{et}.{_BINDING_KEY[et]} must be a non-negative int")
        if et == "bar_ingested":
            bar_open_times.append(key)
            bar_ct[key] = bar_ct.get(key, 0) + 1
        elif et == "portfolio_snapshot":
            snap_ct[key] = snap_ct.get(key, 0) + 1
        elif et == "safe_exit":
            se_ct[key] = se_ct.get(key, 0) + 1
    for ot, c in bar_ct.items():
        if c != 1:
            raise SegmentPipelineError(f"duplicate bar_ingested for open_time {ot}")

    # global safe_exit structural validation (NO dict hiding): every safe_exit payload is validated and
    # anchored to a bar BEFORE any indexing / E_end use; duplicates are recorded (not silently dropped
    # by an open_time-keyed dict) so any consuming segment fails closed even when it is not the E_end bar.
    dup_se_ots = {ot for ot, c in se_ct.items() if c > 1}
    for e in events:
        if e["event_type"] != "safe_exit":
            continue
        ot = e["payload"]["open_time"]
        if ot not in bar_ct:
            raise SegmentPipelineError(f"safe_exit at open_time {ot} has no matching bar_ingested")
        bad = _validate_safe_exit_payload(e["payload"])
        if bad is not None:
            raise SegmentPipelineError(f"malformed safe_exit at open_time {ot}: {bad}")

    tf, segments, allow_gaps = _validate_policy(policy, bar_open_times, session_start, manifest_tf)
    policy_hash = _sha(policy)

    order_events = [e for e in events if e["event_type"] == "order"]
    try:
        whole_ledger = build_cycle_ledger_from_orders(order_events, initial_capital=cap, qty_step=qs)
    except LedgerError as exc:
        raise SegmentPipelineError(f"journal order evidence invalid: {exc}")
    cycles = whole_ledger["cycles"]

    bars = sorted(((e["payload"]["open_time"], e["event_id"]) for e in events
                   if e["event_type"] == "bar_ingested"), key=lambda x: x[0])
    bar_index_of = {ot: i for i, (ot, _id) in enumerate(bars)}
    snap_by_ot = {e["payload"]["open_time"]: e for e in events if e["event_type"] == "portfolio_snapshot"}
    se_by_ot = {e["payload"]["open_time"]: e for e in events if e["event_type"] == "safe_exit"}
    order_ot_by_id = {e["event_id"]: e["payload"]["exec_open_time"] for e in order_events}
    order_by_id = {e["event_id"]: e for e in order_events}
    seq_by_id = {e["event_id"]: e["seq"] for e in events}

    ctx = dict(events=events, tf=tf, session_start=session_start, cap=cap, qs=qs,
               order_events=order_events, cycles=cycles, snap_by_ot=snap_by_ot, se_by_ot=se_by_ot,
               snap_ct=snap_ct, se_ct=se_ct, dup_se_ots=dup_se_ots, bar_index_of=bar_index_of,
               order_ot_by_id=order_ot_by_id, order_by_id=order_by_id, seq_by_id=seq_by_id,
               source_journal_final_hash=final_hash, policy_hash=policy_hash)

    seg_results = []
    for spec in segments:
        r = _safe_build_segment(spec, ctx)
        # identity fields live on every SegmentResult but OUTSIDE ``economic_hash`` (added post-hash),
        # so a segment's economic content still hashes identically across journals/policies.
        r["policy_hash"] = policy_hash
        r["source_journal_final_hash"] = final_hash
        seg_results.append(r)
    report = {"source_journal_final_hash": final_hash, "policy_hash": policy_hash,
              "policy_version": policy["policy_version"], "segments": seg_results}
    report["pipeline_hash"] = _sha(report)
    return report


def _invalid_segment(segment_id: str, reason: str) -> Dict:
    econ = {"segment_id": segment_id, "status": "INVALID", "reason": reason}
    return {**econ, "economic_hash": _sha(econ)}


def _safe_build_segment(spec, ctx) -> Dict:
    try:
        return _build_one_segment(spec, ctx)
    except (LedgerError, KeyError, TypeError, ValueError) as exc:  # never leak a raw exception
        return _invalid_segment(spec["segment_id"], f"segment evidence error: {type(exc).__name__}: {exc}")


def _build_one_segment(spec, ctx) -> Dict:
    sid = spec["segment_id"]
    start, end = spec["start_ms"], spec["end_ms"]
    events = ctx["events"]
    tf = ctx["tf"]
    qs = ctx["qs"]
    snap_by_ot = ctx["snap_by_ot"]
    se_by_ot = ctx["se_by_ot"]
    snap_ct = ctx["snap_ct"]
    se_ct = ctx["se_ct"]
    order_events = ctx["order_events"]

    # a duplicate safe_exit whose open_time falls inside this segment invalidates it (global, no dict
    # hiding) even when that open_time is NOT the E_end bar
    if any(start <= ot < end for ot in ctx["dup_se_ots"]):
        return _invalid_segment(sid, "duplicate safe_exit within segment range")

    # ---- member events (Role A), in journal seq order -------------------------------- #
    member_ids: List[str] = []
    for e in events:                     # events are in seq order (validated contiguous)
        et = e["event_type"]
        if et not in _BINDING_KEY:
            continue
        key = e["payload"].get(_BINDING_KEY[et])
        if start <= key < end:
            member_ids.append(e["event_id"])

    member_bars = sorted(e["payload"]["open_time"] for e in events
                         if e["event_type"] == "bar_ingested" and start <= e["payload"]["open_time"] < end)

    if not member_bars:
        econ = {"segment_id": sid, "status": "OK", "reason": "",
                "segment_member_event_ids": member_ids, "carry_in_evidence": None,
                "E_start_ref": None, "E_end_ref": None, "historical_support_evidence": [],
                "metrics": {m: {"status": NOT_AVAILABLE, "value": None, "reason": "empty segment"}
                            for m in ("net_return", "max_drawdown", "turnover", "exposure_time",
                                      "win_rate", "average_holding_period", "long_short_balance")}
                | {"completed_trade_count": {"status": "OK", "value": 0, "reason": ""}},
                "evidence_refs": {}, "cycle_indices": []}
        return {**econ, "economic_hash": _sha(econ)}

    last_ot = member_bars[-1]
    is_terminal_se = (last_ot in se_by_ot) and (last_ot not in snap_by_ot) \
        and se_by_ot[last_ot]["payload"].get("order_status") in _PERMITTED_NONZERO

    # ---- per-bar snapshot completeness (dup / missing / mismatch) --------------------- #
    for b_ot in member_bars:
        if b_ot == last_ot and is_terminal_se:
            continue
        if snap_ct.get(b_ot, 0) > 1:
            return _invalid_segment(sid, f"duplicate snapshot for bar {b_ot}")
        if b_ot not in snap_by_ot:
            return _invalid_segment(sid, f"missing interior snapshot for bar {b_ot}")
    member_ot = set(member_bars)
    for ot, c in snap_ct.items():
        if start <= ot < end and ot not in member_ot:
            return _invalid_segment(sid, f"snapshot without a bar at {ot}")

    support: Dict[str, Dict] = {}

    def _add_support(ev, reason, boundary_id=None, cycle_index=None):
        rec = support.get(ev["event_id"])
        if rec is None:
            rec = {"event_id": ev["event_id"], "seq": ev["seq"], "reason": [],
                   "related_boundary_event_id": boundary_id, "related_cycle_index": cycle_index}
            support[ev["event_id"]] = rec
        if reason not in rec["reason"]:
            rec["reason"].append(reason)
        if boundary_id is not None and rec.get("related_boundary_event_id") is None:
            rec["related_boundary_event_id"] = boundary_id
        if cycle_index is not None and rec.get("related_cycle_index") is None:
            rec["related_cycle_index"] = cycle_index

    def _prefix_support_orders(seq_cutoff, boundary_id):
        for o in order_events:
            if o["seq"] < seq_cutoff and o["payload"]["exec_open_time"] < start:
                _add_support(o, "prefix_reconciliation", boundary_id=boundary_id)

    def _prefix_ledger(seq_cutoff):
        pref = [o for o in order_events if o["seq"] < seq_cutoff]
        return build_cycle_ledger_from_orders(pref, initial_capital=ctx["cap"], qty_step=qs)

    # ---- E_start (carry-in, Role B) -------------------------------------------------- #
    if start == ctx["session_start"]:
        sc = next(e for e in events if e["event_type"] == "session_created")
        E_start = ctx["cap"]
        carry_in = {"kind": "session_created_initial_capital", "event_id": sc["event_id"],
                    "binding_open_time": ctx["session_start"], "seq_cutoff": sc["seq"], "E_start": E_start}
        E_start_ref = {"event_id": sc["event_id"], "seq_cutoff": sc["seq"]}
    else:
        carry_ot = start - tf
        if snap_ct.get(carry_ot, 0) > 1:
            return _invalid_segment(sid, f"duplicate carry-in snapshot at {carry_ot}")
        carry_snap = snap_by_ot.get(carry_ot)
        if carry_snap is None:
            return _invalid_segment(sid, f"required carry-in snapshot at {carry_ot} is absent")
        checks, ok = reconcile_against_snapshot(_prefix_ledger(carry_snap["seq"]), carry_snap["payload"])
        if not ok:
            return _invalid_segment(sid, f"carry-in snapshot fails prefix reconciliation: {checks}")
        E_start = float(carry_snap["payload"]["equity"])
        carry_in = {"kind": "prev_bar_snapshot", "event_id": carry_snap["event_id"],
                    "binding_open_time": carry_ot, "seq_cutoff": carry_snap["seq"], "E_start": E_start}
        E_start_ref = {"event_id": carry_snap["event_id"], "seq_cutoff": carry_snap["seq"]}
        _prefix_support_orders(carry_snap["seq"], carry_snap["event_id"])

    # ---- E_end ----------------------------------------------------------------------- #
    if is_terminal_se:
        if se_ct.get(last_ot, 0) != 1:
            return _invalid_segment(sid, f"ambiguous safe_exit at {last_ot}")
        se = se_by_ot[last_ot]
        bad = _validate_terminal_safe_exit(se, events, qs)
        if bad is not None:
            return _invalid_segment(sid, f"terminal safe-exit invalid: {bad}")
        order = events[se["seq"] - 1]
        E_end = float(se["payload"]["equity"])
        E_end_ref = {"kind": "terminal_safe_exit", "event_ids": [se["event_id"], order["event_id"]],
                     "seq_cutoff": order["seq"] + 1, "E_end": E_end}
        _prefix_support_orders(order["seq"] + 1, se["event_id"])
        terminal_pos = float(se["payload"]["position_units"])
        end_source_id = se["event_id"]
    else:
        end_snap = snap_by_ot[last_ot]
        checks, ok = reconcile_against_snapshot(_prefix_ledger(end_snap["seq"]), end_snap["payload"])
        if not ok:
            return _invalid_segment(sid, f"end snapshot fails prefix reconciliation: {checks}")
        E_end = float(end_snap["payload"]["equity"])
        E_end_ref = {"kind": "segment_snapshot", "event_ids": [end_snap["event_id"]],
                     "seq_cutoff": end_snap["seq"], "E_end": E_end}
        _prefix_support_orders(end_snap["seq"], end_snap["event_id"])
        terminal_pos = None
        end_source_id = end_snap["event_id"]

    # ---- metric inputs (bar-local) + per-bar source event ids ------------------------ #
    equity_path: List[float] = []
    flags: List[bool] = []
    bar_source_ids: List[str] = []
    for b_ot in member_bars:
        if b_ot == last_ot and is_terminal_se:
            equity_path.append(E_end)
            flags.append(canonical_lots(terminal_pos, qs) != 0)
            bar_source_ids.append(end_source_id)
        else:
            sp = snap_by_ot[b_ot]["payload"]
            bad = _validate_snapshot_payload(sp)   # full payload validation (interior AND boundary bars)
            if bad is not None:
                return _invalid_segment(sid, f"snapshot at {b_ot}: {bad}")
            equity_path.append(float(sp["equity"]))
            flags.append(canonical_lots(float(sp["position_units"]), qs) != 0)
            bar_source_ids.append(snap_by_ot[b_ot]["event_id"])
    n_bars = len(member_bars)

    seg_fill_ids: List[str] = []
    seg_fills = []
    for e in order_events:
        ot = e["payload"]["exec_open_time"]
        if start <= ot < end and e["payload"].get("status") in _PERMITTED_NONZERO \
                and abs(float(e["payload"].get("filled_units", 0.0))) > 0.0:
            seg_fills.append({"filled_units": float(e["payload"]["filled_units"]),
                              "fill_price": float(e["payload"]["fill_price"])})
            seg_fill_ids.append(e["event_id"])

    seg_cycles = []
    cycle_indices: List[int] = []
    lineage_seq: Dict[str, int] = {}          # complete per-cycle fill identity -> seq (dedup + order)
    seq_by_id = ctx["seq_by_id"]
    order_by_id = ctx["order_by_id"]
    for idx, c in enumerate(ctx["cycles"]):
        x = c.get("exit_exec_open_time")
        if x is not None and start <= x < end:
            seg_cycles.append(c)
            cycle_indices.append(idx)
            lineage = _cycle_fill_lineage(c)   # direct fill_ids + inherited (dust) transferred_fill_ids
            for fid in lineage:
                if fid not in lineage_seq:
                    lineage_seq[fid] = seq_by_id.get(fid, _MISSING_SEQ)
            if c.get("entry_exec_open_time") is not None and c["entry_exec_open_time"] < start:
                for fid in lineage:
                    fot = ctx["order_ot_by_id"].get(fid)
                    if fot is not None and fot < start:
                        oe = order_by_id.get(fid)
                        if oe is not None:
                            _add_support(oe, "cross_boundary_cycle_lineage", cycle_index=idx)
    # deterministic seq order (fill_id tie-break); complete lineage is audit evidence, never turnover
    cycle_fill_ids = [fid for fid, _s in sorted(lineage_seq.items(), key=lambda kv: (kv[1], kv[0]))]

    metrics = {
        "net_return": net_return(E_start, E_end),
        "max_drawdown": segment_local_max_drawdown(equity_path, E_start),
        "turnover": turnover(seg_fills, E_start),
        "exposure_time": exposure_time(flags, n_bars),
        "win_rate": win_rate(seg_cycles),
        "average_holding_period": average_holding_period(seg_cycles, ctx["bar_index_of"]),
        "completed_trade_count": completed_trade_count_metric(seg_cycles),
        "long_short_balance": {"status": NOT_AVAILABLE, "value": None,
                               "reason": "long-only (out of scope, U1)"},
    }

    cyc_ref = {"cycle_indices": cycle_indices, "fill_ids": cycle_fill_ids}
    evidence_refs = {
        "net_return": [E_start_ref["event_id"]] + list(E_end_ref["event_ids"]),
        "max_drawdown": list(bar_source_ids),
        "turnover": list(seg_fill_ids),
        "exposure_time": list(bar_source_ids),
        "win_rate": cyc_ref,
        "average_holding_period": cyc_ref,
        "completed_trade_count": {"cycle_indices": cycle_indices},
        "long_short_balance": [],
    }

    support_list = sorted(support.values(), key=lambda r: r["seq"])   # seq order (not lexicographic)
    for rec in support_list:
        rec["reason"] = sorted(rec["reason"])

    econ = {
        "segment_id": sid, "status": "OK", "reason": "",
        "segment_member_event_ids": member_ids,                 # seq order
        "carry_in_evidence": carry_in,
        "E_start_ref": E_start_ref, "E_end_ref": E_end_ref,
        "historical_support_evidence": support_list,
        "metrics": metrics, "evidence_refs": evidence_refs,
        "cycle_indices": cycle_indices,
    }
    return {**econ, "economic_hash": _sha(econ)}


def _validate_terminal_safe_exit(se, events, qs) -> Optional[str]:
    """Validate the terminal safe-exit + its linked CLOSE_ALL order (R3 equations), resolving the order
    by EXACT seq adjacency (events[se.seq-1]). Returns None if valid, else a reason string."""
    sp = se["payload"]
    if se["seq"] <= 0:
        return "safe_exit has no preceding event"
    order = events[se["seq"] - 1]
    if order["event_type"] != "order":
        return "event preceding safe_exit is not an order (ambiguous CLOSE_ALL linkage)"
    op = order["payload"]
    if op.get("exec_open_time") != sp.get("open_time"):
        return "order/safe_exit open_time mismatch"
    if op.get("status") not in _PERMITTED_NONZERO:
        return f"CLOSE_ALL order status {op.get('status')!r} not filled/partial"
    if sp.get("order_status") != op.get("status"):
        return "safe_exit.order_status does not agree with the linked order status"
    for k in ("cash_after", "position_after", "equity_after", "fill_price"):
        if not _finite(op.get(k)):
            return f"order.{k} not finite"
    for k in ("position_units", "mark_price", "equity"):
        if not _finite(sp.get(k)):
            return f"safe_exit.{k} not finite"
    tau = position_tolerance(qs)
    if abs(float(sp["position_units"]) - float(op["position_after"])) > tau:
        return "safe_exit.position_units mismatches order.position_after"
    if canonical_lots(float(op["position_after"]), qs) != 0:
        return "close result is not canonically flat"
    if abs(float(op["equity_after"]) - (float(op["cash_after"]) + float(op["position_after"]) * float(op["fill_price"]))) > _CASH_TOL:
        return "order.equity_after fails the order equation"
    if abs(float(sp["equity"]) - (float(op["cash_after"]) + float(sp["position_units"]) * float(sp["mark_price"]))) > _CASH_TOL:
        return "safe_exit.equity fails the safe-exit marked-equity equation"
    return None
