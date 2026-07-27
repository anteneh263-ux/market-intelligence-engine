"""WP2B-0A tests — pure metrics-v2 behavioural metric functions.

Written test-first. Segment-local, deterministic, read-only, fail-closed, bool-rejecting,
finite-validated. Covers accepted R2/R3 formulas and R4 boundary semantics.
"""
from __future__ import annotations

import math

import pytest

from app.forward.metrics_v2 import (
    INVALID,
    NOT_AVAILABLE,
    OK,
    average_holding_period,
    completed_trade_count_metric,
    exposure_time,
    net_return,
    segment_local_max_drawdown,
    turnover,
    win_rate,
)


# --------------------------------------------------------------------------- #
# net_return                                                                   #
# --------------------------------------------------------------------------- #
def test_net_return_basic():
    r = net_return(100_000.0, 103_380.07)
    assert r["status"] == OK
    assert math.isclose(r["value"], 0.0338007, rel_tol=1e-9)


def test_net_return_uses_segment_e_start_not_session_capital():
    # a second segment whose E_start differs from the session initial capital
    r = net_return(120_000.0, 126_000.0)
    assert r["status"] == OK and math.isclose(r["value"], 0.05, rel_tol=1e-12)


def test_net_return_estart_nonpositive_not_available():
    assert net_return(0.0, 10.0)["status"] == NOT_AVAILABLE
    assert net_return(-5.0, 10.0)["status"] == NOT_AVAILABLE


def test_net_return_nonfinite_invalid():
    assert net_return(float("nan"), 1.0)["status"] == INVALID
    assert net_return(1.0, float("inf"))["status"] == INVALID


# --------------------------------------------------------------------------- #
# segment-local max drawdown                                                   #
# --------------------------------------------------------------------------- #
def test_segment_local_drawdown_differs_from_cumulative():
    # session peak was 200k earlier; the segment starts at E_start=100k and only rises to 110k
    # then dips to 99k. Segment-local peak resets at E_start=100k, so max_dd = (100k-99k)/100k = 0.01
    e_start = 100_000.0
    path = [100_000.0, 110_000.0, 99_000.0, 105_000.0]
    r = segment_local_max_drawdown(path, e_start)
    assert r["status"] == OK
    assert math.isclose(r["value"], (110_000.0 - 99_000.0) / 110_000.0, rel_tol=1e-9)
    # a cumulative drawdown that used a 200k pre-segment peak would be ~0.505 — must NOT be that
    assert r["value"] < 0.2


def test_segment_local_drawdown_flat_is_zero():
    r = segment_local_max_drawdown([100.0, 100.0, 100.0], 100.0)
    assert r["status"] == OK and r["value"] == 0.0


def test_segment_local_drawdown_bad_inputs():
    assert segment_local_max_drawdown([], 100.0)["status"] == NOT_AVAILABLE
    assert segment_local_max_drawdown([100.0, float("nan")], 100.0)["status"] == INVALID
    assert segment_local_max_drawdown([100.0], 0.0)["status"] == NOT_AVAILABLE


# --------------------------------------------------------------------------- #
# completed_trade_count_metric                                                 #
# --------------------------------------------------------------------------- #
def test_completed_trade_count_metric():
    cycles = [{"status": "completed"}, {"status": "completed"}, {"status": "open"}]
    r = completed_trade_count_metric(cycles)
    assert r["status"] == OK and r["value"] == 2


def test_completed_trade_count_empty():
    assert completed_trade_count_metric([])["value"] == 0
    assert completed_trade_count_metric("nope")["status"] == INVALID


# --------------------------------------------------------------------------- #
# turnover — denominator is E_start                                            #
# --------------------------------------------------------------------------- #
def test_turnover_denominator_is_estart():
    fills = [{"filled_units": 10.0, "fill_price": 100.0}, {"filled_units": -10.0, "fill_price": 110.0}]
    r = turnover(fills, 100_000.0)
    assert r["status"] == OK
    assert math.isclose(r["value"], (10 * 100 + 10 * 110) / 100_000.0, rel_tol=1e-12)


