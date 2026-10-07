"""WP2B-0A tests — completed economic position-cycle ledger + canonical lots + dust-carry.

Written test-first. Covers the accepted R2/R3/R4 contracts and the R4 erratum-2 tolerance.
Read-only; deterministic; no wall clock; no RNG. Frozen Stage 1 is exercised only for the
H-025 infrastructure-evidence checks (never modified).
"""
from __future__ import annotations

import json
import math

import pytest

from app.forward.trade_cycle_ledger import (
    LedgerError,
    active_and_dust_units,
    build_cycle_ledger,
    canonical_lots,
    is_execution_flat,
    position_tolerance,
    reconcile_against_snapshot,
    reconstructed_equity,
)

_S = 1e-6  # a BTC-like qty_step


def _fill(u, px, fee, ot):
    return {"filled_units": float(u), "fill_price": float(px), "fee": float(fee), "exec_open_time": int(ot)}


# --------------------------------------------------------------------------- #
# canonical lots — authoritative, mirrors frozen execution rounding            #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("pos,step,expected", [
    (0.0, _S, 0),
    (0.999 * _S, _S, 0),          # sub-one-lot residual is dust
    (1.0 * _S, _S, 1),            # exactly one lot is material
    (1.0001 * _S, _S, 1),
    (2.5 * _S, _S, 2),            # toward-zero
    (-0.999 * _S, _S, 0),
    (-1.0 * _S, _S, -1),
    (-0.0, _S, 0),                # negative zero
    (9.999999974752427e-07, _S, 0),   # real H-025 RISK_STOPPED residual
    (2.2737367544323206e-13, _S, 0),  # real H-025 crash-fixture residual
])
def test_canonical_lots_boundaries(pos, step, expected):
    assert canonical_lots(pos, step) == expected


def test_is_execution_flat():
    assert is_execution_flat(0.999 * _S, _S) is True
    assert is_execution_flat(1.0 * _S, _S) is False


def test_canonical_lots_rejects_bad_input():
    with pytest.raises(LedgerError):
        canonical_lots(True, _S)             # bool
    with pytest.raises(LedgerError):
        canonical_lots(float("nan"), _S)
    with pytest.raises(LedgerError):
        canonical_lots(1.0, 0.0)             # qty_step <= 0
    with pytest.raises(LedgerError):
        canonical_lots(1.0, -1e-6)


# --------------------------------------------------------------------------- #
# position tolerance (R4 erratum 2) — asset-specific, one-lot always fails      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("step", [1e-6, 1e-2, 1e-9, 5e-7, 1.0])
def test_position_tolerance_is_qty_step_bound(step):
    tau = position_tolerance(step)
    assert tau > 0
    assert tau < step
    assert tau <= 1e-9
    # a one-lot discrepancy must always exceed the tolerance
    assert step > tau


def test_position_tolerance_rejects_bad_step():
    with pytest.raises(LedgerError):
        position_tolerance(0.0)
    with pytest.raises(LedgerError):
        position_tolerance(float("inf"))


# --------------------------------------------------------------------------- #
# cycle grouping                                                               #
# --------------------------------------------------------------------------- #
def test_normal_entry_and_full_exit_one_completed_cycle():
    # buy 10 @100, sell 10 @110 -> one completed cycle
    fills = [_fill(10, 100.0, 1.0, 1000), _fill(-10, 110.0, 1.0, 2000)]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    assert len(led["cycles"]) == 1
    assert led["open_cycle"] is None
    c = led["cycles"][0]
    assert c["status"] == "completed"
    assert c["entry_exec_open_time"] == 1000 and c["exit_exec_open_time"] == 2000
    # gross = 10*(110-100) = 100 ; net = gross - entry_fee - exit_fee = 100 - 1 - 1 = 98
    assert math.isclose(c["gross"], 100.0, abs_tol=1e-9)
    assert math.isclose(c["realized_net"], 98.0, abs_tol=1e-9)
    assert c["residual_carried_out"] is None
    assert abs(led["reconstructed"]["position_units"]) < 1e-12


def test_scale_in_stays_in_one_open_cycle():
    fills = [_fill(10, 100.0, 1.0, 1000), _fill(5, 102.0, 0.5, 2000)]  # both buys
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    assert led["cycles"] == []              # nothing closed
    assert led["open_cycle"] is not None
    assert led["open_cycle"]["status"] == "open"
    assert led["open_cycle"]["n_fills"] == 2


