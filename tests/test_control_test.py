"""Tests for target-control-test-v1 (docs/specs/SPEC-target-control-calibrated-v1.md)."""
import inspect
import math

import numpy as np
import pandas as pd
import pytest

from app.research_safety import control_test as CT
from app.research_safety import control_test_calibration as K
from app.research_safety import promotion_gate, target_control


def _pair(rng, n=1500, a=3, edge=0.0, rho=0.5):
    z = rng.standard_normal((n, a))
    u = rng.standard_normal((n, a))
    ctrl = 0.01 * z
    cand = 0.01 * (rho * z + math.sqrt(1 - rho ** 2) * u) + edge * 0.01 / 252
    return cand, ctrl


def test_locked_parameters_match_spec():
    assert (CT.VERSION, CT.BLOCK_LENGTH, CT.N_BOOT, CT.ALPHA, CT.PERIODS_PER_YEAR) == \
        ("target-control-test-v1", 10, 2000, 0.05, 252.0)


def test_delta_sr_matches_manual_computation():
    rng = np.random.default_rng(0)
    c, k = _pair(rng)
    point, per = CT.delta_sr(c, k)
    sr = lambda x: x.mean() / x.std(ddof=1) * math.sqrt(252)
    manual = [sr(c[:, j]) - sr(k[:, j]) for j in range(c.shape[1])]
    assert np.allclose(per, manual) and point == pytest.approx(np.mean(manual))


def test_decision_rule_on_constructed_series():
    rng = np.random.default_rng(1)
    big = 8.0                                               # annual Sharpe edge far beyond noise
    c, k = _pair(rng, edge=big)
    assert CT.paired_test(c, k, seed=1)["verdict"] == "candidate_wins"
    assert CT.paired_test(k, c, seed=1)["verdict"] == "control_wins"
    same = CT.paired_test(k, k.copy(), seed=1)
    assert same["verdict"] == "inconclusive" and same["interval"] == [0.0, 0.0]


def test_seed_determinism():
    c, k = _pair(np.random.default_rng(2))
    assert CT.paired_test(c, k, seed=7) == CT.paired_test(c, k, seed=7)


def test_circular_block_indices_are_contiguous_blocks():
    idx = CT.circular_block_indices(25, 10, 4, np.random.default_rng(3))
    assert idx.shape == (4, 25) and idx.min() >= 0 and idx.max() < 25
    first_block = idx[0, :10]
    assert all((first_block[i + 1] - first_block[i]) % 25 == 1 for i in range(9))


def test_common_calendar_compounds_off_calendar_days():
    mon_fri = pd.bdate_range("2026-01-05", periods=6)            # Mon 5 .. Mon 12
    all_days = pd.date_range("2026-01-05", "2026-01-13")          # includes the weekend and Tue 13
    eq = (mon_fri, np.full(6, 0.01), np.zeros(6))
    cr = (all_days, np.full(len(all_days), 0.01), np.zeros(len(all_days)))
    dates, C, Kk, assets = CT.align_common_calendar({"EQ": eq, "CR": cr})
    assert list(dates) == list(mon_fri) and assets == ["EQ", "CR"]
    # Sat 10 + Sun 11 + Mon 12 compound into Monday 12; Tue 13 is after the last common date: dropped.
    assert C[-1, 1] == pytest.approx(1.01 ** 3 - 1)
    assert C[0, 1] == pytest.approx(0.01) and np.allclose(C[:, 0], 0.01)


def test_legacy_compare_is_unchanged_and_gate_default_is_legacy():
    sig = inspect.signature(target_control.compare)
    assert sig.parameters["sharpe_tol"].default == 0.05
    gate_sig = inspect.signature(promotion_gate.evaluate_promotion)
    assert gate_sig.parameters["control_test"].default == "legacy"


def test_calibration_layer_a_is_deterministic_and_shaped():
    a1 = K.layer_a_series(np.random.default_rng([11, 1]), 0.5, 0.5)
    a2 = K.layer_a_series(np.random.default_rng([11, 1]), 0.5, 0.5)
    assert a1[0].shape == (K.T_LAYER_A, 5) and np.array_equal(a1[0], a2[0])


def test_calibration_null_has_identical_marginals():
    c, k = K.layer_a_series(np.random.default_rng(5), 0.0, 0.5, n=200_000)
    assert abs(CT.delta_sr(c, k)[0]) < 0.06


def test_wilson_interval_known_value():
    lo, hi = K.wilson(100, 2000)
    assert lo == pytest.approx(0.0413, abs=2e-4) and hi == pytest.approx(0.0605, abs=2e-4)


def test_acceptance_rule():
    def sc(d, r, rate, hi):
        return {"delta": d, "rho": r, "approval_rate": rate, "wilson95": [0.0, hi]}
    a = {"scenarios": [sc(0.0, r, 0.04, 0.05) for r in K.RHOS] + [sc(0.5, 0.5, 0.82, 0.9)]}
    assert K.acceptance(a, None)["accepted"]
    a["scenarios"][1] = sc(0.0, 0.5, 0.055, 0.065)
    assert not K.acceptance(a, None)["accepted"]


def test_final_stage_requires_commit(capsys):
    with pytest.raises(SystemExit):
        K.main(["--stage", "final", "--layer", "A"])


def test_calendar_rejects_intraday_or_broken_inputs():
    d = pd.to_datetime([0, 3_600_000, 86_400_000], unit="ms")       # two bars on the same day
    with pytest.raises(ValueError, match="one bar per calendar day"):
        CT.align_common_calendar({"X": (d, np.zeros(3), np.zeros(3))})
    d2 = pd.date_range("2026-01-05", periods=3)
    with pytest.raises(ValueError, match="above -100"):
        CT.align_common_calendar({"X": (d2, np.array([0.0, -1.0, 0.0]), np.zeros(3))})


def test_synthetic_frames_use_daily_millisecond_timestamps():
    fr = K.synthetic_frames(np.random.default_rng(0), 50)
    t = pd.to_datetime(fr["AAPL"]["open_time"].to_numpy(), unit="ms")
    assert t[0] == pd.Timestamp("2000-01-03") and t.is_unique and (t.normalize() == t).all()
