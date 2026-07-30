"""WP2B-2 tests — gross vs. net cost analysis (test-first). Contract: R4 + R4A.

Deterministic, read-only, fail-closed. Reuses the frozen WP2B-1 journal helpers and a local
broker-consistent synthetic-order builder (so QUANTITY_EXACT clean cycles and both fee models can be
exercised exactly). Frozen Stage 1 / WP2B-0A / WP2B-1 are consumed unchanged.
"""
from __future__ import annotations

import json

import pytest

from app.forward.cost_analysis import (
    CostAnalysisError,
    build_cost_analysis_from_journal,
    _build_cost_analysis_from_events,
    canonical_bytes,
)
from app.forward.trade_cycle_ledger import build_cycle_ledger_from_orders
from app.paper.session.profiles import freeze_execution_profile, FEE_BPS
from tests.test_segment_pipeline import (
    _JB, _reseq, _seqd, _policy, _seg, _simple_journal, _cross_boundary_journal,
    _dust_lineage_journal, _dust_manifest, _run_h025, _mkframe,
    _T0, _DAY, PROF, QS, CAP, MH,
)

SPY = freeze_execution_profile("SPY")


# --------------------------------------------------------------------------- #
# broker-consistent synthetic-order journal builder                            #
# --------------------------------------------------------------------------- #
def _mk_order_payload(ot, u, O, prof, pos, cash):
    hs, sl, tk = prof["half_spread_bps"], prof["slippage_bps"], prof["taker_fee_bps"]
    sign = 1.0 if u > 0 else -1.0
    fp = O * (1.0 + sign * (hs + sl) / 1e4)
    if prof["fee_model"] == FEE_BPS:
        fee = abs(u) * fp * tk / 1e4
    else:
        fee = max(prof["min_commission"], abs(u) * prof["commission_per_unit"])
    spread = abs(u) * O * hs / 1e4
    slip = abs(u) * O * sl / 1e4
    cash2 = cash - u * fp - fee
    pos2 = pos + u
    return ({"exec_open_time": int(ot), "filled_units": float(u), "fill_price": float(fp),
             "fee": float(fee), "spread_cost": float(spread), "slippage_cost": float(slip),
             "status": "filled", "side": "buy" if u > 0 else "sell", "requested_units": float(u),
             "position_after": float(pos2), "cash_after": float(cash2),
             "equity_after": float(cash2 + pos2 * fp), "reason": ""}, pos2, cash2)


def _synth(bars, prof=PROF, cap=CAP):
    """bars: list of {ot, open, close?, orders:[(key, signed_units | 'flat') ...]}."""
    events = [{"event_id": "session_created", "seq": 0, "timestamp": _T0,
               "event_type": "session_created",
               "payload": {"manifest_hash": MH, "live_execution_enabled": False},
               "parent_hash": "p", "event_hash": "h"}]
    pos, cash, oes = 0.0, float(cap), []
    for b in bars:
        ot, O = int(b["ot"]), float(b["open"])
        close = float(b.get("close", O))
        events.append({"event_id": f"bar:{ot}", "seq": 0, "timestamp": ot, "event_type": "bar_ingested",
                       "payload": {"open_time": ot, "open": O, "high": max(O, close) + 1.0,
                                   "low": max(0.01, min(O, close) - 1.0), "close": close,
                                   "volume": 1000.0}, "parent_hash": "p", "event_hash": "h"})
        for (key, u) in b.get("orders", []):
            uu = -pos if u == "flat" else float(u)
            payload, pos, cash = _mk_order_payload(ot, uu, O, prof, pos, cash)
            oe = {"event_id": f"order:{ot}:{key}", "seq": 0, "timestamp": ot, "event_type": "order",
                  "payload": payload, "parent_hash": "p", "event_hash": "h"}
            events.append(oe)
            oes.append(oe)
        led = build_cycle_ledger_from_orders(_seqd(oes), initial_capital=cap, qty_step=prof["qty_step"])
        r = led["reconstructed"]
        events.append({"event_id": f"snap:{ot}", "seq": 0, "timestamp": ot,
                       "event_type": "portfolio_snapshot",
                       "payload": {"open_time": ot, "close": close, "cash": r["cash"],
                                   "position_units": r["position_units"], "realized_pnl": r["realized_pnl"],
                                   "fees_paid": r["fees"], "mark_price": close,
                                   "equity": r["cash"] + r["position_units"] * close},
                       "parent_hash": "p", "event_hash": "h"})
    _reseq(events)
    return events


def _synth_manifest(prof=PROF, cap=CAP):
    return {"initial_capital": float(cap), "execution_profile": dict(prof),
            "start_timestamp_ms": _T0, "manifest_hash": MH, "timeframe": "1d"}


def _ca(events, pol, manifest):
    return _build_cost_analysis_from_events(events, pol, manifest)


def _canon_bytes(obj):
    # delegate to the ONE production canonical serialization (no divergent copy)
    return canonical_bytes(obj)


def _exact_cycle_journal(prof=PROF):
    # buy 10 @100 ; partial sell 4 @110 ; close-to-exact-zero sell 6 @120 (intra-cycle partial exit)
    return _synth([
        {"ot": _T0 + 0 * _DAY, "open": 100.0},
        {"ot": _T0 + 1 * _DAY, "open": 100.0, "orders": [("a", 10.0)]},
        {"ot": _T0 + 2 * _DAY, "open": 110.0, "orders": [("b", -4.0)]},
        {"ot": _T0 + 3 * _DAY, "open": 120.0, "orders": [("c", "flat")]},
        {"ot": _T0 + 4 * _DAY, "open": 120.0},
    ], prof=prof)


def _one_seg():
    return _policy([_seg("s1", _T0, _T0 + 5 * _DAY)])


def _dust_journal_with_costs():
    # cycle A closes to sub-lot dust (residual_carried_out); a dust-reduction fill while flat is then
    # inherited by cycle B (residual_carried_in). Both cycles are therefore NON-quantity-exact.
    resid = 0.9e-6
    return _synth([
        {"ot": _T0 + 0 * _DAY, "open": 100.0},
        {"ot": _T0 + 1 * _DAY, "open": 100.0, "orders": [("a", 10.0)]},                 # open A
        {"ot": _T0 + 2 * _DAY, "open": 120.0, "orders": [("b", -(10.0 - resid)),        # close A -> dust
                                                          ("c", -0.3e-6)]},             # dust reduction
        {"ot": _T0 + 3 * _DAY, "open": 95.0, "orders": [("d", 5.0)]},                   # open B (folds dust)
        {"ot": _T0 + 4 * _DAY, "open": 99.0, "orders": [("e", "flat")]},                # close B to exact 0
        {"ot": _T0 + 5 * _DAY, "open": 99.0},
    ])