def test_partial_scale_out_stays_in_same_cycle_until_flat():
    fills = [_fill(10, 100.0, 1.0, 1000), _fill(-4, 110.0, 0.4, 2000), _fill(-6, 120.0, 0.6, 3000)]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    assert len(led["cycles"]) == 1
    c = led["cycles"][0]
    assert c["n_fills"] == 3
    assert c["exit_exec_open_time"] == 3000


def test_zero_fill_and_rejected_create_no_cycle():
    fills = [
        {"filled_units": 0.0, "fill_price": 100.0, "fee": 0.0, "exec_open_time": 1000},
        {"filled_units": 0.0, "fill_price": 100.0, "fee": 0.0, "exec_open_time": 2000},
    ]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    assert led["cycles"] == [] and led["open_cycle"] is None


def test_safe_exit_ending_with_dust_completes_cycle_and_carries_residual():
    # close leaves a sub-qty_step raw residual (execution-flat, not accounting-flat)
    resid = 0.9e-6  # < qty_step -> canonical flat
    fills = [_fill(10.0, 100.0, 1.0, 1000), _fill(-(10.0 - resid), 90.0, 1.0, 2000)]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    assert len(led["cycles"]) == 1
    c = led["cycles"][0]
    assert c["status"] == "completed"
    co = c["residual_carried_out"]
    assert co is not None
    assert math.isclose(co["raw_units"], resid, rel_tol=1e-9, abs_tol=1e-15)
    assert math.isclose(led["dust_carry"]["raw_units"], resid, rel_tol=1e-9, abs_tol=1e-15)
    assert canonical_lots(led["reconstructed"]["position_units"], _S) == 0


def test_dust_followed_by_further_reduction_accrues_to_dust_no_new_cycle():
    resid = 0.9e-6
    fills = [
        _fill(10.0, 100.0, 1.0, 1000),
        _fill(-(10.0 - resid), 90.0, 1.0, 2000),   # close, leaves dust
        _fill(-0.4e-6, 91.0, 0.0, 3000),           # further dust reduction while flat
    ]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    assert len(led["cycles"]) == 1                  # still just one completed cycle
    assert led["open_cycle"] is None
    assert led["dust_carry"]["raw_units"] < resid   # dust reduced further


def test_dust_followed_by_new_entry_starts_clean_cycle_with_explicit_carry():
    resid = 0.9e-6
    fills = [
        _fill(10.0, 100.0, 1.0, 1000),
        _fill(-(10.0 - resid), 90.0, 1.0, 2000),   # close A, dust remains
        _fill(5.0, 95.0, 0.5, 3000),               # new material entry -> cycle B
        _fill(-5.0 - resid, 99.0, 0.5, 4000),      # close B fully (incl. carried dust)
    ]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    assert len(led["cycles"]) == 2
    a, b = led["cycles"]
    assert a["residual_carried_out"] is not None
    assert b["residual_carried_in"] is not None
    assert b["includes_inherited_dust"] is True
    assert math.isclose(a["residual_carried_out"]["raw_units"],
                        b["residual_carried_in"]["raw_units"], rel_tol=1e-9, abs_tol=1e-15)
    assert math.isclose(a["residual_carried_out"]["cost_basis"],
                        b["residual_carried_in"]["cost_basis"], rel_tol=1e-9, abs_tol=1e-12)


def test_active_units_plus_dust_equals_broker_raw_position():
    fills = [_fill(10.0, 100.0, 1.0, 1000), _fill(-4.0, 110.0, 0.4, 2000)]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    active_u, dust_u = active_and_dust_units(led)
    assert math.isclose(active_u + dust_u, led["reconstructed"]["position_units"], abs_tol=1e-12)


def test_no_dust_marked_value_double_counting():
    resid = 0.9e-6
    fills = [_fill(10.0, 100.0, 1.0, 1000), _fill(-(10.0 - resid), 90.0, 1.0, 2000)]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    mark = 90.0
    eq = reconstructed_equity(led, mark)
    # single marked value: cash + raw_position*mark  (NOT + separate dust term)
    r = led["reconstructed"]
    assert math.isclose(eq, r["cash"] + r["position_units"] * mark, abs_tol=1e-9)


def test_fee_and_pnl_conservation():
    fills = [_fill(10.0, 100.0, 1.0, 1000), _fill(-4.0, 110.0, 0.4, 2000), _fill(-6.0, 120.0, 0.6, 3000)]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    r = led["reconstructed"]
    assert math.isclose(r["fees"], 1.0 + 0.4 + 0.6, abs_tol=1e-9)
    # sum of cycle nets + dust net + open net == sum of per-fill nets == reconstructed realized minus allocated fees
    total_cycle_net = sum(c["realized_net"] for c in led["cycles"])
    total_cycle_net += led["dust_carry"]["net"] + (led["open_cycle"]["realized_net"] if led["open_cycle"] else 0.0)
    assert math.isclose(total_cycle_net, led["sum_fill_net"], abs_tol=1e-9)


