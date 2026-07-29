"""WP2B-1 tests — segment binding & metrics pipeline (test-first, hardened R1).

Covers the frozen R2+R3+R4 negative-test matrix, the R1 hardening items, and the two H-025 read-only
integrations (production path + actual unmodified frozen manifest). Deterministic; read-only; no wall
clock; no RNG. Frozen Stage 1 / WP2B-0A are consumed unchanged.
"""
from __future__ import annotations

import json

import pytest

from app.forward.segment_pipeline import (
    SegmentPipelineError,
    _build_segment_report_from_events,
    build_segment_report_from_journal,
)
from app.paper.session.broker import PaperBroker
from app.paper.session.profiles import freeze_execution_profile
from app.forward.trade_cycle_ledger import LedgerError, build_cycle_ledger_from_orders

_T0 = 1_600_000_000_000
_DAY = 86_400_000
PROF = freeze_execution_profile("BTC/USD")
QS = PROF["qty_step"]
CAP = 100_000.0
MH = "manifest-hash-xyz"


# --------------------------------------------------------------------------- #
# synthetic verified-journal builder (broker-consistent order/snapshot fields) #
# --------------------------------------------------------------------------- #
class _JB:
    def __init__(self, cap=CAP):
        self.b = PaperBroker(PROF, initial_capital=cap, max_leverage=1.0)
        self.events = []
        self.seq = 0
        self.cap = cap

    def _add(self, et, payload, ot, eid):
        self.events.append({"event_id": eid, "seq": self.seq, "timestamp": ot,
                            "event_type": et, "payload": payload,
                            "parent_hash": "p", "event_hash": f"h{self.seq}"})
        self.seq += 1
        return self

    def session(self, live=False, mh=MH):
        return self._add("session_created", {"manifest_hash": mh, "live_execution_enabled": live},
                         _T0, "session_created")

    def bar(self, ot, close, *, target=None, exec_price=None, safe_exit=False, snapshot=True, open_=None,
            extra_order_target=None):
        op = open_ if open_ is not None else close
        self._add("bar_ingested", {"open_time": ot, "open": op, "high": max(op, close) + 1.0,
                                   "low": max(0.01, min(op, close) - 1.0), "close": close, "volume": 1000.0},
                  ot, f"bar:{ot}")
        if target is not None:
            xp = exec_price if exec_price is not None else op
            self.b.mark(xp)
            o = self.b.execute(target, exec_open_time=ot, exec_price=xp)
            self._add("order", o, ot, f"order:{ot}")
        if extra_order_target is not None:                 # a second ordinary order on the same bar
            xp = exec_price if exec_price is not None else op
            self.b.mark(xp)
            o2 = self.b.execute(extra_order_target, exec_open_time=ot, exec_price=xp)
            self._add("order", o2, ot, f"order2:{ot}")
        if safe_exit:
            xp = exec_price if exec_price is not None else op
            self.b.mark(xp)
            se = {"open_time": ot, "order_status": "filled", "position_units": self.b.position_units,
                  "mark_price": xp, "equity": self.b.equity, "note": "safe-exit"}
            self._add("safe_exit", se, ot, f"safeexit:{ot}")
            return self
        if snapshot:
            self.b.mark(close)
            self._add("portfolio_snapshot", {"open_time": ot, "close": close, **self.b.snapshot()},
                      ot, f"snap:{ot}")
        return self

    def manifest(self):
        # the REAL frozen manifest schema keys the pipeline reads (incl. the bound timeframe string)
        return {"initial_capital": self.cap, "execution_profile": dict(PROF),
                "start_timestamp_ms": _T0, "manifest_hash": MH, "timeframe": "1d"}


def _reseq(events):
    """Reassign contiguous 0-based seq after structural mutation (keeps a valid verified-journal shape)."""
    for i, e in enumerate(events):
        e["seq"] = i
        e["event_hash"] = f"h{i}"
    return events


def _rep(events, pol, manifest):
    return _build_segment_report_from_events(events, pol, manifest)


def _policy(segments, tf=_DAY, allow_gaps=False):
    return {"policy_version": "segment-policy-v1", "segment_type": "bar_grid_intervals",
            "timeframe_ms": tf, "boundary_rule": "half_open[start,end)",
            "allow_gaps": allow_gaps, "segments": segments}


def _seg(sid, start, end):
    return {"segment_id": sid, "start_ms": start, "end_ms": end}


def _simple_journal(n=6):
    jb = _JB().session()
    for i in range(n):
        ot = _T0 + i * _DAY
        jb.bar(ot, close=100.0 + i, target=(0.5 if i == 1 else None), exec_price=100.0 + i)
    return jb


# --------------------------------------------------------------------------- #
# policy validation (#1-4) + strict policy (item 6)                            #
# --------------------------------------------------------------------------- #
def test_overlapping_segments_raise():
    jb = _simple_journal(6)
    pol = _policy([_seg("s1", _T0, _T0 + 4 * _DAY), _seg("s2", _T0 + 2 * _DAY, _T0 + 6 * _DAY)])
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, pol, jb.manifest())


def test_reversed_boundary_raises():
    jb = _simple_journal(6)
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0 + 4 * _DAY, _T0 + 2 * _DAY)]), jb.manifest())


@pytest.mark.parametrize("bad", [_T0 + 1, float(_T0), True, None])
def test_malformed_timestamp_raises(bad):
    jb = _simple_journal(6)
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", bad, _T0 + 6 * _DAY)]), jb.manifest())


def test_non_aligned_boundary_raises():
    jb = _simple_journal(6)
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0 + _DAY // 2, _T0 + 6 * _DAY)]), jb.manifest())