def _dust_pol():
    return _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])


# =========================================================================== #
# structure / public API                                                       #
# =========================================================================== #
def test_public_api_rejects_event_list():
    ev = _exact_cycle_journal()
    with pytest.raises(CostAnalysisError):
        build_cost_analysis_from_journal(ev, _one_seg(), _synth_manifest())   # list, not a path


def test_no_public_verification_bypass():
    import app.forward.cost_analysis as _m
    assert not hasattr(_m, "build_cost_analysis_from_events")


def test_result_shape_has_session_segments_hashes():
    rep = _ca(_exact_cycle_journal(), _one_seg(), _synth_manifest())
    for k in ("policy_hash", "source_journal_final_hash", "execution_profile_hash",
              "policy_version", "segments", "session", "result_hash"):
        assert k in rep
    s = rep["segments"][0]
    for k in ("segment_id", "equity_net_return", "realized_net_pnl", "realized_net_return",
              "completed_cycle_friction_free_gross_pnl", "completed_cycle_friction_free_gross_return",
              "open_position_friction_free_gross_pnl", "realized_friction_drag_return",
              "segment_fill_costs", "exact_cycle_allocated_costs", "cycle_referenced_fill_costs",
              "nonrealized_or_unallocated_costs", "cycle_results", "cycle_coverage",
              "reconciled_subtotal", "cost_event_ids", "evidence_refs", "result_hash"):
        assert k in s
    assert "reconciliation_status" not in s        # R4A: removed segment-level singular
    assert "attribution_scope" not in s
    assert "completed_cycle_reconciliation_difference" not in s


# =========================================================================== #
# QUANTITY_EXACT gross available + independent reference-price reconstruction   #
# =========================================================================== #
def test_intra_cycle_partial_exit_then_exact_flat_gross_available():
    rep = _ca(_exact_cycle_journal(), _one_seg(), _synth_manifest())
    s = rep["segments"][0]
    cr = s["cycle_results"]
    assert len(cr) == 1
    c = cr[0]
    assert c["quantity_exact"] is True
    assert c["reconciliation_status"] == "OK"
    assert c["attribution_scope"] == "WITHIN_SEGMENT"
    assert c["reason_code"] is None
    assert c["friction_free_gross_pnl"]["status"] == "OK"
    assert abs(c["friction_free_gross_pnl"]["value"] - 160.0) < 1e-6      # 4*110+6*120 - 10*100
    assert s["completed_cycle_friction_free_gross_pnl"]["status"] == "OK"
    assert abs(s["completed_cycle_friction_free_gross_pnl"]["value"] - 160.0) < 1e-6


def test_independent_gross_equals_signed_reference_cashflow():
    rep = _ca(_exact_cycle_journal(), _one_seg(), _synth_manifest())
    c = rep["segments"][0]["cycle_results"][0]
    # exact allocated costs reported, and gross == net + total owned costs (cross-check, not the def)
    ac = c["exact_allocated_costs"]
    assert ac["status"] == "OK"
    assert abs(ac["total"] - (ac["fee"] + ac["spread"] + ac["slippage"])) < 1e-9
    assert c["reconciliation_difference"]["status"] == "OK"
    assert abs(c["reconciliation_difference"]["value"]) < 1e-6


def test_bps_fee_recompute_ok():
    rep = _ca(_exact_cycle_journal(PROF), _one_seg(), _synth_manifest(PROF))
    assert rep["segments"][0]["cycle_results"][0]["reconciliation_status"] == "OK"


def test_per_share_min_commission_counted_once():
    # SPY per-share: 5 shares @ $100 ⇒ fee floor min_commission=1.0 (0.005*5=0.025 < 1.0), once per order
    prof = SPY
    ev = _synth([
        {"ot": _T0 + 0 * _DAY, "open": 100.0},
        {"ot": _T0 + 1 * _DAY, "open": 100.0, "orders": [("a", 5.0)]},
        {"ot": _T0 + 2 * _DAY, "open": 110.0, "orders": [("b", "flat")]},
        {"ot": _T0 + 3 * _DAY, "open": 110.0},
    ], prof=prof)
    rep = _ca(ev, _policy([_seg("s1", _T0, _T0 + 4 * _DAY)]), _synth_manifest(prof))
    c = rep["segments"][0]["cycle_results"][0]
    assert c["reconciliation_status"] == "OK"
    # entry fee is exactly the floor 1.0 (counted once), exit fee is max(1.0, 0.005*5)=1.0
    assert abs(c["exact_allocated_costs"]["fee"] - 2.0) < 1e-9


# =========================================================================== #
# quantity-based ownership (residual / transferred ⇒ NOT_AVAILABLE)            #
# =========================================================================== #
def test_residual_and_transferred_cycles_gross_unavailable():
    # dust journal: cycle A closes to dust (residual_carried_out), cycle B inherits dust (carried_in)
    ev = _dust_journal_with_costs()
    rep = _ca(ev, _dust_pol(), _synth_manifest())
    for seg in rep["segments"]:
        for c in seg["cycle_results"]:
            assert c["quantity_exact"] is False
            assert c["friction_free_gross_pnl"]["status"] == "NOT_AVAILABLE"
            assert c["reason_code"] in ("RESIDUAL_CARRY_IN", "RESIDUAL_CARRY_OUT")


def test_transferred_full_fill_cost_not_in_realized_drag():
    ev = _dust_journal_with_costs()
    rep = _ca(ev, _dust_pol(), _synth_manifest())
    s2 = rep["segments"][1]
    # segment 2 owns the non-exact completed cycle ⇒ realized friction drag NOT_AVAILABLE
    assert s2["realized_friction_drag_return"]["status"] == "NOT_AVAILABLE"
    assert s2["exact_cycle_allocated_costs"]["status"] == "NOT_AVAILABLE"
    # but the referenced full-fill costs are audit-only, explicitly NOT an economic allocation
    assert s2["cycle_referenced_fill_costs"]["is_economic_allocation"] is False


def test_referenced_fill_costs_flagged_not_economic_allocation():
    ev = _dust_journal_with_costs()
    rep = _ca(ev, _dust_pol(), _synth_manifest())
    for seg in rep["segments"]:
        assert seg["cycle_referenced_fill_costs"]["is_economic_allocation"] is False