def test_reconcile_against_snapshot():
    fills = [_fill(10.0, 100.0, 1.0, 1000), _fill(-10.0, 110.0, 1.0, 2000)]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    r = led["reconstructed"]
    snap = {"cash": r["cash"], "position_units": r["position_units"],
            "realized_pnl": r["realized_pnl"], "fees_paid": r["fees"],
            "mark_price": 110.0, "equity": r["cash"] + r["position_units"] * 110.0}
    checks, ok = reconcile_against_snapshot(led, snap)
    assert ok and all(checks.values())


def test_reconcile_tolerance_just_inside_and_outside():
    fills = [_fill(10.0, 100.0, 1.0, 1000), _fill(-10.0, 110.0, 1.0, 2000)]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    r = led["reconstructed"]
    tau = led["position_tolerance"]
    base = {"cash": r["cash"], "position_units": r["position_units"],
            "realized_pnl": r["realized_pnl"], "fees_paid": r["fees"],
            "mark_price": 110.0, "equity": r["cash"] + r["position_units"] * 110.0}
    inside = dict(base, position_units=r["position_units"] + tau * 0.5)
    outside = dict(base, position_units=r["position_units"] + tau * 5)  # still < qty_step
    assert reconcile_against_snapshot(led, inside)[1] is True
    assert reconcile_against_snapshot(led, outside)[1] is False
    one_lot = dict(base, position_units=r["position_units"] + _S)       # one-lot mismatch
    assert reconcile_against_snapshot(led, one_lot)[1] is False


def test_malformed_fills_fail_closed():
    with pytest.raises(LedgerError):
        build_cycle_ledger([{"filled_units": 1.0, "fill_price": 100.0, "fee": 0.0}], initial_capital=1.0, qty_step=_S)
    with pytest.raises(LedgerError):
        build_cycle_ledger([{"filled_units": True, "fill_price": 100.0, "fee": 0.0, "exec_open_time": 1}],
                           initial_capital=1.0, qty_step=_S)  # bool filled_units
    with pytest.raises(LedgerError):
        build_cycle_ledger([_fill(1.0, float("inf"), 0.0, 1)], initial_capital=1.0, qty_step=_S)
    with pytest.raises(LedgerError):
        build_cycle_ledger([_fill(1.0, 100.0, -1.0, 1)], initial_capital=1.0, qty_step=_S)  # neg fee
    with pytest.raises(LedgerError):
        build_cycle_ledger([_fill(1.0, 100.0, 0.0, 1)], initial_capital=0.0, qty_step=_S)   # bad capital


def test_determinism_two_runs_identical():
    fills = [_fill(10.0, 100.0, 1.0, 1000), _fill(-4.0, 110.0, 0.4, 2000), _fill(-6.0, 120.0, 0.6, 3000)]
    a = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    b = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# --------------------------------------------------------------------------- #
# H-025 infrastructure evidence (frozen engine, read-only)                     #
# --------------------------------------------------------------------------- #
def _h025_fills(frame, tmp_path, risk=None):
    import pandas as pd  # noqa
    from app.strategy_research.strategies import get_strategy
    from app.paper.session.profiles import freeze_execution_profile
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
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                     params={"length": 5}, frame=frame, journal_dir=str(tmp_path))
    fills = []
    with open(str(tmp_path) + "/journal.jsonl") as f:
        for line in f:
            e = json.loads(line)
            if e["event_type"] == "order":
                p = e["payload"]
                fills.append({"filled_units": p["filled_units"], "fill_price": p["fill_price"],
                              "fee": p["fee"], "exec_open_time": int(p["exec_open_time"])})
    return fills, res, man["execution_profile"]["qty_step"]


def _mkframe(closes):
    import pandas as pd
    _T0, _DAY = 1_600_000_000_000, 86_400_000
    opens = [closes[0]] + closes[:-1]
    rows = [{"open_time": _T0 + i * _DAY, "open": float(opens[i]),
             "high": max(opens[i], closes[i]) + 1.0, "low": max(0.01, min(opens[i], closes[i]) - 1.0),
             "close": float(closes[i]), "volume": 1000.0} for i in range(len(closes))]
    return pd.DataFrame(rows)