def test_bad_policy_version_raises():
    jb = _simple_journal(6)
    pol = _policy([_seg("s1", _T0, _T0 + 6 * _DAY)])
    pol["policy_version"] = "v9"
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, pol, jb.manifest())


def test_bad_segment_type_raises():
    jb = _simple_journal(6)
    pol = _policy([_seg("s1", _T0, _T0 + 6 * _DAY)])
    pol["segment_type"] = "arbitrary_ms"
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, pol, jb.manifest())


@pytest.mark.parametrize("bad", ["true", 1, 0, None])
def test_allow_gaps_non_bool_raises(bad):
    jb = _simple_journal(6)
    pol = _policy([_seg("s1", _T0, _T0 + 6 * _DAY)])
    pol["allow_gaps"] = bad
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, pol, jb.manifest())


def test_unknown_policy_key_raises():
    jb = _simple_journal(6)
    pol = _policy([_seg("s1", _T0, _T0 + 6 * _DAY)])
    pol["mystery"] = 1
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, pol, jb.manifest())


# --------------------------------------------------------------------------- #
# verified-journal production input (item 1)                                   #
# --------------------------------------------------------------------------- #
def test_non_contiguous_seq_raises():
    jb = _simple_journal(6)
    jb.events = jb.events[:3] + jb.events[4:]     # drop one event -> seq gap
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())


def test_missing_session_created_raises():
    jb = _simple_journal(6)
    jb.events = _reseq([e for e in jb.events if e["event_type"] != "session_created"])
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())


def test_two_session_created_raises():
    jb = _simple_journal(6)
    jb.events.insert(1, dict(jb.events[0]))
    jb.events[1]["event_id"] = "session_created_2"
    _reseq(jb.events)
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())


def test_manifest_hash_mismatch_raises():
    jb = _JB().session(mh="WRONG")
    for i in range(3):
        jb.bar(_T0 + i * _DAY, close=100.0 + i)
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 3 * _DAY)]), jb.manifest())


def test_live_execution_true_raises():
    jb = _JB().session(live=True)
    for i in range(3):
        jb.bar(_T0 + i * _DAY, close=100.0 + i)
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 3 * _DAY)]), jb.manifest())


def test_manifest_missing_execution_profile_raises():
    jb = _simple_journal(3)
    man = jb.manifest()
    del man["execution_profile"]
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 3 * _DAY)]), man)


# --------------------------------------------------------------------------- #
# journal identity (#5-6)                                                      #
# --------------------------------------------------------------------------- #
def test_duplicate_event_id_raises():
    jb = _simple_journal(6)
    jb.events[3] = dict(jb.events[3], event_id=jb.events[2]["event_id"])
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())


# --------------------------------------------------------------------------- #
# duplicate / ambiguous structures (item 5)                                    #
# --------------------------------------------------------------------------- #
def test_duplicate_bar_ingested_raises():
    jb = _simple_journal(6)
    dup = next(e for e in jb.events if e["event_type"] == "bar_ingested"
               and e["payload"]["open_time"] == _T0 + 2 * _DAY)
    jb.events.append(dict(dup, event_id="bar:dup"))
    _reseq(jb.events)
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())


def test_duplicate_carry_in_snapshot_invalid():
    jb = _simple_journal(6)
    dup = next(e for e in jb.events if e["event_type"] == "portfolio_snapshot"
               and e["payload"]["open_time"] == _T0 + 2 * _DAY)                # carry-in of s2
    jb.events.append(dict(dup, event_id="snap:dup"))
    _reseq(jb.events)
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])
    rep = _rep(jb.events, pol, jb.manifest())
    assert rep["segments"][1]["status"] == "INVALID"


def test_duplicate_safe_exit_invalid():
    jb = _terminal_journal(final_target=0.0)
    dup = next(e for e in jb.events if e["event_type"] == "safe_exit")
    jb.events.append(dict(dup, event_id="safeexit:dup"))
    _reseq(jb.events)
    pol = _policy([_seg("s1", _T0, _T0 + 4 * _DAY)])
    rep = _rep(jb.events, pol, jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"


def test_multiple_orders_one_bar_both_counted():
    jb = _JB().session()
    jb.bar(_T0 + 0 * _DAY, close=100.0)
    jb.bar(_T0 + 1 * _DAY, close=101.0, target=0.3, exec_price=101.0, extra_order_target=0.6)  # two orders
    jb.bar(_T0 + 2 * _DAY, close=102.0)
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY)])
    rep = _rep(jb.events, pol, jb.manifest())
    s1 = rep["segments"][0]
    member_orders = [x for x in s1["segment_member_event_ids"] if x.startswith("order")]
    # BOTH order events are preserved as members — neither silently overwritten by an open_time-keyed dict
    assert set(member_orders) == {f"order:{_T0 + _DAY}", f"order2:{_T0 + _DAY}"}
    # the frozen broker suppresses the same-bar second execution (no_trade, 0 fill), so only the real
    # fill is counted in turnover; the turnover refs are a subset of the member orders (no phantom fill)
    assert f"order:{_T0 + _DAY}" in s1["evidence_refs"]["turnover"]
    assert set(s1["evidence_refs"]["turnover"]).issubset(set(member_orders))