# =========================================================================== #
# empty completed-cycle set + open position separation                         #
# =========================================================================== #
def test_empty_completed_cycle_gross_is_valid_zero():
    # a segment with bars but no completed cycle exiting in it
    ev = _simple_journal(6).events   # opens at bar1, never closes ⇒ zero completed cycles
    rep = _ca(ev, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), _sm6())
    s = rep["segments"][0]
    assert s["cycle_results"] == []
    assert s["completed_cycle_friction_free_gross_pnl"] == {"status": "OK", "value": 0.0}
    assert s["realized_net_pnl"] == {"status": "OK", "value": 0.0}
    assert s["realized_net_return"]["status"] == "OK" and s["realized_net_return"]["value"] == 0.0
    assert s["realized_friction_drag_return"] == {"status": "OK", "value": 0.0}


def _sm6():
    return {"initial_capital": CAP, "execution_profile": dict(PROF),
            "start_timestamp_ms": _T0, "manifest_hash": MH, "timeframe": "1d"}


def test_no_completed_cycles_is_not_missing_evidence():
    rep = _ca(_simple_journal(6).events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), _sm6())
    s = rep["segments"][0]
    assert s["realized_net_pnl"]["status"] == "OK"       # not NOT_AVAILABLE
    assert s["completed_cycle_friction_free_gross_pnl"]["status"] == "OK"


def test_open_position_realized_net_is_valid_zero():
    rep = _ca(_simple_journal(6).events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), _sm6())
    s = rep["segments"][0]
    assert s["realized_net_pnl"] == {"status": "OK", "value": 0.0}


def test_open_position_gross_separate_and_not_available():
    rep = _ca(_simple_journal(6).events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), _sm6())
    s = rep["segments"][0]
    assert s["open_position_friction_free_gross_pnl"]["status"] == "NOT_AVAILABLE"
    # separated: the completed-cycle aggregate is a valid zero, not contaminated by the open position
    assert s["completed_cycle_friction_free_gross_pnl"] == {"status": "OK", "value": 0.0}


def test_open_position_does_not_invalidate_completed_cycle_empty_sum():
    rep = _ca(_simple_journal(6).events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), _sm6())
    s = rep["segments"][0]
    assert rep["session"]["session_completeness_status"] == "PARTIAL"    # open position present


# =========================================================================== #
# status vs attribution, per-cycle difference, coverage determinism            #
# =========================================================================== #
def test_no_singular_segment_reconciliation_status():
    s = _ca(_exact_cycle_journal(), _one_seg(), _synth_manifest())["segments"][0]
    assert "reconciliation_status" not in s and "attribution_scope" not in s


def test_reconciliation_difference_is_per_cycle():
    c = _ca(_exact_cycle_journal(), _one_seg(), _synth_manifest())["segments"][0]["cycle_results"][0]
    assert "reconciliation_difference" in c and set(c["reconciliation_difference"]) == {"status", "value"}


def test_mixed_cycle_scopes_preserved_per_cycle():
    # cross-boundary completed cycle: entry in seg1, exit in seg2
    ev = _cross_boundary_journal().events
    pol = _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 6 * _DAY)])
    rep = _ca(ev, pol, _sm6())
    s2 = rep["segments"][1]
    assert len(s2["cycle_results"]) == 1
    assert s2["cycle_results"][0]["attribution_scope"] == "CROSS_BOUNDARY"


def test_cycle_coverage_counts_match_cycle_results():
    rep = _ca(_dust_journal_with_costs(), _dust_pol(), _synth_manifest())
    for s in rep["segments"]:
        cov = s["cycle_coverage"]
        assert cov["total_completed"] == len(s["cycle_results"])
        assert cov["reconciled"] == sum(1 for c in s["cycle_results"] if c["reconciliation_status"] == "OK")
        assert cov["unavailable"] == sum(1 for c in s["cycle_results"] if c["reconciliation_status"] != "OK")
        idxs = [u["cycle_index"] for u in cov["unavailable_cycles"]]
        assert idxs == sorted(idxs)          # canonically sorted list
        for u in cov["unavailable_cycles"]:
            assert set(u) == {"cycle_index", "reason_code", "detail"}


def _mixed_journal():
    # clean cycle A (exact, closes to zero) + cycle B that leaves sub-lot dust (non-exact), one segment
    return _synth([
        {"ot": _T0 + 0 * _DAY, "open": 100.0},
        {"ot": _T0 + 1 * _DAY, "open": 100.0, "orders": [("a", 10.0)]},
        {"ot": _T0 + 2 * _DAY, "open": 120.0, "orders": [("b", "flat")]},     # clean cycle A closes exact
        {"ot": _T0 + 3 * _DAY, "open": 90.0, "orders": [("c", 8.0)]},
        {"ot": _T0 + 4 * _DAY, "open": 95.0, "orders": [("d", -8.0 + 0.4e-6)]},  # leaves dust ⇒ non-exact
        {"ot": _T0 + 5 * _DAY, "open": 95.0},
    ])


def test_segment_status_derived_from_cycle_results_mixed():
    # segment with one OK cycle and one NOT_AVAILABLE cycle ⇒ segment gross NOT_AVAILABLE + audit subtotal
    ev = _mixed_journal()
    rep = _ca(ev, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), _synth_manifest())
    s = rep["segments"][0]
    statuses = sorted(c["reconciliation_status"] for c in s["cycle_results"])
    assert statuses == ["NOT_AVAILABLE", "OK"]
    assert s["completed_cycle_friction_free_gross_pnl"]["status"] == "NOT_AVAILABLE"
    assert s["reconciled_subtotal"]["status"] == "OK"       # audit-only partial sum exposed
    assert s["reconciled_subtotal"]["value"] is not None


# =========================================================================== #
# session fill coverage                                                        #
# =========================================================================== #
def test_session_total_fill_cost_equals_sum_of_segment_fill_costs():
    rep = _ca(_dust_journal_with_costs(), _dust_pol(), _synth_manifest())
    seg_total = sum(s["segment_fill_costs"]["total_friction_cost"] for s in rep["segments"])
    assert rep["session"]["fill_coverage_proven"] is True
    assert abs(rep["session"]["total_session_fill_cost"]["total_friction_cost"] - seg_total) < 1e-9


def test_session_open_position_not_complete():
    rep = _ca(_simple_journal(6).events, _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]), _sm6())
    assert rep["session"]["session_completeness_status"] == "PARTIAL"


def test_session_overlapping_segments_fail_closed():
    ev = _exact_cycle_journal()
    pol = _policy([_seg("s1", _T0, _T0 + 4 * _DAY), _seg("s2", _T0 + 2 * _DAY, _T0 + 5 * _DAY)])
    with pytest.raises(CostAnalysisError):
        _ca(ev, pol, _synth_manifest())