def test_h025_uptrend_zero_completed_cycles(tmp_path):
    frame = _mkframe([100 + 3 * i for i in range(30)])
    fills, res, qs = _h025_fills(frame, tmp_path)
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=qs)
    assert res["lifecycle_status"] == "COMPLETED"
    assert len(led["cycles"]) == 0            # never returns to flat -> 0 completed economic cycles
    assert led["open_cycle"] is not None       # still holding


def test_h025_risk_stop_one_completed_cycle(tmp_path):
    from app.paper.session.risk import freeze_risk_limits
    frame = _mkframe([100 + 3 * i for i in range(12)] + [40.0, 41.0, 42.0])
    fills, res, qs = _h025_fills(frame, tmp_path, risk=freeze_risk_limits(max_drawdown=0.10))
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=qs)
    assert res["lifecycle_status"] == "RISK_STOPPED"
    assert len(led["cycles"]) == 1             # safe-exit closes the single cycle


def test_h025_ledger_deterministic(tmp_path):
    frame = _mkframe([100 + 3 * i for i in range(30)])
    fills, _, qs = _h025_fills(frame, tmp_path)
    a = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=qs)
    b = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=qs)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# =========================================================================== #
# WP2B-0A HARDENING R1 — conservation, partitioning, sub-EPS, production ctor  #
# =========================================================================== #
from app.forward.trade_cycle_ledger import (  # noqa: E402
    build_cycle_ledger_from_orders,
    validate_fill_partition,
)
from app.paper.session.broker import PaperBroker  # noqa: E402
from app.paper.session.profiles import freeze_execution_profile  # noqa: E402


def _fid(u, px, fee, ot, fid):
    d = _fill(u, px, fee, ot)
    d["fill_id"] = fid
    return d


# ---- item 1: dust-carry net PnL must survive a fold, exactly once ---------- #
def test_dust_net_preserved_through_fold():
    resid = 0.9e-6
    fills = [
        _fid(10.0, 100.0, 1.0, 1000, "a"),                 # open A
        _fid(-(10.0 - resid), 120.0, 1.0, 2000, "b"),      # close A to dust (winning)
        _fid(-0.3e-6, 121.0, 0.05, 3000, "c"),             # later dust reduction: non-zero net+fee
        _fid(5.0, 95.0, 0.5, 4000, "d"),                   # open B (folds dust)
        _fid(-5.0 - (resid - 0.3e-6), 99.0, 0.5, 5000, "e"),  # close B
    ]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    # aggregate net conserved: sum of all bucket nets == sum of per-fill nets
    agg = sum(c["realized_net"] for c in led["cycles"])
    agg += led["dust_carry"]["net"] + (led["open_cycle"]["realized_net"] if led["open_cycle"] else 0.0)
    assert math.isclose(agg, led["sum_fill_net"], abs_tol=1e-9)
    # the dust reduction's net appears exactly once (folded into B), not lost, not doubled
    b = led["cycles"][1]
    assert b["residual_carried_in"] is not None
    assert "c" in b["residual_carried_in"]["transferred_fill_ids"]


def test_aggregate_net_unchanged_when_dust_transfers_buckets():
    resid = 0.8e-6
    fills = [
        _fid(10.0, 100.0, 1.0, 1000, "a"),
        _fid(-(10.0 - resid), 110.0, 1.0, 2000, "b"),
        _fid(4.0, 105.0, 0.4, 3000, "d"),                  # open B, folds dust (net 0 here)
        _fid(-4.0 - resid, 108.0, 0.4, 4000, "e"),
    ]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    agg = sum(c["realized_net"] for c in led["cycles"]) + led["dust_carry"]["net"]
    agg += (led["open_cycle"]["realized_net"] if led["open_cycle"] else 0.0)
    assert math.isclose(agg, led["sum_fill_net"], abs_tol=1e-9)


# ---- item 2: provable fill partitioning ------------------------------------ #
def test_fill_partition_disjoint_union_equals_all():
    fills = [_fid(10.0, 100.0, 1.0, 1000, "a"), _fid(-4.0, 110.0, 0.4, 2000, "b"),
             _fid(-6.0, 120.0, 0.6, 3000, "c")]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    ok, detail = validate_fill_partition(led)
    assert ok, detail
    assert set(led["all_fill_ids"]) == {"a", "b", "c"}


def test_duplicate_fill_id_fails_closed():
    fills = [_fid(10.0, 100.0, 1.0, 1000, "dup"), _fid(-10.0, 110.0, 1.0, 2000, "dup")]
    with pytest.raises(LedgerError):
        build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)


def test_fill_ids_exposed_on_cycles_and_dust():
    resid = 0.9e-6
    fills = [_fid(10.0, 100.0, 1.0, 1000, "a"), _fid(-(10.0 - resid), 90.0, 1.0, 2000, "b")]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    assert led["cycles"][0]["fill_ids"] == ["a", "b"]