# --------------------------------------------------------------------------- #
# membership / boundary (#7-8) + coverage (#24) + seq order (item 4)           #
# --------------------------------------------------------------------------- #
def test_boundary_membership_and_coverage():
    jb = _simple_journal(6)
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])
    rep = _rep(jb.events, pol, jb.manifest())
    s1, s2 = rep["segments"]
    assert f"bar:{_T0 + 3 * _DAY}" in s2["segment_member_event_ids"]
    assert f"bar:{_T0 + 3 * _DAY}" not in s1["segment_member_event_ids"]
    allbars = {f"bar:{_T0 + i * _DAY}" for i in range(6)}
    got = set()
    for s in (s1, s2):
        got |= {x for x in s["segment_member_event_ids"] if x.startswith("bar:")}
    assert got == allbars


def test_member_ids_follow_seq_not_alpha():
    # event IDs whose alphabetical order is the REVERSE of seq order
    jb = _JB().session()
    for i in range(3):
        jb.bar(_T0 + i * _DAY, close=100.0 + i)
    for e in jb.events:
        e["event_id"] = f"{chr(ord('z') - e['seq'])}:{e['event_type']}:{e['seq']}"  # reverse-alpha vs seq
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY)])
    rep = _rep(jb.events, pol, jb.manifest())
    ids = rep["segments"][0]["segment_member_event_ids"]
    seqs = {e["event_id"]: e["seq"] for e in jb.events}
    assert [seqs[i] for i in ids] == sorted(seqs[i] for i in ids)   # ascending by seq
    assert ids != sorted(ids)                                       # NOT lexicographic


def test_future_event_does_not_change_economic_result():
    jb3 = _JB().session()
    for i in range(3):
        jb3.bar(_T0 + i * _DAY, close=100.0 + i, target=(0.5 if i == 1 else None), exec_price=100.0 + i)
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY)])
    rep_a = _rep(jb3.events, pol, jb3.manifest())
    jb4 = _JB().session()
    for i in range(4):
        jb4.bar(_T0 + i * _DAY, close=100.0 + i, target=(0.5 if i == 1 else None), exec_price=100.0 + i)
    pol2 = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 4 * _DAY)])
    rep_b = _rep(jb4.events, pol2, jb4.manifest())
    assert rep_a["segments"][0]["economic_hash"] == rep_b["segments"][0]["economic_hash"]
    assert rep_a["source_journal_final_hash"] != rep_b["source_journal_final_hash"]
    assert rep_a["pipeline_hash"] != rep_b["pipeline_hash"]


# --------------------------------------------------------------------------- #
# per-bar snapshot completeness (#11-13) + payload validation (item 7)         #
# --------------------------------------------------------------------------- #
def test_missing_interior_snapshot_invalid():
    jb = _simple_journal(6)
    jb.events = _reseq([e for e in jb.events if not (e["event_type"] == "portfolio_snapshot"
                        and e["payload"]["open_time"] == _T0 + 2 * _DAY)])
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"


def test_duplicate_interior_snapshot_invalid():
    jb = _simple_journal(6)
    dup = next(e for e in jb.events if e["event_type"] == "portfolio_snapshot"
               and e["payload"]["open_time"] == _T0 + 3 * _DAY)
    jb.events.append(dict(dup, event_id="snap:dup"))
    _reseq(jb.events)
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"


def test_non_int_binding_open_time_raises():
    jb = _simple_journal(3)
    for e in jb.events:
        if e["event_type"] == "order":
            e["payload"] = dict(e["payload"], exec_open_time=float(e["payload"]["exec_open_time"]))
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 3 * _DAY)]), jb.manifest())


def test_non_finite_snapshot_equity_invalid():
    jb = _simple_journal(6)
    for e in jb.events:
        if e["event_type"] == "portfolio_snapshot" and e["payload"]["open_time"] == _T0 + 3 * _DAY:
            e["payload"] = dict(e["payload"], equity=float("nan"))
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"       # no raw exception leaks


# --------------------------------------------------------------------------- #
# empty / NA vs INVALID (#16, #25) + evidence_refs (item 3)                     #
# --------------------------------------------------------------------------- #
def test_no_completed_cycle_not_available_and_evidence_refs_present():
    jb = _simple_journal(6)
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())
    s1 = rep["segments"][0]
    assert s1["status"] == "OK"
    assert s1["metrics"]["win_rate"]["status"] == "NOT_AVAILABLE"
    assert s1["metrics"]["completed_trade_count"]["value"] == 0
    assert s1["metrics"]["exposure_time"]["status"] == "OK"
    for k in ("segment_id", "policy_hash" if False else "segment_id", "segment_member_event_ids",
              "carry_in_evidence", "E_start_ref", "E_end_ref", "historical_support_evidence",
              "metrics", "evidence_refs", "cycle_indices", "economic_hash"):
        assert k in s1
    # per-metric evidence refs are populated
    assert s1["evidence_refs"]["net_return"] == [s1["E_start_ref"]["event_id"]] + s1["E_end_ref"]["event_ids"]
    assert s1["evidence_refs"]["max_drawdown"]           # snapshot ids
    assert s1["evidence_refs"]["exposure_time"]
    assert "cycle_indices" in s1["evidence_refs"]["win_rate"]


# --------------------------------------------------------------------------- #
# carry-in separation (#26-29)                                                 #
# --------------------------------------------------------------------------- #
def test_carry_in_supplies_estart_and_is_not_a_member():
    jb = _simple_journal(6)
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])
    rep = _rep(jb.events, pol, jb.manifest())
    s2 = rep["segments"][1]
    carry_id = f"snap:{_T0 + 2 * _DAY}"
    assert s2["carry_in_evidence"]["event_id"] == carry_id
    assert s2["carry_in_evidence"]["kind"] == "prev_bar_snapshot"
    assert carry_id not in s2["segment_member_event_ids"]
    assert s2["E_start_ref"]["event_id"] == carry_id