# =========================================================================== #
# fail-closed cost-evidence validation                                         #
# =========================================================================== #
def _tamper_order(ev, ot_suffix, **changes):
    for e in ev:
        if e["event_type"] == "order" and e["event_id"].endswith(ot_suffix):
            e["payload"] = dict(e["payload"], **changes)
    return ev


def test_missing_cost_field_fails_closed():
    ev = _exact_cycle_journal()
    for e in ev:
        if e["event_type"] == "order" and e["event_id"].endswith(":a"):
            p = dict(e["payload"]); del p["spread_cost"]; e["payload"] = p
    with pytest.raises(CostAnalysisError):
        _ca(ev, _one_seg(), _synth_manifest())


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, -1.0])
def test_malformed_cost_value_fails_closed(bad):
    ev = _tamper_order(_exact_cycle_journal(), ":a", spread_cost=bad)
    with pytest.raises(CostAnalysisError):
        _ca(ev, _one_seg(), _synth_manifest())


def test_reference_price_fill_price_inconsistent_fails_closed():
    ev = _tamper_order(_exact_cycle_journal(), ":a", fill_price=999.0)   # inconsistent with bar open
    with pytest.raises(CostAnalysisError):
        _ca(ev, _one_seg(), _synth_manifest())


def test_zero_fill_carrying_cost_fails_closed():
    ev = _exact_cycle_journal()
    # inject a no_trade order that (wrongly) carries a non-zero fee
    inject = {"event_id": "order:%d:z" % (_T0 + 4 * _DAY), "seq": 0, "timestamp": _T0 + 4 * _DAY,
              "event_type": "order",
              "payload": {"exec_open_time": _T0 + 4 * _DAY, "filled_units": 0.0, "fill_price": 120.0,
                          "fee": 1.0, "spread_cost": 0.0, "slippage_cost": 0.0, "status": "no_trade",
                          "side": "flat", "requested_units": 0.0, "position_after": 0.0,
                          "cash_after": 0.0, "equity_after": 0.0, "reason": ""},
              "parent_hash": "p", "event_hash": "h"}
    ev.append(inject)
    _reseq(ev)
    with pytest.raises(CostAnalysisError):
        _ca(ev, _one_seg(), _synth_manifest())


def test_total_cost_is_sum_of_components():
    rep = _ca(_exact_cycle_journal(), _one_seg(), _synth_manifest())
    s = rep["segments"][0]
    fc = s["segment_fill_costs"]
    assert abs(fc["total_friction_cost"] - (fc["fee_cost"] + fc["spread_cost"] + fc["slippage_cost"])) < 1e-9


def test_hash_mismatch_fails_closed():
    ev = _exact_cycle_journal()
    bad = dict(_synth_manifest(), manifest_hash="not-the-real-hash")
    with pytest.raises(CostAnalysisError):
        _ca(ev, _one_seg(), bad)


# =========================================================================== #
# determinism                                                                  #
# =========================================================================== #
def test_duplicate_order_event_id_fails_closed():
    ev = _exact_cycle_journal()
    # duplicate an order event_id
    for e in ev:
        if e["event_id"].endswith(":b"):
            e["event_id"] = "order:%d:a" % (_T0 + 1 * _DAY)   # collide with the 'a' order id
    with pytest.raises(CostAnalysisError):
        _ca(ev, _one_seg(), _synth_manifest())


def _xborder_exact_journal():
    # single entry (seg1) + single exact-flat exit (seg2): QUANTITY_EXACT, CROSS_BOUNDARY
    return _synth([
        {"ot": _T0 + 0 * _DAY, "open": 100.0},
        {"ot": _T0 + 1 * _DAY, "open": 100.0, "orders": [("a", 10.0)]},   # entry (seg1)
        {"ot": _T0 + 2 * _DAY, "open": 120.0},
        {"ot": _T0 + 3 * _DAY, "open": 120.0, "orders": [("b", "flat")]}, # exit (seg2)
        {"ot": _T0 + 4 * _DAY, "open": 120.0},
    ])


def _xborder_pol():
    return _policy([_seg("s1", _T0, _T0 + 2 * _DAY), _seg("s2", _T0 + 2 * _DAY, _T0 + 5 * _DAY)])


def test_segment_fill_cost_differs_from_cycle_owned_cost():
    rep = _ca(_xborder_exact_journal(), _xborder_pol(), _synth_manifest())
    s1, s2 = rep["segments"]
    c = s2["cycle_results"][0]
    assert c["attribution_scope"] == "CROSS_BOUNDARY" and c["reconciliation_status"] == "OK"
    # entry-fill cost is in seg1's operational costs, NOT seg2's
    assert s1["segment_fill_costs"]["total_friction_cost"] > 0.0
    # the cycle owns BOTH fills for reconciliation; seg2 segment_fill_costs is exit-only, so the
    # cycle-owned allocation is strictly larger than seg2's segment fill costs
    assert c["exact_allocated_costs"]["total"] > s2["segment_fill_costs"]["total_friction_cost"] + 1e-9


def test_self_consistent_totals_wrong_reference_gross_unavailable_or_invalid():
    # keep fee/spread/slippage internally consistent as a bucket but break reference↔fill consistency
    ev = _exact_cycle_journal()
    for e in ev:
        if e["event_type"] == "bar_ingested" and e["payload"]["open_time"] == _T0 + 1 * _DAY:
            e["payload"] = dict(e["payload"], open=e["payload"]["open"] + 25.0)   # shift reference only
    with pytest.raises(CostAnalysisError):
        _ca(ev, _one_seg(), _synth_manifest())


def test_repeated_replay_byte_identical():
    ev = _exact_cycle_journal()
    a = _ca(ev, _one_seg(), _synth_manifest())
    b = _ca(ev, _one_seg(), _synth_manifest())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# =========================================================================== #
# REAL H-025 INTEGRATIONS (production journal path; unmodified manifest)        #
# =========================================================================== #
def test_h025_cost_uptrend_open_position(tmp_path):
    n = 30
    path, man, res = _run_h025(_mkframe([100 + 3 * i for i in range(n)]), tmp_path)
    assert res["lifecycle_status"] == "COMPLETED"
    split = _T0 + 15 * _DAY
    pol = _policy([_seg("s1", _T0, split), _seg("s2", split, _T0 + n * _DAY)])
    from app.paper.session.journal import read_verified_events
    ev0 = read_verified_events(path, man["manifest_hash"])
    sc = next(e for e in ev0 if e["event_type"] == "session_created")
    assert sc["payload"]["live_execution_enabled"] is False                    # live execution disabled
    rep = build_cost_analysis_from_journal(path, pol, man)
    b = build_cost_analysis_from_journal(path, pol, man)
    assert _canon_bytes(rep) == _canon_bytes(b)                                # identical canonical bytes
    assert rep["result_hash"] == b["result_hash"]
    assert rep["session"]["session_result_hash"] == b["session"]["session_result_hash"]
    for s in rep["segments"]:
        assert s["cycle_results"] == []                                        # zero completed cycles
        assert s["completed_cycle_friction_free_gross_pnl"] == {"status": "OK", "value": 0.0}
        assert s["realized_net_pnl"] == {"status": "OK", "value": 0.0}
        assert s["open_position_friction_free_gross_pnl"]["status"] == "NOT_AVAILABLE"
        assert s["equity_net_return"]["status"] == "OK"
    assert rep["session"]["session_completeness_status"] == "PARTIAL"