# ---- item 3: sub-EPS non-zero fills must NOT be discarded ------------------- #
def test_only_exact_zero_is_skipped_sub_eps_is_accounted():
    fills = [_fid(10.0, 100.0, 1.0, 1000, "a"),
             _fid(-(10.0 - 0.9e-6), 90.0, 1.0, 2000, "b"),  # close to dust
             _fid(-5e-10, 91.0, 0.02, 3000, "tiny")]        # sub-_FILL_EPS non-zero fill w/ fee
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    # fee conserved incl. the tiny fill; tiny fill assigned to a bucket (dust) and not discarded
    assert math.isclose(led["reconstructed"]["fees"], 1.0 + 1.0 + 0.02, abs_tol=1e-12)
    ok, detail = validate_fill_partition(led)
    assert ok, detail
    assert "tiny" in set(led["all_fill_ids"])


def test_exact_zero_fill_creates_no_bucket_entry():
    fills = [{"filled_units": 0.0, "fill_price": 100.0, "fee": 0.0, "exec_open_time": 1, "fill_id": "z"}]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    assert led["all_fill_ids"] == []       # exact-zero is a no-fill


# ---- item 4: cycle accounting-basis evidence ------------------------------- #
def test_clean_cycle_basis_fields_from_b_only():
    fills = [_fid(10.0, 100.0, 1.0, 1000, "a"), _fid(-10.0, 110.0, 1.0, 2000, "b")]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    c = led["cycles"][0]
    for k in ("entry_fill_price", "entry_fee", "opening_cost_basis_after_fill",
              "opening_fee_basis_after_fill", "realized_gross", "realized_net",
              "exit_fill_price", "exit_exec_open_time", "residual_carried_in",
              "residual_carried_out", "includes_inherited_dust", "fill_ids"):
        assert k in c
    assert c["residual_carried_in"] is None            # clean cycle: no inheritance
    assert c["includes_inherited_dust"] is False
    assert math.isclose(c["entry_fill_price"], 100.0, abs_tol=1e-12)
    assert math.isclose(c["opening_cost_basis_after_fill"], 100.0, abs_tol=1e-12)
    assert math.isclose(c["exit_fill_price"], 110.0, abs_tol=1e-12)


def test_inherited_dust_cycle_exposes_weighted_basis():
    resid = 0.9e-6
    fills = [_fid(10.0, 100.0, 1.0, 1000, "a"), _fid(-(10.0 - resid), 90.0, 1.0, 2000, "b"),
             _fid(5.0, 95.0, 0.5, 3000, "d"), _fid(-5.0 - resid, 99.0, 0.5, 4000, "e")]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    b = led["cycles"][1]
    assert b["includes_inherited_dust"] is True
    assert b["residual_carried_in"] is not None
    assert math.isclose(b["entry_fill_price"], 95.0, abs_tol=1e-12)   # B's own new-entry fill price
    # weighted opening basis blends the carried residual basis (A's persisted entry ~100, since the
    # broker does NOT reset avg_entry_price on a sub-qty_step residual) with the new fill (95),
    # so it sits just above 95 and below 100
    assert 95.0 <= b["opening_cost_basis_after_fill"] <= 100.0


# ---- item 5: production order constructor + per-fill reconciliation --------- #
def _broker_orders(trades, *, asset="BTC/USD", cap=100_000.0):
    """Separate helper: use the FROZEN broker to build complete, valid order evidence."""
    prof = freeze_execution_profile(asset)
    b = PaperBroker(prof, initial_capital=cap, max_leverage=1.0)
    events = []
    seq = 0
    for target, ot, px in trades:
        b.mark(px)
        o = b.execute(target, exec_open_time=ot, exec_price=px)
        # wrap as a real journal-event structure (event_type/event_id/seq/payload)
        events.append({"event_type": "order", "event_id": f"order:{ot}", "seq": seq, "payload": o})
        seq += 1
    return events, prof["qty_step"]


def _tamper_payload(ev, **kw):
    return dict(ev, payload=dict(ev["payload"], **kw))


def test_from_orders_builds_and_reconciles():
    events, qs = _broker_orders([(1.0, 1000, 100.0), (0.0, 2000, 110.0)])
    led = build_cycle_ledger_from_orders(events, initial_capital=100_000.0, qty_step=qs)
    assert led["reconciled"] is True
    assert len(led["cycles"]) == 1