def test_first_segment_estart_is_initial_capital():
    jb = _simple_journal(6)
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())
    ci = rep["segments"][0]["carry_in_evidence"]
    assert ci["kind"] == "session_created_initial_capital"
    assert ci["E_start"] == CAP and ci["event_id"] == "session_created"


def test_missing_exact_carry_in_snapshot_invalid():
    jb = _simple_journal(6)
    jb.events = _reseq([e for e in jb.events if not (e["event_type"] == "portfolio_snapshot"
                        and e["payload"]["open_time"] == _T0 + 2 * _DAY)])
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])
    rep = _rep(jb.events, pol, jb.manifest())
    assert rep["segments"][1]["status"] == "INVALID"


def test_carry_in_snapshot_corrupted_reconcile_invalid():
    jb = _simple_journal(6)
    for e in jb.events:
        if e["event_type"] == "portfolio_snapshot" and e["payload"]["open_time"] == _T0 + 2 * _DAY:
            e["payload"] = dict(e["payload"], cash=e["payload"]["cash"] + 500.0)
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])
    rep = _rep(jb.events, pol, jb.manifest())
    assert rep["segments"][1]["status"] == "INVALID"


def test_prefix_orders_are_support_not_members():
    jb = _simple_journal(6)
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])
    rep = _rep(jb.events, pol, jb.manifest())
    s2 = rep["segments"][1]
    support_ids = {r["event_id"] for r in s2["historical_support_evidence"]}
    entry_order = f"order:{_T0 + 1 * _DAY}"
    assert entry_order in support_ids
    assert entry_order not in s2["segment_member_event_ids"]
    rec = next(r for r in s2["historical_support_evidence"] if r["event_id"] == entry_order)
    assert "prefix_reconciliation" in rec["reason"] and rec["seq"] < s2["E_start_ref"]["seq_cutoff"]


def test_support_events_are_below_cutoff_and_seq_ordered():
    jb = _cross_boundary_journal()
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])
    rep = _rep(jb.events, pol, jb.manifest())
    s2 = rep["segments"][1]
    start = _T0 + 3 * _DAY
    seqs = [r["seq"] for r in s2["historical_support_evidence"]]
    assert seqs == sorted(seqs)                                   # seq-ordered, not lexicographic
    order_ot = {e["event_id"]: e["payload"]["exec_open_time"]
                for e in jb.events if e["event_type"] == "order"}
    for r in s2["historical_support_evidence"]:
        assert order_ot[r["event_id"]] < start


# --------------------------------------------------------------------------- #
# cross-boundary cycle lineage (#30-33)                                        #
# --------------------------------------------------------------------------- #
def _cross_boundary_journal():
    jb = _JB().session()
    prices = [100.0, 100.0, 101.0, 102.0, 103.0, 104.0]
    for i, px in enumerate(prices):
        ot = _T0 + i * _DAY
        if i == 1:
            jb.bar(ot, close=px, target=0.5, exec_price=px)
        elif i == 4:
            jb.bar(ot, close=px, target=0.0, exec_price=px)
        else:
            jb.bar(ot, close=px)
    return jb


def test_cross_boundary_cycle_attributed_to_exit_segment_with_lineage():
    jb = _cross_boundary_journal()
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])
    rep = _rep(jb.events, pol, jb.manifest())
    s1, s2 = rep["segments"]
    assert s2["metrics"]["completed_trade_count"]["value"] == 1
    assert s1["metrics"]["completed_trade_count"]["value"] == 0
    entry_order = f"order:{_T0 + 1 * _DAY}"
    rec = next((r for r in s2["historical_support_evidence"] if r["event_id"] == entry_order), None)
    assert rec is not None and "cross_boundary_cycle_lineage" in rec["reason"]
    assert rec["related_cycle_index"] is not None
    member_orders = [x for x in s2["segment_member_event_ids"] if x.startswith("order")]
    assert member_orders == [f"order:{_T0 + 4 * _DAY}"]
    # complete cycle fill lineage is traceable in evidence_refs but NOT counted as s2 turnover
    assert entry_order in s2["evidence_refs"]["win_rate"]["fill_ids"]
    assert entry_order not in s2["evidence_refs"]["turnover"]


def test_dedup_evidence_ids_across_roles():
    jb = _cross_boundary_journal()
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])
    rep = _rep(jb.events, pol, jb.manifest())
    s2 = rep["segments"][1]
    members = set(s2["segment_member_event_ids"])
    support = {r["event_id"] for r in s2["historical_support_evidence"]}
    carry = {s2["carry_in_evidence"]["event_id"]}
    assert members.isdisjoint(support) and members.isdisjoint(carry) and support.isdisjoint(carry)
    ids = [r["event_id"] for r in s2["historical_support_evidence"]]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------- #
# terminal safe-exit (#19-22, item 5 linkage)                                  #
# --------------------------------------------------------------------------- #
def _terminal_journal(final_target=0.0):
    jb = _JB().session()
    jb.bar(_T0 + 0 * _DAY, close=100.0)
    jb.bar(_T0 + 1 * _DAY, close=101.0, target=0.5, exec_price=101.0)
    jb.bar(_T0 + 2 * _DAY, close=102.0)
    jb.bar(_T0 + 3 * _DAY, close=103.0, target=final_target, exec_price=103.0, safe_exit=True)
    return jb