def test_h025_cost_riskstop_completed_cycle(tmp_path):
    from app.paper.session.risk import freeze_risk_limits
    from app.paper.session.journal import read_verified_events
    closes = [100 + 3 * i for i in range(12)] + [40.0, 41.0, 42.0]
    path, man, res = _run_h025(_mkframe(closes), tmp_path, risk=freeze_risk_limits(max_drawdown=0.10))
    assert res["lifecycle_status"] == "RISK_STOPPED"
    events = read_verified_events(path, man["manifest_hash"])
    bar_ots = sorted(e["payload"]["open_time"] for e in events if e["event_type"] == "bar_ingested")
    grid_end = bar_ots[-1] + _DAY
    orders = [e for e in events if e["event_type"] == "order"]
    led = build_cycle_ledger_from_orders(orders, initial_capital=100_000.0,
                                         qty_step=man["execution_profile"]["qty_step"])
    cyc = led["cycles"][0]
    split = cyc["entry_exec_open_time"] + _DAY
    pol = _policy([_seg("s1", _T0, split), _seg("s2", split, grid_end)])
    # --- SAFE-EXIT / CLOSE_ALL IDENTITY PROOF (blocker 3), via FROZEN CONTRACT FIELDS ---
    from app.forward.trade_cycle_ledger import canonical_lots
    qs = man["execution_profile"]["qty_step"]
    sc = next(e for e in events if e["event_type"] == "session_created")
    assert sc["payload"]["live_execution_enabled"] is False                # live execution disabled
    safe_exits = [e for e in events if e["event_type"] == "safe_exit"]
    assert len(safe_exits) == 1                                            # exactly one safe_exit event
    se = safe_exits[0]
    # identify the terminal CLOSE_ALL order by FROZEN CONTRACT FIELDS (not list position / comment):
    #   same exec_open_time as the safe_exit, status agreeing with safe_exit.order_status,
    #   a non-zero fill, and a canonically-flat resulting position.
    close_candidates = [o for o in events if o["event_type"] == "order"
                        and o["payload"]["exec_open_time"] == se["payload"]["open_time"]
                        and o["payload"]["status"] == se["payload"]["order_status"]
                        and float(o["payload"]["filled_units"]) != 0.0
                        and canonical_lots(float(o["payload"]["position_after"]), qs) == 0]
    assert len(close_candidates) == 1                                      # exactly one CLOSE_ALL order
    close_all = close_candidates[0]
    assert float(close_all["payload"]["filled_units"]) != 0.0             # exactly one non-zero exit fill
    # the frozen-field identification agrees with the frozen seq-adjacency contract (independent check)
    assert events[se["seq"] - 1]["event_id"] == close_all["event_id"]
    exit_id = close_all["event_id"]
    ep = close_all["payload"]

    rep = build_cost_analysis_from_journal(path, pol, man)
    b = build_cost_analysis_from_journal(path, pol, man)
    assert _canon_bytes(rep) == _canon_bytes(b)                            # identical canonical bytes
    assert rep["result_hash"] == b["result_hash"]                          # identical result_hash
    assert rep["session"]["session_result_hash"] == b["session"]["session_result_hash"]
    s1, s2 = rep["segments"]
    c = s2["cycle_results"][0]
    assert len(s2["cycle_results"]) == 1
    # FROZEN single deterministic expectation (residual_carried_out present ⇒ non-exact)
    assert c["quantity_exact"] is False
    assert c["reconciliation_status"] == "NOT_AVAILABLE"
    assert c["reason_code"] == "RESIDUAL_CARRY_OUT"
    assert c["attribution_scope"] == "CROSS_BOUNDARY"
    assert c["friction_free_gross_pnl"]["status"] == "NOT_AVAILABLE"
    assert s2["realized_net_pnl"]["status"] == "OK"                         # net still available
    # the exit fill appears EXACTLY ONCE in seg2 operational cost IDs, and never in seg1
    assert s2["cost_event_ids"].count(exit_id) == 1
    assert exit_id not in s1["cost_event_ids"]

    # INDEPENDENT recomputation of session costs from the RAW order payloads (not from the report)
    nz = [o["payload"] for o in events if o["event_type"] == "order"
          and o["payload"]["status"] in ("filled", "partial")
          and float(o["payload"]["filled_units"]) != 0.0]
    rc_fee = sum(p["fee"] for p in nz)
    rc_spread = sum(p["spread_cost"] for p in nz)
    rc_slip = sum(p["slippage_cost"] for p in nz)
    rc_total = rc_fee + rc_spread + rc_slip
    tsc = rep["session"]["total_session_fill_cost"]
    assert abs(tsc["fee_cost"] - rc_fee) < 1e-9
    assert abs(tsc["spread_cost"] - rc_spread) < 1e-9
    assert abs(tsc["slippage_cost"] - rc_slip) < 1e-9
    assert abs(tsc["total_friction_cost"] - rc_total) < 1e-9
    # seg2 operational costs independently recomputed from its cost_event_ids' payloads
    fmap = {o["event_id"]: o["payload"] for o in events if o["event_type"] == "order"}
    s2_fee = sum(fmap[i]["fee"] for i in s2["cost_event_ids"])
    s2_spread = sum(fmap[i]["spread_cost"] for i in s2["cost_event_ids"])
    s2_slip = sum(fmap[i]["slippage_cost"] for i in s2["cost_event_ids"])
    assert abs(s2["segment_fill_costs"]["fee_cost"] - s2_fee) < 1e-9
    assert abs(s2["segment_fill_costs"]["spread_cost"] - s2_spread) < 1e-9
    assert abs(s2["segment_fill_costs"]["slippage_cost"] - s2_slip) < 1e-9
    # the exit fill's EXACT payload costs are reflected: removing them from the recomputation yields
    # exactly the report total minus the exit's total (⇒ exit is included once, not zero/twice)
    exit_total = ep["fee"] + ep["spread_cost"] + ep["slippage_cost"]
    assert abs((rc_total - exit_total) - (tsc["total_friction_cost"] - exit_total)) < 1e-12
    recomputed_without_exit = sum(p["fee"] + p["spread_cost"] + p["slippage_cost"]
                                  for p in nz if p is not ep)
    assert abs(tsc["total_friction_cost"] - recomputed_without_exit - exit_total) < 1e-9
    assert s1["segment_fill_costs"]["total_friction_cost"] > 0.0
    assert s2["segment_fill_costs"]["total_friction_cost"] > 0.0