def test_turnover_bad_inputs():
    assert turnover([{"filled_units": 1.0, "fill_price": float("nan")}], 100.0)["status"] == INVALID
    assert turnover([], 0.0)["status"] == NOT_AVAILABLE


# --------------------------------------------------------------------------- #
# exposure_time                                                                #
# --------------------------------------------------------------------------- #
def test_exposure_time_fraction():
    flags = [True, True, False, False, True]  # 3 exposed of 5 bars
    r = exposure_time(flags, 5)
    assert r["status"] == OK and math.isclose(r["value"], 0.6, rel_tol=1e-12)


def test_exposure_time_bad_inputs():
    assert exposure_time([True], 0)["status"] == NOT_AVAILABLE
    assert exposure_time("nope", 3)["status"] == INVALID


# --------------------------------------------------------------------------- #
# win_rate                                                                     #
# --------------------------------------------------------------------------- #
def test_win_rate_basic():
    cycles = [{"status": "completed", "realized_net": 50.0},
              {"status": "completed", "realized_net": -20.0},
              {"status": "completed", "realized_net": 10.0},
              {"status": "open", "realized_net": 999.0}]  # open excluded
    r = win_rate(cycles)
    assert r["status"] == OK and math.isclose(r["value"], 2 / 3, rel_tol=1e-9)


def test_win_rate_no_completed_cycles_not_available():
    assert win_rate([{"status": "open", "realized_net": 1.0}])["status"] == NOT_AVAILABLE
    assert win_rate([])["status"] == NOT_AVAILABLE


def test_win_rate_scratch_near_zero_is_not_a_win():
    cycles = [{"status": "completed", "realized_net": 1e-12},   # within eps -> scratch (not a win)
              {"status": "completed", "realized_net": 5.0}]
    r = win_rate(cycles, eps_pnl=1e-9)
    assert r["status"] == OK and math.isclose(r["value"], 0.5, rel_tol=1e-9)  # 1 win of 2


def test_win_rate_nonfinite_invalid():
    assert win_rate([{"status": "completed", "realized_net": float("nan")}])["status"] == INVALID


# --------------------------------------------------------------------------- #
# average_holding_period — executable-bar indices, not wall-clock ms           #
# --------------------------------------------------------------------------- #
def test_average_holding_period_spy_friday_to_monday_is_one_bar():
    # SPY: Fri and Mon are ADJACENT executable bars (weekend has no bars) -> 1, not 3 days
    fri, mon = 1_700_000_000_000, 1_700_000_000_000 + 3 * 86_400_000
    bar_index_of = {fri: 10, mon: 11}   # consecutive executable-bar indices
    cycles = [{"status": "completed", "entry_exec_open_time": fri, "exit_exec_open_time": mon}]
    r = average_holding_period(cycles, bar_index_of)
    assert r["status"] == OK and r["value"] == 1.0


def test_average_holding_period_btc_adjacent_daily_is_one_bar():
    d0, d1 = 1_600_000_000_000, 1_600_000_000_000 + 86_400_000
    bar_index_of = {d0: 0, d1: 1}
    cycles = [{"status": "completed", "entry_exec_open_time": d0, "exit_exec_open_time": d1}]
    assert average_holding_period(cycles, bar_index_of)["value"] == 1.0


def test_average_holding_period_mean_over_cycles():
    idx = {0: 0, 1: 1, 2: 2, 5: 5}
    cycles = [{"status": "completed", "entry_exec_open_time": 0, "exit_exec_open_time": 1},
              {"status": "completed", "entry_exec_open_time": 2, "exit_exec_open_time": 5}]
    r = average_holding_period(cycles, idx)  # (1 + 3)/2 = 2
    assert r["status"] == OK and math.isclose(r["value"], 2.0, rel_tol=1e-12)


def test_average_holding_period_no_completed_not_available():
    assert average_holding_period([], {})["status"] == NOT_AVAILABLE