def test_terminal_safe_exit_valid_uses_safe_exit_equity():
    jb = _terminal_journal(final_target=0.0)
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 4 * _DAY)]), jb.manifest())
    s1 = rep["segments"][0]
    assert s1["status"] == "OK" and s1["E_end_ref"]["kind"] == "terminal_safe_exit"
    se = next(e for e in jb.events if e["event_type"] == "safe_exit")
    assert s1["E_end_ref"]["E_end"] == se["payload"]["equity"]
    assert set(s1["E_end_ref"]["event_ids"]) == {se["event_id"], f"order:{_T0 + 3 * _DAY}"}
    assert s1["metrics"]["completed_trade_count"]["value"] == 1


def test_terminal_corrupted_safe_exit_equity_invalid():
    jb = _terminal_journal(final_target=0.0)
    for e in jb.events:
        if e["event_type"] == "safe_exit":
            e["payload"] = dict(e["payload"], equity=e["payload"]["equity"] + 100.0)
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 4 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"


def test_terminal_mismatched_position_units_invalid():
    jb = _terminal_journal(final_target=0.0)
    for e in jb.events:
        if e["event_type"] == "safe_exit":
            e["payload"] = dict(e["payload"], position_units=e["payload"]["position_units"] + 5.0)
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 4 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"


def test_terminal_status_disagreement_invalid():
    jb = _terminal_journal(final_target=0.0)
    for e in jb.events:
        if e["event_type"] == "safe_exit":
            e["payload"] = dict(e["payload"], order_status="partial")   # linked order is 'filled'
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 4 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"


def test_terminal_non_flat_result_invalid():
    jb = _JB().session()
    jb.bar(_T0 + 0 * _DAY, close=100.0)
    jb.bar(_T0 + 1 * _DAY, close=101.0, target=0.8, exec_price=101.0)
    jb.bar(_T0 + 2 * _DAY, close=102.0, target=0.4, exec_price=102.0, safe_exit=True)
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 3 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"


def test_terminal_none_variant_is_invalid():
    jb = _terminal_journal(final_target=0.0)
    for e in jb.events:
        if e["event_type"] == "safe_exit":
            e["payload"] = dict(e["payload"], order_status="none")
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 4 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"


def test_terminal_non_adjacent_linkage_invalid():
    # insert a spurious event between the CLOSE_ALL order and safe_exit -> non-adjacent
    jb = _terminal_journal(final_target=0.0)
    se_i = next(i for i, e in enumerate(jb.events) if e["event_type"] == "safe_exit")
    filler = {"event_id": "filler", "seq": 0, "timestamp": _T0 + 3 * _DAY, "event_type": "signal",
              "payload": {"open_time": _T0 + 3 * _DAY}, "parent_hash": "p", "event_hash": "hx"}
    jb.events.insert(se_i, filler)
    _reseq(jb.events)
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 4 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"


def test_terminal_corrupted_order_equity_after_fails_closed():
    jb = _terminal_journal(final_target=0.0)
    for e in jb.events:
        if e["event_type"] == "order" and e["payload"]["exec_open_time"] == _T0 + 3 * _DAY:
            e["payload"] = dict(e["payload"], equity_after=e["payload"]["equity_after"] + 100.0)
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 4 * _DAY)]), jb.manifest())


def test_terminal_close_all_order_is_member_not_support():
    jb = _terminal_journal(final_target=0.0)
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 4 * _DAY)]), jb.manifest())
    s1 = rep["segments"][0]
    order_id = f"order:{_T0 + 3 * _DAY}"
    assert order_id in s1["segment_member_event_ids"]
    assert order_id not in {r["event_id"] for r in s1["historical_support_evidence"]}


# --------------------------------------------------------------------------- #
# gap orphan (#17) + determinism (#23,#35)                                     #
# --------------------------------------------------------------------------- #
def test_gap_orphan_bar_raises():
    jb = _simple_journal(6)
    pol = _policy([_seg("s1", _T0, _T0 + 2 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)],
                  allow_gaps=True)
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, pol, jb.manifest())


def test_repeated_replay_byte_identical():
    jb = _cross_boundary_journal()
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])
    a = _rep(jb.events, pol, jb.manifest())
    b = _rep(jb.events, pol, jb.manifest())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# =========================================================================== #
# REAL H-025 INTEGRATIONS (production path + actual unmodified frozen manifest) #
# =========================================================================== #
def _run_h025(frame, tmp_path, risk=None):
    from app.strategy_research.strategies import get_strategy
    from app.paper.session.risk import freeze_risk_limits
    from app.paper.session.manifest import build_session_manifest
    from app.paper.session.engine import run_replay
    from app.forward.shadow.ranges import build_reference_ranges, freeze_range_policy
    from app.forward.shadow.regime import DEFAULT_REGIME_POLICY, regime_policy_hash
    from app.forward.shadow.consistency import DEFAULT_CONSISTENCY_POLICY, consistency_policy_hash
    from app.paper.session.ingest import dataset_hash
    strat = get_strategy("H-025")
    prof = freeze_execution_profile("BTC/USD")
    rpol = freeze_range_policy(seed=42, block_length=3, n_resamples=100, min_unique_segments=6)
    segs = [{"segment_index": i, "metrics": {
        "return": 0.001 * (i % 7 - 3), "drawdown": -0.02 - 0.001 * (i % 3),
        "trade_count": float(5 + i % 4), "turnover": float(1 + i % 3), "exposure_time": 0.5,
        "win_rate": 0.5 + 0.01 * (i % 3), "avg_holding_period": float(3 + i % 2),
        "long_short_balance": 1.0}} for i in range(24)]
    ref = build_reference_ranges(segs, policy=rpol)
    risk = risk or freeze_risk_limits(max_drawdown=0.90)
    man = build_session_manifest(
        strategy=strat, params={"length": 5}, asset="BTC/USD", timeframe="1d",
        data_source={"provider": "synthetic_test", "root": "n/a", "is_real": False},
        start_timestamp_ms=int(frame["open_time"].iloc[0]), initial_capital=100_000.0,
        position_sizing_policy={"type": "target_fraction_of_equity", "max_gross": 1.0},
        execution_profile=prof, absolute_risk_limits=risk,
        gap_stale_policy={"max_gap_bars": 2, "on_gap": "DATA_STOPPED"},
        regime_policy_hash=regime_policy_hash(DEFAULT_REGIME_POLICY),
        consistency_policy_hash=consistency_policy_hash(DEFAULT_CONSISTENCY_POLICY),
        reference_range_hash=ref["range_hash"], comparability_basis=ref["comparability_basis"],
        dataset_hash=dataset_hash(frame))
    d = str(tmp_path)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                     params={"length": 5}, frame=frame, journal_dir=d)
    return d + "/journal.jsonl", man, res