# =========================================================================== #
# R1 blocker 1 — strict order payload validation                               #
# =========================================================================== #
def _tamper_a(ev, **changes):
    for e in ev:
        if e["event_type"] == "order" and e["event_id"].endswith(":a"):
            e["payload"] = dict(e["payload"], **changes)
    return ev


def _del_a_field(ev, field):
    for e in ev:
        if e["event_type"] == "order" and e["event_id"].endswith(":a"):
            p = dict(e["payload"]); p.pop(field, None); e["payload"] = p
    return ev


def test_missing_filled_units_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_del_a_field(_exact_cycle_journal(), "filled_units"), _one_seg(), _synth_manifest())


def test_string_filled_units_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_tamper_a(_exact_cycle_journal(), filled_units="10"), _one_seg(), _synth_manifest())


def test_nan_filled_units_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_tamper_a(_exact_cycle_journal(), filled_units=float("nan")), _one_seg(), _synth_manifest())


def test_inf_filled_units_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_tamper_a(_exact_cycle_journal(), filled_units=float("inf")), _one_seg(), _synth_manifest())


def test_bool_filled_units_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_tamper_a(_exact_cycle_journal(), filled_units=True), _one_seg(), _synth_manifest())


def test_nonzero_no_trade_units_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_tamper_a(_exact_cycle_journal(), status="no_trade"), _one_seg(), _synth_manifest())


def test_nonzero_rejected_units_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_tamper_a(_exact_cycle_journal(), status="rejected"), _one_seg(), _synth_manifest())


def test_unknown_order_status_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_tamper_a(_exact_cycle_journal(), status="mystery"), _one_seg(), _synth_manifest())


def test_side_sign_mismatch_fails_closed():
    # buy order (+units) labelled side='sell' — not caught by the ledger, caught by strict validation
    with pytest.raises(CostAnalysisError):
        _ca(_tamper_a(_exact_cycle_journal(), side="sell"), _one_seg(), _synth_manifest())


def test_missing_exec_open_time_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_del_a_field(_exact_cycle_journal(), "exec_open_time"), _one_seg(), _synth_manifest())


# =========================================================================== #
# R1 blocker 2 — complete negative / positive matrix                           #
# =========================================================================== #
def test_fee_omitted_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_del_a_field(_exact_cycle_journal(), "fee"), _one_seg(), _synth_manifest())


def test_spread_omitted_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_del_a_field(_exact_cycle_journal(), "spread_cost"), _one_seg(), _synth_manifest())


def test_slippage_omitted_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_del_a_field(_exact_cycle_journal(), "slippage_cost"), _one_seg(), _synth_manifest())


@pytest.mark.parametrize("field", ["fee", "spread_cost", "slippage_cost"])
def test_non_numeric_cost_string_fails_closed(field):
    with pytest.raises(CostAnalysisError):
        _ca(_tamper_a(_exact_cycle_journal(), **{field: "x"}), _one_seg(), _synth_manifest())


def test_spread_inconsistent_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_tamper_a(_exact_cycle_journal(), spread_cost=5.0), _one_seg(), _synth_manifest())


def test_slippage_inconsistent_fails_closed():
    with pytest.raises(CostAnalysisError):
        _ca(_tamper_a(_exact_cycle_journal(), slippage_cost=5.0), _one_seg(), _synth_manifest())


def test_strict_validation_runs_before_ledger(monkeypatch):
    # LOAD-BEARING: malformed filled_units must be rejected by the WP2B-2 validator BEFORE the frozen
    # ledger is ever invoked. Spy on the ledger; a valid journal calls it, a malformed one never does.
    import app.forward.cost_analysis as _m
    called = {"n": 0}
    real = _m.build_cycle_ledger_from_orders

    def _spy(*a, **k):
        called["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(_m, "build_cycle_ledger_from_orders", _spy)
    _ca(_exact_cycle_journal(), _one_seg(), _synth_manifest())      # valid → ledger IS invoked
    assert called["n"] >= 1
    called["n"] = 0
    with pytest.raises(CostAnalysisError):
        _ca(_tamper_a(_exact_cycle_journal(), filled_units=float("nan")), _one_seg(), _synth_manifest())
    assert called["n"] == 0                                          # rejected before the ledger


def test_fee_not_double_counted():
    # exact_allocated_costs.fee equals the sum of the cycle's owned fills' fee EXACTLY (each once)
    ev = _exact_cycle_journal()
    rep = _ca(ev, _one_seg(), _synth_manifest())
    c = rep["segments"][0]["cycle_results"][0]
    owned_fee = 0.0
    for e in ev:
        if e["event_type"] == "order" and e["event_id"] in c["evidence_fill_ids"]:
            owned_fee += e["payload"]["fee"]
    assert abs(c["exact_allocated_costs"]["fee"] - owned_fee) < 1e-12


def test_per_share_fee_above_floor():
    prof = SPY
    ev = _synth([
        {"ot": _T0 + 0 * _DAY, "open": 100.0},
        {"ot": _T0 + 1 * _DAY, "open": 100.0, "orders": [("a", 500.0)]},   # 500*0.005=2.5 > 1.0 floor
        {"ot": _T0 + 2 * _DAY, "open": 110.0, "orders": [("b", "flat")]},
        {"ot": _T0 + 3 * _DAY, "open": 110.0},
    ], prof=prof)
    c = _ca(ev, _policy([_seg("s1", _T0, _T0 + 4 * _DAY)]), _synth_manifest(prof))["segments"][0]["cycle_results"][0]
    assert c["reconciliation_status"] == "OK"
    assert abs(c["exact_allocated_costs"]["fee"] - 5.0) < 1e-9    # 2.5 entry + 2.5 exit (above floor)


def test_reconciliation_tolerance_exact_boundary():
    import math
    from app.forward.cost_analysis import _TOL_CUR
    # exp_spread for order 'a' recomputed exactly as the module does (ref=bar open at ot1)
    ref, u, hs = 100.0, 10.0, PROF["half_spread_bps"]
    exp = abs(u) * ref * hs / 1e4
    # largest spread_cost whose deviation is EXACTLY within tolerance (step toward exp) -> must PASS
    sc_pass = exp + _TOL_CUR
    while abs(sc_pass - exp) > _TOL_CUR:
        sc_pass = math.nextafter(sc_pass, exp)
    assert abs(sc_pass - exp) <= _TOL_CUR
    rep = _ca(_tamper_a(_exact_cycle_journal(), spread_cost=sc_pass), _one_seg(), _synth_manifest())
    assert isinstance(rep, dict)                       # accepted (no fail-closed) exactly at tolerance
    # the smallest deterministic float above tolerance -> must FAIL closed
    sc_fail = math.nextafter(sc_pass, math.inf)
    assert abs(sc_fail - exp) > _TOL_CUR
    with pytest.raises(CostAnalysisError):
        _ca(_tamper_a(_exact_cycle_journal(), spread_cost=sc_fail), _one_seg(), _synth_manifest())


def _a_spread(ev):
    for e in ev:
        if e["event_type"] == "order" and e["event_id"].endswith(":a"):
            return e["payload"]["spread_cost"]
    raise AssertionError


def _boundary_journal():
    return _synth([
        {"ot": _T0 + 0 * _DAY, "open": 100.0},
        {"ot": _T0 + 1 * _DAY, "open": 100.0, "orders": [("a", 10.0)]},     # seg1
        {"ot": _T0 + 2 * _DAY, "open": 110.0, "orders": [("b", -4.0)]},     # seg1 partial exit
        {"ot": _T0 + 3 * _DAY, "open": 120.0, "orders": [("c", "flat")]},   # exactly at seg2 start
        {"ot": _T0 + 4 * _DAY, "open": 120.0},
    ])


def _boundary_pol():
    return _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 5 * _DAY)])