def test_average_holding_period_missing_index_invalid():
    cycles = [{"status": "completed", "entry_exec_open_time": 0, "exit_exec_open_time": 99}]
    assert average_holding_period(cycles, {0: 0})["status"] == INVALID


# --------------------------------------------------------------------------- #
# determinism                                                                  #
# --------------------------------------------------------------------------- #
def test_metrics_deterministic():
    fills = [{"filled_units": 10.0, "fill_price": 100.0}, {"filled_units": -10.0, "fill_price": 110.0}]
    assert turnover(fills, 100_000.0) == turnover(fills, 100_000.0)
    assert net_return(100.0, 110.0) == net_return(100.0, 110.0)


# =========================================================================== #
# WP2B-0A HARDENING R1 — metrics-v2 validation (items 6A-6F)                   #
# =========================================================================== #
# 6A completed_trade_count_metric
def test_completed_trade_count_rejects_malformed_cycle():
    assert completed_trade_count_metric([{"status": "completed"}, "not-a-dict"])["status"] == INVALID


def test_completed_trade_count_rejects_unknown_status():
    assert completed_trade_count_metric([{"status": "weird"}])["status"] == INVALID


# 6B exposure_time
def test_exposure_time_requires_len_equals_n_bars():
    assert exposure_time([True, False], 5)["status"] == INVALID       # len mismatch
    r = exposure_time([True, True, False], 3)
    assert r["status"] == OK and r["value"] <= 1.0


# 6C win_rate
@pytest.mark.parametrize("bad", [True, float("nan"), -1e-9, float("inf")])
def test_win_rate_rejects_bad_eps(bad):
    assert win_rate([{"status": "completed", "realized_net": 1.0}], eps_pnl=bad)["status"] == INVALID


def test_win_rate_rejects_malformed_or_unknown_status_cycle():
    assert win_rate([{"status": "completed", "realized_net": 1.0}, "x"])["status"] == INVALID
    assert win_rate([{"status": "weird", "realized_net": 1.0}])["status"] == INVALID


# 6D turnover
def test_turnover_rejects_nonpositive_price():
    assert turnover([{"filled_units": 1.0, "fill_price": 0.0}], 100.0)["status"] == INVALID
    assert turnover([{"filled_units": 1.0, "fill_price": -5.0}], 100.0)["status"] == INVALID


def test_turnover_rejects_bool_values():
    assert turnover([{"filled_units": True, "fill_price": 100.0}], 100.0)["status"] == INVALID


# 6E average_holding_period
def test_average_holding_period_rejects_negative_or_bool_index():
    cyc = [{"status": "completed", "entry_exec_open_time": 0, "exit_exec_open_time": 1}]
    assert average_holding_period(cyc, {0: -1, 1: 2})["status"] == INVALID       # negative index
    assert average_holding_period(cyc, {0: 0, 1: True})["status"] == INVALID     # bool index


def test_average_holding_period_requires_exit_after_entry():
    cyc = [{"status": "completed", "entry_exec_open_time": 5, "exit_exec_open_time": 5}]
    assert average_holding_period(cyc, {5: 7})["status"] == INVALID              # exit == entry


def test_average_holding_period_rejects_malformed_or_open_masquerading():
    assert average_holding_period([{"status": "completed"}], {})["status"] == INVALID   # missing fields
    assert average_holding_period(["x"], {})["status"] == INVALID


# 6F segment_local_max_drawdown
def test_segment_local_drawdown_rejects_malformed_container_and_bool():
    assert segment_local_max_drawdown("not-a-list", 100.0)["status"] == INVALID
    assert segment_local_max_drawdown([100.0, True, 100.0], 100.0)["status"] == INVALID  # bool in path


# immutability of metric inputs
def test_metric_inputs_not_mutated():
    import copy
    fills = [{"filled_units": 10.0, "fill_price": 100.0}]
    before = copy.deepcopy(fills)
    turnover(fills, 100.0)
    assert fills == before
    cyc = [{"status": "completed", "realized_net": 1.0}]
    b2 = copy.deepcopy(cyc)
    win_rate(cyc)
    assert cyc == b2