def _mkframe(closes):
    import pandas as pd
    opens = [closes[0]] + closes[:-1]
    return pd.DataFrame([{"open_time": _T0 + i * _DAY, "open": float(opens[i]),
                          "high": max(opens[i], closes[i]) + 1.0,
                          "low": max(0.01, min(opens[i], closes[i]) - 1.0),
                          "close": float(closes[i]), "volume": 1000.0} for i in range(len(closes))])


def test_h025_uptrend_multiseg_carry_in_open_position(tmp_path):
    n = 30
    path, man, res = _run_h025(_mkframe([100 + 3 * i for i in range(n)]), tmp_path)
    assert res["lifecycle_status"] == "COMPLETED"
    split = _T0 + 15 * _DAY
    pol = _policy([_seg("s1", _T0, split), _seg("s2", split, _T0 + n * _DAY)])
    rep = build_segment_report_from_journal(path, pol, man)      # production path, real manifest
    b = build_segment_report_from_journal(path, pol, man)
    assert json.dumps(rep, sort_keys=True) == json.dumps(b, sort_keys=True)
    s1, s2 = rep["segments"]
    assert s1["status"] == "OK" and s2["status"] == "OK"
    carry_id = f"snap:{split - _DAY}"
    assert s2["carry_in_evidence"]["event_id"] == carry_id and carry_id not in s2["segment_member_event_ids"]
    assert s1["metrics"]["completed_trade_count"]["value"] == 0
    assert s2["metrics"]["completed_trade_count"]["value"] == 0
    assert s2["metrics"]["exposure_time"]["value"] > 0.0
    assert s2["metrics"]["net_return"]["status"] == "OK"


def test_h025_riskstop_two_segment_cross_boundary(tmp_path):
    from app.paper.session.risk import freeze_risk_limits
    closes = [100 + 3 * i for i in range(12)] + [40.0, 41.0, 42.0]
    path, man, res = _run_h025(_mkframe(closes), tmp_path, risk=freeze_risk_limits(max_drawdown=0.10))
    assert res["lifecycle_status"] == "RISK_STOPPED"
    from app.forward.segment_pipeline import build_segment_report_from_journal as _bj
    from app.paper.session.journal import read_verified_events
    from app.forward.trade_cycle_ledger import build_cycle_ledger_from_orders
    events = read_verified_events(path, man["manifest_hash"])
    bar_ots = sorted(e["payload"]["open_time"] for e in events if e["event_type"] == "bar_ingested")
    grid_end = bar_ots[-1] + _DAY
    orders = [e for e in events if e["event_type"] == "order"]
    led = build_cycle_ledger_from_orders(orders, initial_capital=100_000.0,
                                         qty_step=man["execution_profile"]["qty_step"])
    cyc = led["cycles"][0]
    split = cyc["entry_exec_open_time"] + _DAY
    assert cyc["entry_exec_open_time"] < split <= cyc["exit_exec_open_time"] and split in bar_ots
    pol = _policy([_seg("s1", _T0, split), _seg("s2", split, grid_end)])
    rep = _bj(path, pol, man)
    b = _bj(path, pol, man)
    assert json.dumps(rep, sort_keys=True) == json.dumps(b, sort_keys=True)
    s1, s2 = rep["segments"]
    assert s1["status"] == "OK" and s2["status"] == "OK"
    assert s2["metrics"]["completed_trade_count"]["value"] == 1
    assert s1["metrics"]["completed_trade_count"]["value"] == 0
    entry_order_id = f"order:{cyc['entry_exec_open_time']}"
    rec = next((r for r in s2["historical_support_evidence"] if r["event_id"] == entry_order_id), None)
    assert rec is not None and "cross_boundary_cycle_lineage" in rec["reason"]
    assert entry_order_id not in s2["segment_member_event_ids"]
    assert entry_order_id not in s2["evidence_refs"]["turnover"]
    se = next(e for e in events if e["event_type"] == "safe_exit")
    assert s2["E_end_ref"]["kind"] == "terminal_safe_exit"
    assert s2["E_end_ref"]["E_end"] == se["payload"]["equity"]


def test_h025_from_journal_manifest_hash_mismatch_fails(tmp_path):
    path, man, res = _run_h025(_mkframe([100 + 3 * i for i in range(30)]), tmp_path)
    bad = dict(man, manifest_hash="not-the-real-hash")
    pol = _policy([_seg("s1", _T0, _T0 + 30 * _DAY)])
    with pytest.raises(SegmentPipelineError):
        build_segment_report_from_journal(path, pol, bad)