def test_from_orders_position_mismatch_fails_closed():
    events, qs = _broker_orders([(1.0, 1000, 100.0), (0.0, 2000, 110.0)])
    events[1] = _tamper_payload(events[1], position_after=events[1]["payload"]["position_after"] + 5.0)
    with pytest.raises(LedgerError):
        build_cycle_ledger_from_orders(events, initial_capital=100_000.0, qty_step=qs)


def test_from_orders_cash_mismatch_fails_closed():
    events, qs = _broker_orders([(1.0, 1000, 100.0), (0.0, 2000, 110.0)])
    events[0] = _tamper_payload(events[0], cash_after=events[0]["payload"]["cash_after"] + 1.0)
    with pytest.raises(LedgerError):
        build_cycle_ledger_from_orders(events, initial_capital=100_000.0, qty_step=qs)


def test_from_orders_equity_mismatch_fails_closed():
    events, qs = _broker_orders([(1.0, 1000, 100.0), (0.0, 2000, 110.0)])
    events[0] = _tamper_payload(events[0], equity_after=events[0]["payload"]["equity_after"] + 1.0)
    with pytest.raises(LedgerError):
        build_cycle_ledger_from_orders(events, initial_capital=100_000.0, qty_step=qs)


def test_from_orders_bad_status_and_zerofill_consistency():
    events, qs = _broker_orders([(1.0, 1000, 100.0)])
    with pytest.raises(LedgerError):
        build_cycle_ledger_from_orders([_tamper_payload(events[0], status="weird")],
                                       initial_capital=100_000.0, qty_step=qs)
    # rejected must have zero fill
    with pytest.raises(LedgerError):
        build_cycle_ledger_from_orders([_tamper_payload(events[0], status="rejected")],
                                       initial_capital=100_000.0, qty_step=qs)


def test_from_orders_nonmonotonic_seq_fails():
    events, qs = _broker_orders([(1.0, 1000, 100.0), (0.0, 2000, 110.0)])
    events[1] = dict(events[1], seq=0)  # duplicate/non-monotonic seq
    with pytest.raises(LedgerError):
        build_cycle_ledger_from_orders(events, initial_capital=100_000.0, qty_step=qs)


# ---- item 7: reconcile helper hardening ------------------------------------ #
def test_reconcile_against_snapshot_fails_closed_on_malformed():
    fills = [_fid(10.0, 100.0, 1.0, 1000, "a"), _fid(-10.0, 110.0, 1.0, 2000, "b")]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    with pytest.raises(LedgerError):
        reconcile_against_snapshot(led, "not-a-dict")
    with pytest.raises(LedgerError):
        reconcile_against_snapshot(led, {"cash": 1.0})  # missing fields
    with pytest.raises(LedgerError):
        reconcile_against_snapshot(led, {"cash": True, "position_units": 0.0,
                                         "realized_pnl": 0.0, "fees_paid": 0.0})  # bool
    with pytest.raises(LedgerError):
        reconcile_against_snapshot(led, {"cash": float("nan"), "position_units": 0.0,
                                         "realized_pnl": 0.0, "fees_paid": 0.0})
    with pytest.raises(LedgerError):
        reconcile_against_snapshot(led, {"cash": 0.0, "position_units": 0.0, "realized_pnl": 0.0,
                                         "fees_paid": 0.0, "mark_price": 110.0, "equity": 0.0},
                                   cash_tol=-1.0)  # bad tolerance


# ---- item 8: immutability ---------------------------------------------------#
def test_input_fills_not_mutated():
    import copy
    fills = [_fid(10.0, 100.0, 1.0, 1000, "a"), _fid(-10.0, 110.0, 1.0, 2000, "b")]
    before = copy.deepcopy(fills)
    build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    assert fills == before


def test_runs_are_independent():
    f1 = [_fid(10.0, 100.0, 1.0, 1000, "a"), _fid(-10.0, 110.0, 1.0, 2000, "b")]
    f2 = [_fid(3.0, 50.0, 0.3, 5000, "x"), _fid(-3.0, 55.0, 0.3, 6000, "y")]
    a = build_cycle_ledger(f1, initial_capital=100_000.0, qty_step=_S)
    b = build_cycle_ledger(f2, initial_capital=100_000.0, qty_step=_S)
    assert a["cycles"][0]["fill_ids"] == ["a", "b"]
    assert b["cycles"][0]["fill_ids"] == ["x", "y"]


# ---- H-025 via the PRODUCTION constructor: reconcile every order boundary --- #
def _h025_orders(frame, tmp_path, risk=None):
    fills, res, qs = _h025_fills(frame, tmp_path, risk=risk)
    # feed the ACTUAL verified journal order events (full wrappers: event_type/event_id/seq/payload)
    orders = []
    with open(str(tmp_path) + "/journal.jsonl") as f:
        for line in f:
            e = json.loads(line)
            if e["event_type"] == "order":
                orders.append(e)
    return orders, res, qs


