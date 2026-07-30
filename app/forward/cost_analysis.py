"""WP2B-2 — Gross vs. net cost analysis (deterministic, fail-closed, read-only).

Contract: WP2B-2 Control Matrix R4 + R4A. Decomposes verified paper-trading evidence into gross vs.
net performance and its friction components (fee, spread, slippage), proving — never inventing — the
gross result. Pure: no wall clock, no RNG, no network, no live execution, no mutable hidden state.
Frozen Stage 1 / WP2B-0A / WP2B-1 are consumed unchanged.

Key proved facts (from the frozen broker, see WP2B-2 audit): ``fill_price`` embeds spread+slippage
against the bar OPEN (the friction-free reference price, preserved in ``bar_ingested.open`` at the
fill's ``exec_open_time``); ``fee``/``spread_cost``/``slippage_cost`` are non-negative monetary
magnitudes; the ledger ``realized_gross`` is at FILL prices (spread/slippage-inclusive). Friction-free
gross is therefore reported ONLY for a QUANTITY_EXACT completed cycle
(``residual_carried_in is None and residual_carried_out is None``) whose independent reference-price
reconstruction (``-Σ filled_units·open``) agrees with ``realized_gross + owned_spread + owned_slippage``
and whose fee reconciliation ``realized_net == realized_gross - owned_fee`` holds; otherwise
NOT_AVAILABLE. Costs are attributed per fill by the frozen WP2B-1 half-open segment rule.

PRODUCTION entry: ``build_cost_analysis_from_journal(journal_path, policy, manifest)`` reads events via
the frozen ``read_verified_events`` (hash chain verified) and rejects any non-path input.
``_build_cost_analysis_from_events`` is PRIVATE and test-only.
"""
from __future__ import annotations

import hashlib
import json
import math
import numbers
import os
from typing import Dict, List, Optional, Tuple

from app.forward import segment_pipeline as _segp
from app.forward.trade_cycle_ledger import (
    LedgerError,
    build_cycle_ledger_from_orders,
    validate_fill_partition,
    _cycle_owned_ids,
)
from app.paper.session.profiles import FEE_BPS, FEE_PER_SHARE, execution_profile_hash

_TOL_CUR = 1e-6           # currency / cost / reconciliation tolerance
_TOL_PX_REL = 1e-9        # price relative tolerance
_TOL_PX_ABS = 1e-6        # price absolute tolerance
_PERMITTED_STATUS = ("filled", "partial", "no_trade", "rejected")
_NONZERO_STATUS = ("filled", "partial")
_PERMITTED_SIDE = ("buy", "sell", "flat", "hold")


class CostAnalysisError(Exception):
    """Raised on a hard fail-closed condition (malformed evidence, partition/coverage violation)."""


# --------------------------------------------------------------------------- #
# numeric / hashing helpers                                                   #
# --------------------------------------------------------------------------- #
def _is_real(v) -> bool:
    return (not isinstance(v, bool)) and isinstance(v, numbers.Real)


def _finite(v) -> bool:
    return _is_real(v) and math.isfinite(float(v))