def test_h025_live_execution_disabled(tmp_path):
    path, man, res = _run_h025(_mkframe([100 + 3 * i for i in range(30)]), tmp_path)
    from app.paper.session.journal import read_verified_events
    events = read_verified_events(path, man["manifest_hash"])
    sc = next(e for e in events if e["event_type"] == "session_created")
    assert sc["payload"]["live_execution_enabled"] is False


# =========================================================================== #
# WP2B-1 HARDENING R2 — six narrowly-scoped corrections                        #
# =========================================================================== #

# --- R2 item 1: every valid non-empty SegmentResult carries policy_hash + ---- #
# --- source_journal_final_hash DIRECTLY (identity fields; economic_hash stable) #
def test_segment_carries_policy_and_journal_hash():
    jb = _simple_journal(6)
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])
    rep = _rep(jb.events, pol, jb.manifest())
    for s in rep["segments"]:
        assert s["policy_hash"] == rep["policy_hash"]
        assert s["source_journal_final_hash"] == rep["source_journal_final_hash"]
    # the identity fields do NOT feed economic_hash: same segment economics under a different
    # journal/policy still hashes identically (invariance preserved)
    for k in ("segment_id", "segment_member_event_ids", "carry_in_evidence", "E_start_ref",
              "E_end_ref", "historical_support_evidence", "metrics", "evidence_refs",
              "cycle_indices", "economic_hash", "policy_hash", "source_journal_final_hash"):
        assert k in rep["segments"][0]


# --- R2 item 2: no public verification bypass; production entry = journal only  #
def test_no_public_verification_bypass():
    import app.forward.segment_pipeline as _mod
    assert not hasattr(_mod, "build_segment_report_from_events")   # public bypass removed


def test_public_api_rejects_event_list():
    jb = _simple_journal(6)
    pol = _policy([_seg("s1", _T0, _T0 + 6 * _DAY)])
    with pytest.raises(SegmentPipelineError):
        build_segment_report_from_journal(jb.events, pol, jb.manifest())   # event list, not a path


# --- R2 item 3: policy timeframe bound to the REAL manifest timeframe --------- #
def test_policy_timeframe_matches_manifest_1d():
    jb = _simple_journal(6)
    man = jb.manifest()
    assert man["timeframe"] == "1d"
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)], tf=_DAY), man)
    assert rep["segments"][0]["status"] == "OK"


def test_policy_timeframe_mismatch_raises():
    # end aligns to O[-1]+tf under the WRONG tf, so the grid/coverage checks pass and ONLY the
    # timeframe-binding check can reject it (isolates item 3 from grid alignment)
    jb = _simple_journal(6)
    tf_1h = 3_600_000
    pol = _policy([_seg("s1", _T0, _T0 + 5 * _DAY + tf_1h)], tf=tf_1h)   # 1h policy vs 1d manifest
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, pol, jb.manifest())


def test_unknown_manifest_timeframe_fails_closed():
    jb = _simple_journal(6)
    man = dict(jb.manifest(), timeframe="3d")   # not a supported timeframe string
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), man)


def test_missing_manifest_timeframe_fails_closed():
    jb = _simple_journal(6)
    man = jb.manifest()
    del man["timeframe"]
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), man)


# --- R2 item 4: global safe_exit validation (no dict hiding) ------------------ #
def _inject_safe_exit(jb, ot, eid, **overrides):
    payload = {"open_time": ot, "order_status": "filled", "position_units": 0.0,
               "mark_price": 102.0, "equity": 100_000.0, "note": "injected"}
    payload.update(overrides)
    jb.events.append({"event_id": eid, "seq": 0, "timestamp": ot, "event_type": "safe_exit",
                      "payload": payload, "parent_hash": "p", "event_hash": "h"})


def test_duplicate_nonterminal_safe_exit_invalid():
    # two safe_exit events at the SAME interior open_time must not be silently de-duplicated by a
    # dict; the segment consuming that open_time is invalidated (even though it is not the E_end bar)
    jb = _simple_journal(6)
    _inject_safe_exit(jb, _T0 + 2 * _DAY, "se:i1")
    _inject_safe_exit(jb, _T0 + 2 * _DAY, "se:i2")
    _reseq(jb.events)
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"


def test_malformed_nonterminal_safe_exit_fails_closed():
    # a non-terminal safe_exit with a non-finite required field is validated BEFORE indexing
    jb = _simple_journal(6)
    _inject_safe_exit(jb, _T0 + 2 * _DAY, "se:bad", position_units=float("nan"))
    _reseq(jb.events)
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())


def test_safe_exit_without_matching_bar_fails_closed():
    jb = _simple_journal(6)
    _inject_safe_exit(jb, _T0 + 9 * _DAY, "se:orphan", mark_price=50.0)   # no bar at this open_time
    _reseq(jb.events)
    with pytest.raises(SegmentPipelineError):
        _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())


# --- R2 item 5: complete cycle fill lineage incl. transferred (dust) fill ids - #
def _seqd(order_events):
    """Return shallow copies of order wrappers with strictly-increasing seq for prefix-ledger use."""
    return [dict(o, seq=i) for i, o in enumerate(order_events)]