def test_h025_uptrend_reconciles_all_order_boundaries(tmp_path):
    frame = _mkframe([100 + 3 * i for i in range(30)])
    orders, res, qs = _h025_orders(frame, tmp_path)
    led = build_cycle_ledger_from_orders(orders, initial_capital=100_000.0, qty_step=qs)
    assert res["lifecycle_status"] == "COMPLETED"
    assert led["reconciled"] is True
    assert len(led["cycles"]) == 0
    ok, detail = validate_fill_partition(led)
    assert ok, detail


def test_h025_riskstop_reconciles_one_cycle(tmp_path):
    from app.paper.session.risk import freeze_risk_limits
    frame = _mkframe([100 + 3 * i for i in range(12)] + [40.0, 41.0, 42.0])
    orders, res, qs = _h025_orders(frame, tmp_path, risk=freeze_risk_limits(max_drawdown=0.10))
    led = build_cycle_ledger_from_orders(orders, initial_capital=100_000.0, qty_step=qs)
    assert res["lifecycle_status"] == "RISK_STOPPED"
    assert led["reconciled"] is True
    assert len(led["cycles"]) == 1


# =========================================================================== #
# WP2B-0A HARDENING R2 — equity reconcile, journal-event identity, dust,       #
# exact-zero/timestamp                                                         #
# =========================================================================== #
def _ev(seq, payload, *, etype="order", eid=None):
    return {"event_type": etype, "event_id": eid if eid is not None else f"order:{payload['exec_open_time']}",
            "seq": seq, "payload": payload}


def _snap_from(led, mark):
    r = led["reconstructed"]
    return {"cash": r["cash"], "position_units": r["position_units"], "realized_pnl": r["realized_pnl"],
            "fees_paid": r["fees"], "mark_price": float(mark), "equity": r["cash"] + r["position_units"] * mark}


# ---- item 1: snapshot equity reconciliation -------------------------------- #
def test_reconcile_verifies_equity_with_mark_price():
    fills = [_fid(10.0, 100.0, 1.0, 1000, "a"), _fid(-4.0, 110.0, 0.4, 2000, "b")]  # still holding 6
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    good = _snap_from(led, 110.0)
    checks, ok = reconcile_against_snapshot(led, good)
    assert ok and checks["equity"] is True
    # equity mismatch must fail EVEN THOUGH cash and position individually match
    bad = dict(good, equity=good["equity"] + 10.0)
    checks, ok = reconcile_against_snapshot(led, bad)
    assert checks["cash"] is True and checks["position"] is True
    assert checks["equity"] is False and ok is False


def test_reconcile_requires_mark_and_equity_fields():
    fills = [_fid(10.0, 100.0, 1.0, 1000, "a"), _fid(-10.0, 110.0, 1.0, 2000, "b")]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    r = led["reconstructed"]
    base = {"cash": r["cash"], "position_units": r["position_units"],
            "realized_pnl": r["realized_pnl"], "fees_paid": r["fees"]}
    with pytest.raises(LedgerError):
        reconcile_against_snapshot(led, base)                                   # no mark_price/equity
    with pytest.raises(LedgerError):
        reconcile_against_snapshot(led, dict(base, mark_price=True, equity=0.0))  # bool mark
    with pytest.raises(LedgerError):
        reconcile_against_snapshot(led, dict(base, mark_price=110.0, equity=float("nan")))


# ---- item 2: real journal-event identity ----------------------------------- #
def test_from_events_requires_order_event_type():
    events, qs = _broker_orders([(1.0, 1000, 100.0), (0.0, 2000, 110.0)])
    events[0] = dict(events[0], event_type="bar_ingested")
    with pytest.raises(LedgerError):
        build_cycle_ledger_from_orders(events, initial_capital=100_000.0, qty_step=qs)


def test_from_events_requires_nonempty_string_event_id():
    events, qs = _broker_orders([(1.0, 1000, 100.0)])
    for bad in ("", 123, None):
        with pytest.raises(LedgerError):
            build_cycle_ledger_from_orders([dict(events[0], event_id=bad)],
                                           initial_capital=100_000.0, qty_step=qs)


def test_from_events_seq_must_be_nonneg_int_not_bool():
    events, qs = _broker_orders([(1.0, 1000, 100.0)])
    for bad in (True, -1, 1.5, "0"):
        with pytest.raises(LedgerError):
            build_cycle_ledger_from_orders([dict(events[0], seq=bad)],
                                           initial_capital=100_000.0, qty_step=qs)