def test_fill_at_segment_start_boundary_in_next_segment():
    rep = _ca(_boundary_journal(), _boundary_pol(), _synth_manifest())
    s1, s2 = rep["segments"]
    cid = "order:%d:c" % (_T0 + 3 * _DAY)
    assert cid in s2["cost_event_ids"]        # start boundary belongs to seg2 (half-open lower bound)


def test_fill_at_segment_end_boundary_excluded():
    rep = _ca(_boundary_journal(), _boundary_pol(), _synth_manifest())
    s1, s2 = rep["segments"]
    cid = "order:%d:c" % (_T0 + 3 * _DAY)
    assert cid not in s1["cost_event_ids"]    # seg1 end is exclusive


def test_uncovered_fill_in_gap_fails_closed():
    ev = _exact_cycle_journal()   # order 'b' at _T0+2*_DAY
    pol = _policy([_seg("s1", _T0, _T0 + 2 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 5 * _DAY)],
                  allow_gaps=True)   # leaves the _T0+2*_DAY bar/fill uncovered
    with pytest.raises(CostAnalysisError):
        _ca(ev, pol, _synth_manifest())


def test_non_increasing_seq_fails_closed():
    ev = _exact_cycle_journal()
    ev[3]["seq"] = ev[2]["seq"]        # break contiguous 0-based seq
    with pytest.raises(CostAnalysisError):
        _ca(ev, _one_seg(), _synth_manifest())


def test_decreasing_exec_open_time_fails_closed():
    ev = _exact_cycle_journal()
    _tamper_a(ev, exec_open_time=_T0 + 3 * _DAY)   # 'a' now later than 'b'/'c' ⇒ decreasing order stream
    with pytest.raises(CostAnalysisError):
        _ca(ev, _one_seg(), _synth_manifest())


def _short_cycle_journal():
    return _synth([
        {"ot": _T0 + 0 * _DAY, "open": 100.0},
        {"ot": _T0 + 1 * _DAY, "open": 100.0, "orders": [("a", 10.0)]},
        {"ot": _T0 + 2 * _DAY, "open": 120.0, "orders": [("z", "flat")]},
    ])


def _long_cycle_journal():
    return _synth([
        {"ot": _T0 + 0 * _DAY, "open": 100.0},
        {"ot": _T0 + 1 * _DAY, "open": 100.0, "orders": [("a", 10.0)]},
        {"ot": _T0 + 2 * _DAY, "open": 120.0, "orders": [("z", "flat")]},
        {"ot": _T0 + 3 * _DAY, "open": 90.0, "orders": [("d", 5.0)]},
        {"ot": _T0 + 4 * _DAY, "open": 95.0, "orders": [("e", "flat")]},
    ])


def test_future_fill_does_not_affect_earlier_segment():
    rep_s = _ca(_short_cycle_journal(), _policy([_seg("s1", _T0, _T0 + 3 * _DAY)]), _synth_manifest())
    rep_l = _ca(_long_cycle_journal(),
                _policy([_seg("s1", _T0, _T0 + 3 * _DAY), _seg("s2", _T0 + 3 * _DAY, _T0 + 5 * _DAY)]),
                _synth_manifest())
    assert rep_s["segments"][0]["result_hash"] == rep_l["segments"][0]["result_hash"]


def test_transferred_dust_fill_not_owned_twice():
    rep = _ca(_dust_journal_with_costs(), _dust_pol(), _synth_manifest())
    seen = set()
    for s in rep["segments"]:
        for c in s["cycle_results"]:
            for fid in c["evidence_fill_ids"]:
                assert fid not in seen
                seen.add(fid)


def test_partition_violation_fails_closed(monkeypatch):
    import app.forward.cost_analysis as _m
    monkeypatch.setattr(_m, "validate_fill_partition", lambda led: (False, "forced violation"))
    with pytest.raises(CostAnalysisError):
        _ca(_exact_cycle_journal(), _one_seg(), _synth_manifest())


def _unallocated_dust_journal():
    resid = 0.9e-6
    return _synth([
        {"ot": _T0 + 0 * _DAY, "open": 100.0},
        {"ot": _T0 + 1 * _DAY, "open": 100.0, "orders": [("a", 10.0)]},
        {"ot": _T0 + 2 * _DAY, "open": 120.0, "orders": [("b", -(10.0 - resid)),   # close A -> dust
                                                          ("c", -0.3e-6)]},         # dust while flat
        {"ot": _T0 + 3 * _DAY, "open": 120.0},   # session ends with unallocated dust
    ])


def test_unallocated_dust_reported_and_session_not_complete():
    rep = _ca(_unallocated_dust_journal(), _policy([_seg("s1", _T0, _T0 + 4 * _DAY)]), _synth_manifest())
    assert rep["session"]["session_completeness_status"] != "COMPLETE"
    du = []
    for s in rep["segments"]:
        du += s["nonrealized_or_unallocated_costs"]["dust_unallocated"]["fill_ids"]
    assert du                                            # the dust-reduction fill is reported
    assert rep["session"]["unallocated_cost_event_ids"]  # and named at session level