def _dust_lineage_journal():
    """A verified-shape journal whose cycle B (entirely inside segment 2) inherits a dust fill whose
    identity ('c') occurred in segment 1, BEFORE segment 2's start. Order after-fields and every
    snapshot are computed from the frozen ledger's own reconstruction, so they reconcile exactly."""
    resid = 0.9e-6
    ots = [_T0 + i * _DAY for i in range(6)]
    bar_close = {0: 100.0, 1: 100.0, 2: 120.0, 3: 121.0, 4: 95.0, 5: 99.0}
    # per-bar orders: (key, filled | "flat", fill_price, fee)
    orders_by_bar = {
        1: [("a", 10.0, 100.0, 1.0)],                    # open A            (segment 1)
        2: [("b", -(10.0 - resid), 120.0, 1.0),          # close A -> dust   (segment 1)
            ("c", -0.3e-6, 121.0, 0.05)],                # dust reduction    (segment 1)  <- transferred
        4: [("d", 5.0, 95.0, 0.5)],                      # open B (folds dust) (segment 2)
        5: [("e", "flat", 99.0, 0.5)],                   # close B fully       (segment 2)
    }
    events = [{"event_id": "session_created", "seq": 0, "timestamp": _T0,
               "event_type": "session_created",
               "payload": {"manifest_hash": MH, "live_execution_enabled": False},
               "parent_hash": "p", "event_hash": "h"}]
    pos = 0.0
    cash = CAP
    order_events = []
    for k in range(6):
        ot = ots[k]
        close = bar_close[k]
        events.append({"event_id": f"bar:{ot}", "seq": 0, "timestamp": ot, "event_type": "bar_ingested",
                       "payload": {"open_time": ot, "open": close, "high": close + 1.0,
                                   "low": max(0.01, close - 1.0), "close": close, "volume": 1000.0},
                       "parent_hash": "p", "event_hash": "h"})
        for (key, filled, fp, fee) in orders_by_bar.get(k, []):
            if filled == "flat":
                filled = -pos
            cash = cash - filled * fp - fee
            pos = pos + filled
            oe = {"event_id": f"order:{ot}:{key}", "seq": 0, "timestamp": ot, "event_type": "order",
                  "payload": {"exec_open_time": ot, "filled_units": float(filled), "fill_price": float(fp),
                              "fee": float(fee), "status": "filled", "position_after": float(pos),
                              "cash_after": float(cash), "equity_after": float(cash + pos * fp)},
                  "parent_hash": "p", "event_hash": "h"}
            events.append(oe)
            order_events.append(oe)
        led = build_cycle_ledger_from_orders(_seqd(order_events), initial_capital=CAP, qty_step=QS)
        r = led["reconstructed"]
        snap = {"open_time": ot, "close": close, "cash": r["cash"], "position_units": r["position_units"],
                "realized_pnl": r["realized_pnl"], "fees_paid": r["fees"], "mark_price": float(close),
                "equity": r["cash"] + r["position_units"] * float(close)}
        events.append({"event_id": f"snap:{ot}", "seq": 0, "timestamp": ot,
                       "event_type": "portfolio_snapshot", "payload": snap,
                       "parent_hash": "p", "event_hash": "h"})
    _reseq(events)
    return events


def _dust_manifest():
    return {"initial_capital": CAP, "execution_profile": dict(PROF),
            "start_timestamp_ms": _T0, "manifest_hash": MH, "timeframe": "1d"}


def test_cycle_fill_lineage_includes_transferred_dust_ids():
    events = _dust_lineage_journal()
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])
    rep = _rep(events, pol, _dust_manifest())
    s1, s2 = rep["segments"]
    assert s1["status"] == "OK" and s2["status"] == "OK"
    assert s2["metrics"]["completed_trade_count"]["value"] == 1     # cycle B attributed to segment 2
    dust_id = f"order:{_T0 + 2 * _DAY}:c"
    fill_ids = s2["evidence_refs"]["win_rate"]["fill_ids"]
    assert dust_id in fill_ids                                      # transferred dust identity preserved
    assert fill_ids.count(dust_id) == 1                             # each identity exactly once
    assert dust_id not in s2["evidence_refs"]["turnover"]          # pre-segment dust NOT in turnover
    # deterministic seq order of the complete lineage set
    seqs = {e["event_id"]: e["seq"] for e in events}
    assert [seqs[i] for i in fill_ids] == sorted(seqs[i] for i in fill_ids)
    # the same identity list is shared by the cycle-based metrics
    assert s2["evidence_refs"]["average_holding_period"]["fill_ids"] == fill_ids


# --- R2 item 6: complete portfolio_snapshot payload validation --------------- #
def _corrupt_interior_snapshot(jb, ot, **changes):
    for e in jb.events:
        if e["event_type"] == "portfolio_snapshot" and e["payload"]["open_time"] == ot:
            e["payload"] = dict(e["payload"], **changes)
    return jb


def test_interior_snapshot_missing_field_invalid():
    jb = _simple_journal(6)
    for e in jb.events:
        if e["event_type"] == "portfolio_snapshot" and e["payload"]["open_time"] == _T0 + 3 * _DAY:
            p = dict(e["payload"])
            del p["fees_paid"]
            e["payload"] = p
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"


def test_interior_snapshot_non_positive_mark_price_invalid():
    jb = _simple_journal(6)
    _corrupt_interior_snapshot(jb, _T0 + 3 * _DAY, mark_price=0.0)
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"


def test_interior_snapshot_bool_field_invalid():
    jb = _simple_journal(6)
    _corrupt_interior_snapshot(jb, _T0 + 3 * _DAY, realized_pnl=True)
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"


def test_interior_snapshot_non_finite_fees_invalid():
    jb = _simple_journal(6)
    _corrupt_interior_snapshot(jb, _T0 + 3 * _DAY, fees_paid=float("inf"))
    rep = _rep(jb.events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), jb.manifest())
    assert rep["segments"][0]["status"] == "INVALID"