def test_from_events_rejects_duplicate_event_id():
    events, qs = _broker_orders([(1.0, 1000, 100.0), (0.0, 2000, 110.0)])
    events[1] = dict(events[1], event_id=events[0]["event_id"])
    with pytest.raises(LedgerError):
        build_cycle_ledger_from_orders(events, initial_capital=100_000.0, qty_step=qs)


def test_from_events_rejects_decreasing_exec_open_time():
    events, qs = _broker_orders([(1.0, 1000, 100.0), (0.0, 2000, 110.0)])
    events[1] = _tamper_payload(events[1], exec_open_time=500)  # goes backwards
    with pytest.raises(LedgerError):
        build_cycle_ledger_from_orders(events, initial_capital=100_000.0, qty_step=qs)


def test_from_events_unhashable_identity_raises_ledgererror_not_typeerror():
    events, qs = _broker_orders([(1.0, 1000, 100.0)])
    with pytest.raises(LedgerError):
        build_cycle_ledger_from_orders([dict(events[0], event_id=["x"])],
                                       initial_capital=100_000.0, qty_step=qs)


# ---- item 3: inherited dust evidence truthful & complete ------------------- #
def test_inherited_dust_disclosed_when_raw_returns_to_zero():
    resid = 0.9e-6
    fills = [
        _fid(10.0, 100.0, 1.0, 1000, "a"),
        _fid(-(10.0 - resid), 120.0, 1.0, 2000, "b"),   # close A, winning, leaves dust
        _fid(-resid, 121.0, 0.02, 3000, "c"),           # dust reduction to EXACT raw zero, net+fee
        _fid(5.0, 95.0, 0.5, 4000, "d"),                # open B
        _fid(-5.0, 99.0, 0.5, 5000, "e"),               # close B
    ]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    b = led["cycles"][1]
    assert b["includes_inherited_dust"] is True                 # disclosed even though raw units are 0
    ci = b["residual_carried_in"]
    assert ci is not None and "c" in ci["transferred_fill_ids"]
    assert ci["net"] != 0.0 and "fees" in ci
    ok, detail = validate_fill_partition(led)
    assert ok, detail
    agg = sum(c["realized_net"] for c in led["cycles"]) + led["dust_carry"]["net"]
    agg += (led["open_cycle"]["realized_net"] if led["open_cycle"] else 0.0)
    assert math.isclose(agg, led["sum_fill_net"], abs_tol=1e-9)


def test_residual_carry_exposes_fee_field():
    resid = 0.9e-6
    fills = [_fid(10.0, 100.0, 1.0, 1000, "a"), _fid(-(10.0 - resid), 90.0, 1.0, 2000, "b"),
             _fid(5.0, 95.0, 0.5, 3000, "d"), _fid(-5.0 - resid, 99.0, 0.5, 4000, "e")]
    led = build_cycle_ledger(fills, initial_capital=100_000.0, qty_step=_S)
    assert "fees" in led["cycles"][0]["residual_carried_out"]
    assert "fees" in led["cycles"][1]["residual_carried_in"]


# ---- item 4: exact-zero and timestamp validation --------------------------- #
def test_exact_zero_fill_with_nonzero_fee_fails_both_constructors():
    with pytest.raises(LedgerError):
        build_cycle_ledger([{"filled_units": 0.0, "fill_price": 100.0, "fee": 0.5,
                             "exec_open_time": 1, "fill_id": "z"}], initial_capital=1.0, qty_step=_S)
    events, qs = _broker_orders([(1.0, 1000, 100.0)])
    zpayload = {"filled_units": 0.0, "fill_price": 100.0, "fee": 0.5, "exec_open_time": 1,
                "position_after": 0.0, "cash_after": 100_000.0, "equity_after": 100_000.0,
                "status": "rejected"}
    with pytest.raises(LedgerError):
        build_cycle_ledger_from_orders([_ev(0, zpayload)], initial_capital=100_000.0, qty_step=qs)


def test_negative_exec_open_time_fails_both_constructors():
    with pytest.raises(LedgerError):
        build_cycle_ledger([_fid(1.0, 100.0, 0.0, -5, "a")], initial_capital=1.0, qty_step=_S)
    events, qs = _broker_orders([(1.0, 1000, 100.0)])
    bad = _tamper_payload(events[0], exec_open_time=-1)
    with pytest.raises(LedgerError):
        build_cycle_ledger_from_orders([bad], initial_capital=100_000.0, qty_step=qs)