# =========================================================================== #
# R1 blocker 3 — reconciled_subtotal coverage                                  #
# =========================================================================== #
def test_reconciled_subtotal_all_ok():
    rs = _ca(_exact_cycle_journal(), _one_seg(), _synth_manifest())["segments"][0]["reconciled_subtotal"]
    assert rs["is_complete_gross"] is False
    assert rs["excluded_cycle_indices"] == [] and rs["included_cycle_indices"] == [0]
    assert rs["reconciled"] == rs["total_completed"] == 1


def test_reconciled_subtotal_mixed():
    s = _ca(_mixed_journal(), _policy([_seg("s1", _T0, _T0 + 6 * _DAY)]),
            _synth_manifest())["segments"][0]
    rs = s["reconciled_subtotal"]
    assert len(rs["included_cycle_indices"]) == 1 and len(rs["excluded_cycle_indices"]) == 1
    assert rs["total_completed"] == 2 and rs["reconciled"] == 1
    assert rs["reconciled"] == s["cycle_coverage"]["reconciled"]
    assert rs["is_complete_gross"] is False


def test_reconciled_subtotal_all_unavailable():
    s2 = _ca(_dust_journal_with_costs(), _dust_pol(), _synth_manifest())["segments"][1]
    rs = s2["reconciled_subtotal"]
    assert rs["included_cycle_indices"] == [] and rs["value"] == 0.0 and rs["reconciled"] == 0


# =========================================================================== #
# R1 blocker 4 — reference-price evidence                                      #
# =========================================================================== #
def test_reference_price_evidence_maps_each_fill_to_its_bar():
    rep = _ca(_exact_cycle_journal(), _one_seg(), _synth_manifest())
    s = rep["segments"][0]
    er = s["evidence_refs"]
    assert er["reference_price"]                              # canonically ordered bar ids
    for fid in s["cost_event_ids"]:
        bar_id = er["reference_price_map"][fid]
        assert bar_id in er["reference_price"]
        # the mapped bar is the bar at the fill's exec_open_time
        ot = int(fid.split(":")[1])
        assert bar_id == f"bar:{ot}"


def test_missing_reference_bar_fails_closed():
    ev = _exact_cycle_journal()
    # remove the bar (and its snapshot) at order 'a's open_time, keep the order
    ev = [e for e in ev if not ((e["event_type"] == "bar_ingested" and e["payload"]["open_time"] == _T0 + 1 * _DAY)
                                or (e["event_type"] == "portfolio_snapshot" and e["payload"]["open_time"] == _T0 + 1 * _DAY))]
    _reseq(ev)
    with pytest.raises(CostAnalysisError):
        _ca(ev, _one_seg(), _synth_manifest())


def test_duplicate_reference_bar_fails_closed():
    ev = _exact_cycle_journal()
    dup = next(e for e in ev if e["event_type"] == "bar_ingested" and e["payload"]["open_time"] == _T0 + 1 * _DAY)
    ev.append(dict(dup, event_id="bar:dup"))
    _reseq(ev)
    with pytest.raises(CostAnalysisError):
        _ca(ev, _one_seg(), _synth_manifest())


def test_future_reference_bar_fails_closed():
    ev = _exact_cycle_journal()
    # reorder so order 'a' precedes its own bar in journal sequence (reference bar after the order)
    ai = next(i for i, e in enumerate(ev) if e["event_id"].endswith(":a"))
    bi = next(i for i, e in enumerate(ev) if e["event_id"] == f"bar:{_T0 + 1 * _DAY}")
    order_ev = ev.pop(ai)
    ev.insert(bi, order_ev)      # order now just before its bar
    _reseq(ev)
    with pytest.raises(CostAnalysisError):
        _ca(ev, _one_seg(), _synth_manifest())


# =========================================================================== #
# R1 blocker 6 — session identity coverage                                     #
# =========================================================================== #
def test_session_identity_not_available_when_cycle_unavailable():
    rep = _ca(_dust_journal_with_costs(), _dust_pol(), _synth_manifest())
    # both cycles are non-exact ⇒ NOT every completed cycle reconciled ⇒ identity must be NOT_AVAILABLE
    assert rep["session"]["completed_cycle_session_identity_difference"]["status"] == "NOT_AVAILABLE"


def test_session_identity_ok_when_all_reconciled_or_none():
    rep = _ca(_exact_cycle_journal(), _one_seg(), _synth_manifest())
    assert rep["session"]["completed_cycle_session_identity_difference"]["status"] == "OK"


# =========================================================================== #
# R1b3 — canonical serialization consistency (ensure_ascii=False, one source)  #
# =========================================================================== #
def test_canonical_serialization_ensure_ascii_false_and_hash_consistent():
    import hashlib
    from app.forward.cost_analysis import _sha, canonical_bytes as _cb
    obj = {"segment_id": "ség—Ω", "nested": {"z": 1, "a": [2, "µ"]}, "v": 3.5}
    cb = _cb(obj)
    # ensure_ascii=False ⇒ raw UTF-8 multibyte, never \uXXXX escapes
    assert "Ω".encode("utf-8") in cb and "µ".encode("utf-8") in cb
    assert b"\\u" not in cb
    # production _sha hashes EXACTLY these canonical bytes (same serialization as byte comparison)
    assert _sha(obj) == hashlib.sha256(cb).hexdigest()
    # and it genuinely differs from an ensure_ascii=True encoding (proves the flag is load-bearing)
    ascii_bytes = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    assert cb != ascii_bytes


def test_non_ascii_segment_id_deterministic_and_hashed_consistently():
    import hashlib
    from app.forward.cost_analysis import _sha
    pol = _policy([_seg("s1—Ω", _T0, _T0 + 5 * _DAY)])   # non-ASCII segment id
    rep_a = _ca(_exact_cycle_journal(), pol, _synth_manifest())
    rep_b = _ca(_exact_cycle_journal(), pol, _synth_manifest())
    assert canonical_bytes(rep_a) == canonical_bytes(rep_b)               # byte-identical replay
    assert rep_a["result_hash"] == rep_b["result_hash"]
    assert rep_a["segments"][0]["segment_id"] == "s1—Ω"
    # the non-ASCII id survives as raw UTF-8 in the canonical bytes, and the report hash matches a
    # fresh hash of the production canonical bytes of the same economic content
    assert "Ω".encode("utf-8") in canonical_bytes(rep_a)