def canonical_bytes(obj) -> bytes:
    """The ONE authoritative canonical serialization for WP2B-2: UTF-8, sorted keys, compact
    separators, ``ensure_ascii=False``. Every hash and every byte-equality comparison uses exactly
    these bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _canon(obj) -> str:
    return canonical_bytes(obj).decode("utf-8")


def _sha(obj) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def _cell(status: str, value):
    return {"status": status, "value": value}


def _bucket(fee: float, spread: float, slip: float) -> Dict:
    return {"fee_cost": float(fee), "spread_cost": float(spread), "slippage_cost": float(slip),
            "total_friction_cost": float(fee + spread + slip)}


def _alloc(status: str, fee, spread, slip, **extra) -> Dict:
    """Allocated-cost cell with short keys (fee/spread/slippage/total) per the R4 cycle_results spec."""
    if fee is None:
        return {"status": status, "fee": None, "spread": None, "slippage": None, "total": None, **extra}
    return {"status": status, "fee": float(fee), "spread": float(spread), "slippage": float(slip),
            "total": float(fee + spread + slip), **extra}


# --------------------------------------------------------------------------- #
# public / private entry points                                               #
# --------------------------------------------------------------------------- #
def build_cost_analysis_from_journal(journal_path, policy, manifest) -> Dict:
    """PRODUCTION entry. ``journal_path`` MUST be a filesystem path (an event list is rejected)."""
    if not isinstance(journal_path, (str, bytes, os.PathLike)):
        raise CostAnalysisError(
            "journal_path must be a filesystem path; an event list cannot enter the production API")
    try:
        _c, _q, _s, mh, _t = _segp._extract_manifest(manifest)
    except _segp.SegmentPipelineError as exc:
        raise CostAnalysisError(str(exc))
    from app.paper.session.journal import read_verified_events, JournalError
    try:
        events = read_verified_events(journal_path, mh)
    except JournalError as exc:
        raise CostAnalysisError(f"journal failed verification: {exc}")
    return _build(events, policy, manifest)


def _build_cost_analysis_from_events(events, policy, manifest) -> Dict:
    """PRIVATE, test-only builder over ALREADY-verified fixture events."""
    return _build(events, policy, manifest)


# --------------------------------------------------------------------------- #
# strict order-payload validation (runs on EVERY order, before classification) #
# --------------------------------------------------------------------------- #
def _validate_order_payload_strict(order_ev) -> Tuple[float, str, bool]:
    """Validate an order payload by its status BEFORE deciding whether it is a non-zero fill. Fail
    closed on any malformed field, status/units contradiction, or side/sign mismatch. Returns
    (signed_filled_units, status, is_nonzero_fill)."""
    eid = order_ev.get("event_id")
    p = order_ev.get("payload")
    if not isinstance(p, dict):
        raise CostAnalysisError(f"{eid}: order payload must be a dict")
    st = p.get("status")
    if st not in _PERMITTED_STATUS:
        raise CostAnalysisError(f"{eid}: unknown/unpermitted order status {st!r}")
    if "filled_units" not in p:
        raise CostAnalysisError(f"{eid}: missing filled_units")
    u = p["filled_units"]
    if not _finite(u):                     # rejects bool / str / None / NaN / Infinity
        raise CostAnalysisError(f"{eid}: filled_units must be a finite real number")
    uu = float(u)
    ot = p.get("exec_open_time")
    if not (isinstance(ot, int) and not isinstance(ot, bool) and ot >= 0):
        raise CostAnalysisError(f"{eid}: exec_open_time must be a non-negative int timestamp")
    side = p.get("side")
    if side not in _PERMITTED_SIDE:
        raise CostAnalysisError(f"{eid}: side {side!r} not in the permitted enum")
    for k in ("fill_price", "fee", "spread_cost", "slippage_cost"):
        if k not in p:
            raise CostAnalysisError(f"{eid}: missing required field {k!r}")
        if not _finite(p[k]):
            raise CostAnalysisError(f"{eid}: field {k!r} must be a finite real number")
    for k in ("fee", "spread_cost", "slippage_cost"):
        if float(p[k]) < 0.0:
            raise CostAnalysisError(f"{eid}: cost field {k!r} must be non-negative")
    nonzero = uu != 0.0
    if st in _NONZERO_STATUS and not nonzero:
        raise CostAnalysisError(f"{eid}: status {st!r} must have non-zero filled_units")
    if st not in _NONZERO_STATUS and nonzero:
        raise CostAnalysisError(f"{eid}: status {st!r} must have zero filled_units")
    if not nonzero:
        for k in ("fee", "spread_cost", "slippage_cost"):
            if float(p[k]) != 0.0:
                raise CostAnalysisError(f"{eid}: {st!r} order may not carry a non-zero {k}")
    else:
        if uu > 0 and side != "buy":
            raise CostAnalysisError(f"{eid}: positive filled_units requires side 'buy' (got {side!r})")
        if uu < 0 and side != "sell":
            raise CostAnalysisError(f"{eid}: negative filled_units requires side 'sell' (got {side!r})")
    return uu, st, nonzero


# --------------------------------------------------------------------------- #
# per-fill cost evidence                                                       #
# --------------------------------------------------------------------------- #
def _validate_and_make_evidence(order_ev, bar_index, prof, final_hash) -> Dict:
    """Build CostEvidence for one already-strictly-validated non-zero fill, binding an unambiguous
    reference bar. Fail closed on missing/duplicate/future reference bars or inconsistent cost."""
    p = order_ev["payload"]
    eid = order_ev["event_id"]
    uu = float(p["filled_units"])
    fill_price = float(p["fill_price"])
    if not (fill_price > 0):
        raise CostAnalysisError(f"{eid}: fill_price must be positive for a fill")
    ot = int(p["exec_open_time"])
    rec = bar_index.get(ot)
    if rec is None:
        raise CostAnalysisError(f"{eid}: no bar_ingested.open (reference price) at exec_open_time {ot}")
    if rec["count"] != 1:
        raise CostAnalysisError(f"{eid}: {rec['count']} bar_ingested share open_time {ot} (ambiguous ref)")
    if rec["seq"] >= int(order_ev["seq"]):
        raise CostAnalysisError(f"{eid}: reference bar (seq {rec['seq']}) does not precede the order")
    ref = float(rec["open"])
    if not (_finite(ref) and ref > 0):
        raise CostAnalysisError(f"{eid}: reference price must be finite positive")
    ref_bar_id = rec["event_id"]

    sign = 1.0 if uu > 0 else -1.0
    hs = float(prof["half_spread_bps"]); sl = float(prof["slippage_bps"])
    exp_fill = ref * (1.0 + sign * (hs + sl) / 1e4)
    if abs(float(fill_price) - exp_fill) > (_TOL_PX_ABS + _TOL_PX_REL * abs(exp_fill)):
        raise CostAnalysisError(f"{eid}: fill_price inconsistent with reference price")
    exp_spread = abs(uu) * ref * hs / 1e4
    exp_slip = abs(uu) * ref * sl / 1e4
    if abs(float(p["spread_cost"]) - exp_spread) > _TOL_CUR:
        raise CostAnalysisError(f"{eid}: spread_cost inconsistent with reference price/units")
    if abs(float(p["slippage_cost"]) - exp_slip) > _TOL_CUR:
        raise CostAnalysisError(f"{eid}: slippage_cost inconsistent with reference price/units")
    if prof["fee_model"] == FEE_BPS:
        exp_fee = abs(uu) * float(fill_price) * float(prof["taker_fee_bps"]) / 1e4
    elif prof["fee_model"] == FEE_PER_SHARE:
        exp_fee = max(float(prof["min_commission"]), abs(uu) * float(prof["commission_per_unit"]))
    else:
        raise CostAnalysisError(f"{eid}: unknown fee_model")
    if abs(float(p["fee"]) - exp_fee) > _TOL_CUR:
        raise CostAnalysisError(f"{eid}: fee inconsistent with the frozen execution profile")

    fee, spread, slip = float(p["fee"]), float(p["spread_cost"]), float(p["slippage_cost"])
    return {"order_event_id": eid, "seq": int(order_ev["seq"]), "exec_open_time": int(ot),
            "signed_filled_units": uu, "reference_price": ref,
            "reference_bar_event_id": ref_bar_id, "fill_price": float(fill_price),
            "fee": fee, "spread_cost": spread, "slippage_cost": slip,
            "total_cost": float(fee + spread + slip), "segment_id": None,
            "source_journal_final_hash": final_hash}


def _sum_costs(ids, evidence) -> Dict:
    fee = sum(evidence[i]["fee"] for i in ids)
    spread = sum(evidence[i]["spread_cost"] for i in ids)
    slip = sum(evidence[i]["slippage_cost"] for i in ids)
    return _bucket(fee, spread, slip)


# --------------------------------------------------------------------------- #
# core                                                                        #
# --------------------------------------------------------------------------- #
def _build(events, policy, manifest) -> Dict:
    # 0. minimal structural guard so orders can be inspected BEFORE any ledger classification
    if not isinstance(events, (list, tuple)) or not events:
        raise CostAnalysisError("events must be a non-empty list of verified journal events")
    for e in events:
        if not isinstance(e, dict) or not isinstance(e.get("payload"), dict):
            raise CostAnalysisError("each event must be a dict carrying a payload dict")
        if not (isinstance(e.get("seq"), int) and not isinstance(e.get("seq"), bool)):
            raise CostAnalysisError("each event must carry an int seq")
    try:
        cap, qs, _ss, _mh, _tf = _segp._extract_manifest(manifest)
    except _segp.SegmentPipelineError as exc:
        raise CostAnalysisError(f"manifest invalid: {exc}")
    prof = manifest.get("execution_profile")
    if not isinstance(prof, dict):
        raise CostAnalysisError("manifest.execution_profile must be a dict")
    ep_hash = execution_profile_hash(prof)
    last = events[-1]
    if not (isinstance(last.get("event_hash"), str) and last["event_hash"]):
        raise CostAnalysisError("final event_hash must be a non-empty string")
    final_hash = last["event_hash"]

    # 1. collect order events + reference-bar index (unambiguous open_time -> single bar event)
    order_events = [e for e in events if e.get("event_type") == "order"]
    bar_index: Dict[int, Dict] = {}
    for e in events:
        if e.get("event_type") == "bar_ingested":
            ot = e["payload"].get("open_time")
            rec = bar_index.get(ot)
            if rec is None:
                bar_index[ot] = {"event_id": e["event_id"], "seq": int(e["seq"]),
                                 "open": e["payload"].get("open"), "count": 1}
            else:
                rec["count"] += 1

    # 2. STRICTLY validate every order payload BEFORE any ledger classification (blocker 1)
    classified = [(e, _validate_order_payload_strict(e)) for e in order_events]

    # 3. build CostEvidence for valid non-zero fills (still before the ledger)
    evidence: Dict[str, Dict] = {}
    for e, (_uu, _st, is_nonzero) in classified:
        if is_nonzero:
            evidence[e["event_id"]] = _validate_and_make_evidence(e, bar_index, prof, final_hash)

    # 4. ONLY NOW invoke the frozen ledger + exactly-once partition proof
    try:
        ledger = build_cycle_ledger_from_orders(order_events, initial_capital=cap, qty_step=qs)
    except LedgerError as exc:
        raise CostAnalysisError(f"ledger invalid: {exc}")
    ok, detail = validate_fill_partition(ledger)
    if not ok:
        raise CostAnalysisError(f"fill partition violation: {detail}")
    cycles = ledger["cycles"]
    open_cycle = ledger["open_cycle"]
    dust = ledger["dust_carry"]

    # 5. reuse frozen WP2B-1 for journal/policy/manifest validation + segments + E_start + attribution
    try:
        seg_report = _segp._build_segment_report_from_events(events, policy, manifest)
    except _segp.SegmentPipelineError as exc:
        raise CostAnalysisError(f"segment/journal validation failed: {exc}")
    policy_hash = seg_report["policy_hash"]
    final_hash = seg_report["source_journal_final_hash"]   # authoritative (WP2B-1 verified chain)
    seg_by_id = {s["segment_id"]: s for s in seg_report["segments"]}
    for s in seg_report["segments"]:
        if s.get("status") != "OK":
            raise CostAnalysisError(f"segment {s['segment_id']!r} is not OK ({s.get('reason')})")

    bar_index_seq = {rec["event_id"]: rec["seq"] for rec in bar_index.values()}
    order_by_id = {e["event_id"]: e for e in order_events}
    seq_by_id = {e["event_id"]: e["seq"] for e in events}
    ot_by_id = {e["event_id"]: e["payload"]["exec_open_time"] for e in order_events}

    # 6. prove segment fill coverage: each non-zero fill in exactly one window
    windows = sorted(({"segment_id": s["segment_id"], "start_ms": int(s["start_ms"]),
                       "end_ms": int(s["end_ms"])} for s in policy["segments"]),
                     key=lambda w: w["start_ms"])
    for i in range(1, len(windows)):
        if windows[i]["start_ms"] < windows[i - 1]["end_ms"]:
            raise CostAnalysisError("segment windows overlap")
    for ce in evidence.values():
        hits = [w for w in windows if w["start_ms"] <= ce["exec_open_time"] < w["end_ms"]]
        if len(hits) != 1:
            raise CostAnalysisError(
                f"fill {ce['order_event_id']} at {ce['exec_open_time']} maps to {len(hits)} segments")
        ce["segment_id"] = hits[0]["segment_id"]

    def _in(ot, w):
        return w["start_ms"] <= ot < w["end_ms"]

    open_owned = sorted(_cycle_owned_ids(open_cycle), key=lambda i: seq_by_id.get(i, 1 << 62)) \
        if open_cycle is not None else []
    dust_ids = sorted(dust.get("fill_ids", []), key=lambda i: seq_by_id.get(i, 1 << 62))

    # 6. per-segment results
    segments_out: List[Dict] = []
    all_reconciled_ok = True
    session_identity_resid = 0.0
    for w in windows:
        sid = w["segment_id"]
        wr = seg_by_id[sid]
        E_start = None
        ci = wr.get("carry_in_evidence")
        if isinstance(ci, dict):
            E_start = ci.get("E_start")
        eqnr = wr.get("metrics", {}).get("net_return", _cell("NOT_AVAILABLE", None))

        seg_fill_ids = sorted((i for i, ce in evidence.items() if ce["segment_id"] == sid),
                              key=lambda i: seq_by_id.get(i, 1 << 62))
        segment_fill_costs = _sum_costs(seg_fill_ids, evidence)
        segment_fill_costs["cost_event_ids"] = list(seg_fill_ids)

        # cycles attributed to this segment (frozen WP2B-1 exit rule)
        cyc_idxs = sorted(wr.get("cycle_indices", []))
        cycle_results: List[Dict] = []
        for idx in cyc_idxs:
            cycle_results.append(_cycle_result(idx, cycles[idx], w, evidence, ot_by_id,
                                               seq_by_id))
        # derive aggregates from cycle_results
        realized_net = sum(cycles[idx]["realized_net"] for idx in cyc_idxs)
        realized_net_pnl = _cell("OK", float(realized_net))
        realized_net_return = _ret(realized_net_pnl, E_start)

        gross_ok = all(c["reconciliation_status"] == "OK" for c in cycle_results)
        gross_sum = sum(c["friction_free_gross_pnl"]["value"] for c in cycle_results
                        if c["reconciliation_status"] == "OK")
        if not cycle_results:
            ccg = _cell("OK", 0.0)
        elif gross_ok:
            ccg = _cell("OK", float(gross_sum))
        else:
            ccg = _cell("NOT_AVAILABLE", None)
        ccg_return = _ret(ccg, E_start)
        incl_idx = sorted(c["cycle_index"] for c in cycle_results
                          if c["reconciliation_status"] == "OK")
        excl_idx = sorted(c["cycle_index"] for c in cycle_results
                          if c["reconciliation_status"] != "OK")
        reconciled_subtotal = {"status": "OK", "value": float(gross_sum),
                               "is_complete_gross": False,
                               "included_cycle_indices": incl_idx,
                               "excluded_cycle_indices": excl_idx,
                               "total_completed": len(cycle_results),
                               "reconciled": len(incl_idx)}

        # exact allocated costs: only when EVERY completed cycle is quantity_exact + reconciled
        exact_ok = bool(cycle_results) and all(c["quantity_exact"] and c["reconciliation_status"] == "OK"
                                               for c in cycle_results)
        exact_ids: List[str] = []
        ref_ids: List[str] = []
        for c in cycle_results:
            if c["quantity_exact"] and c["reconciliation_status"] == "OK":
                exact_ids.extend(c["evidence_fill_ids"])
            else:
                ref_ids.extend(c["evidence_fill_ids"])
        if not cycle_results:
            exact_costs = _alloc("OK", 0.0, 0.0, 0.0, cycle_indices=[])
        elif exact_ok:
            b = _sum_costs(exact_ids, evidence)
            exact_costs = _alloc("OK", b["fee_cost"], b["spread_cost"], b["slippage_cost"],
                                 cycle_indices=list(cyc_idxs))
        else:
            exact_costs = _alloc("NOT_AVAILABLE", None, None, None, cycle_indices=[])
        ref_costs = dict(_sum_costs(sorted(set(ref_ids), key=lambda i: seq_by_id.get(i, 1 << 62)),
                                    evidence),
                         status="OK", referenced_fill_ids=sorted(set(ref_ids),
                                                                 key=lambda i: seq_by_id.get(i, 1 << 62)),
                         is_economic_allocation=False)

        if not cycle_results:
            drag = _cell("OK", 0.0)
        elif exact_ok and E_start not in (None, 0):
            drag = _cell("OK", float(exact_costs["total"]) / float(E_start))
        else:
            drag = _cell("NOT_AVAILABLE", None)

        # nonrealized / unallocated (this segment's window)
        oc_ids = [i for i in open_owned if _in(ot_by_id.get(i, -1), w) and i in evidence]
        du_ids = [i for i in dust_ids if _in(ot_by_id.get(i, -1), w) and i in evidence]
        nonrealized = {
            "open_cycle": dict(_sum_costs(oc_ids, evidence), fill_ids=list(oc_ids)),
            "dust_unallocated": dict(_sum_costs(du_ids, evidence), fill_ids=list(du_ids)),
        }

        open_gross = _cell("NOT_AVAILABLE", None) if open_cycle is not None else _cell("OK", 0.0)

        # coverage derived from cycle_results
        unavailable = [{"cycle_index": c["cycle_index"], "reason_code": c["reason_code"],
                        "detail": c["detail"]} for c in cycle_results
                       if c["reconciliation_status"] != "OK"]
        unavailable.sort(key=lambda d: (d["cycle_index"], d["reason_code"] or ""))
        coverage = {"total_completed": len(cycle_results),
                    "reconciled": sum(1 for c in cycle_results if c["reconciliation_status"] == "OK"),
                    "unavailable": len(unavailable), "unavailable_cycles": unavailable}

        for c in cycle_results:
            if c["reconciliation_status"] != "OK":
                all_reconciled_ok = False
            else:
                session_identity_resid += c["reconciliation_difference"]["value"]

        econ = {
            "segment_id": sid,
            "equity_net_return": {"status": eqnr.get("status", "NOT_AVAILABLE"),
                                  "value": eqnr.get("value")},
            "realized_net_pnl": realized_net_pnl,
            "realized_net_return": realized_net_return,
            "completed_cycle_friction_free_gross_pnl": ccg,
            "completed_cycle_friction_free_gross_return": ccg_return,
            "open_position_friction_free_gross_pnl": open_gross,
            "realized_friction_drag_return": drag,
            "reconciled_subtotal": reconciled_subtotal,
            "segment_fill_costs": segment_fill_costs,
            "exact_cycle_allocated_costs": exact_costs,
            "cycle_referenced_fill_costs": ref_costs,
            "nonrealized_or_unallocated_costs": nonrealized,
            "cycle_results": cycle_results,
            "cycle_coverage": coverage,
            "cost_event_ids": list(seg_fill_ids),
            "evidence_refs": {"fee": list(seg_fill_ids), "spread": list(seg_fill_ids),
                              "slippage": list(seg_fill_ids),
                              "gross": list(cyc_idxs), "net": list(cyc_idxs),
                              "reference_price": sorted(
                                  {evidence[i]["reference_bar_event_id"] for i in seg_fill_ids},
                                  key=lambda b: (bar_index_seq.get(b, 1 << 62), b)),
                              "reference_price_map": {i: evidence[i]["reference_bar_event_id"]
                                                      for i in seg_fill_ids}},
        }
        econ["result_hash"] = _sha(econ)
        econ["policy_hash"] = policy_hash
        econ["source_journal_final_hash"] = final_hash
        econ["execution_profile_hash"] = ep_hash
        segments_out.append(econ)

    # 7. session roll-up
    total_session = _sum_costs(list(evidence.keys()), evidence)
    unalloc_ids = sorted(set(open_owned) | set(dust_ids), key=lambda i: seq_by_id.get(i, 1 << 62))
    dust_clean = (len(dust.get("fill_ids", [])) == 0
                  and abs(float(dust.get("net", 0.0))) <= _TOL_CUR
                  and abs(float(dust.get("gross", 0.0))) <= _TOL_CUR
                  and abs(float(dust.get("fees", 0.0))) <= _TOL_CUR)
    complete = (open_cycle is None and dust_clean and all_reconciled_ok)
    # the completed-cycle session identity is only meaningful when EVERY completed cycle reconciled;
    # never present an unqualified {OK, 0.0} while any completed cycle is unavailable
    if all_reconciled_ok:
        session_identity = _cell("OK", float(session_identity_resid))
    else:
        session_identity = _cell("NOT_AVAILABLE", None)
    session = {
        "session_completeness_status": "COMPLETE" if complete else "PARTIAL",
        "fill_coverage_proven": True,
        "total_session_fill_cost": dict(total_session, status="OK"),
        "completed_cycle_session_identity_difference": session_identity,
        "unallocated_cost_event_ids": list(unalloc_ids),
        "source_journal_final_hash": final_hash,
        "manifest_hash": _mh,
        "execution_profile_hash": ep_hash,
    }
    session["session_result_hash"] = _sha(session)

    report = {"policy_hash": policy_hash, "source_journal_final_hash": final_hash,
              "execution_profile_hash": ep_hash, "policy_version": policy["policy_version"],
              "segments": segments_out, "session": session}
    report["result_hash"] = _sha({"segments": segments_out, "session": session})
    return report


def _ret(pnl_cell, E_start) -> Dict:
    if pnl_cell["status"] != "OK" or E_start in (None, 0) or not _finite(E_start):
        return _cell("NOT_AVAILABLE", None)
    return _cell("OK", float(pnl_cell["value"]) / float(E_start))


def _cycle_result(idx, cycle, window, evidence, ot_by_id, seq_by_id) -> Dict:
    owned = sorted(_cycle_owned_ids(cycle), key=lambda i: seq_by_id.get(i, 1 << 62))
    start, end = window["start_ms"], window["end_ms"]
    within = all(start <= ot_by_id.get(i, -1) < end for i in owned)
    scope = "WITHIN_SEGMENT" if within else "CROSS_BOUNDARY"
    quantity_exact = (cycle.get("residual_carried_in") is None
                      and cycle.get("residual_carried_out") is None)

    base = {"cycle_index": int(idx), "attribution_scope": scope, "quantity_exact": quantity_exact,
            "evidence_fill_ids": list(owned)}

    if not quantity_exact:
        reason = "RESIDUAL_CARRY_IN" if cycle.get("residual_carried_in") is not None \
            else "RESIDUAL_CARRY_OUT"
        return dict(base, reconciliation_status="NOT_AVAILABLE",
                    reconciliation_difference=_cell("NOT_AVAILABLE", None),
                    friction_free_gross_pnl=_cell("NOT_AVAILABLE", None),
                    exact_allocated_costs=_alloc("NOT_AVAILABLE", None, None, None),
                    reason_code=reason,
                    detail=f"cycle {idx} not quantity-exact ({reason})")

    # every owned fill must have cost evidence (owned fills are non-zero by construction)
    missing = [i for i in owned if i not in evidence]
    if missing:
        return dict(base, reconciliation_status="NOT_AVAILABLE",
                    reconciliation_difference=_cell("NOT_AVAILABLE", None),
                    friction_free_gross_pnl=_cell("NOT_AVAILABLE", None),
                    exact_allocated_costs=_alloc("NOT_AVAILABLE", None, None, None),
                    reason_code="COST_FIELD_INVALID",
                    detail=f"owned fills without cost evidence: {missing}")

    owned_fee = sum(evidence[i]["fee"] for i in owned)
    owned_spread = sum(evidence[i]["spread_cost"] for i in owned)
    owned_slip = sum(evidence[i]["slippage_cost"] for i in owned)
    realized_net = float(cycle["realized_net"])
    realized_gross = float(cycle["realized_gross"])

    # fee reconciliation (independent of spread/slippage)
    if abs(realized_net - (realized_gross - owned_fee)) > _TOL_CUR:
        return dict(base, reconciliation_status="NOT_AVAILABLE",
                    reconciliation_difference=_cell("NOT_AVAILABLE", None),
                    friction_free_gross_pnl=_cell("NOT_AVAILABLE", None),
                    exact_allocated_costs=_alloc("NOT_AVAILABLE", None, None, None),
                    reason_code="FEE_RECON_FAIL",
                    detail="realized_net != realized_gross - owned_fee")

    # independent friction-free gross from reference prices + quantities (no fill_price, no cost field)
    g_indep = -sum(evidence[i]["signed_filled_units"] * evidence[i]["reference_price"] for i in owned)
    g_derived = realized_gross + owned_spread + owned_slip
    diff = g_indep - g_derived
    if abs(diff) > _TOL_CUR:
        return dict(base, reconciliation_status="NOT_AVAILABLE",
                    reconciliation_difference=_cell("OK", float(diff)),
                    friction_free_gross_pnl=_cell("NOT_AVAILABLE", None),
                    exact_allocated_costs=_alloc("NOT_AVAILABLE", None, None, None),
                    reason_code="GROSS_INDEP_MISMATCH",
                    detail="independent reference-price gross disagrees with ledger-derived gross")

    return dict(base, reconciliation_status="OK",
                reconciliation_difference=_cell("OK", float(diff)),
                friction_free_gross_pnl=_cell("OK", float(g_indep)),
                exact_allocated_costs=_alloc("OK", owned_fee, owned_spread, owned_slip),
                reason_code=None,
                detail="")
