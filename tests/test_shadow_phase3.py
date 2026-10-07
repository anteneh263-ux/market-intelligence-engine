"""Phase-3 shadow-forward tests: frozen regimes, ordered bootstrap reference ranges and
behavioural-consistency classification.

All fixtures are deterministic (fixed synthetic data, fixed seeds, fixed clocks). No network,
no files, no lifecycle state and no evidence directories are touched. A dedicated side-effect
test snapshots the filesystem and deep-copies every input to prove the evaluators are pure.
"""
from __future__ import annotations

import copy
import os

import numpy as np
import pandas as pd
import pytest

from app.forward.manifest import canonical_json, sha256_text
from app.forward.shadow.regime import (
    LABEL_NA,
    RANGE_HIGH_VOL,
    RANGE_LOW_VOL,
    TREND_HIGH_VOL,
    TREND_LOW_VOL,
    DEFAULT_REGIME_POLICY,
    align_labels_to_execution,
    classify_regimes,
    freeze_regime_policy,
    regime_policy_hash,
    validate_regime_policy,
)
from app.forward.shadow.ranges import (
    EXTEND as R_EXTEND,
    build_reference_ranges,
    freeze_range_policy,
    range_policy_hash,
    validate_range_policy,
)
from app.forward.shadow.consistency import (
    CONSISTENT,
    DRIFT,
    EXTEND,
    FAILED,
    INVALIDATED,
    PRECEDENCE_ORDER,
    consistency_policy_hash,
    evaluate_consistency,
    freeze_consistency_policy,
)
from app.forward.shadow.dataquality import INVALID, NOT_AVAILABLE, PASS

_T0 = 946_684_800_000  # 2000-01-01T00:00:00Z epoch-ms; fixed synthetic clock
_MIN = 60_000


# --------------------------------------------------------------------------- #
# regime fixtures                                                             #
# --------------------------------------------------------------------------- #
def _ohlc(close: np.ndarray, spread: np.ndarray) -> pd.DataFrame:
    n = len(close)
    close = np.asarray(close, dtype=float)
    spread = np.asarray(spread, dtype=float)
    ts = _T0 + np.arange(n) * _MIN
    return pd.DataFrame({
        "open_time": ts,
        "close": close,
        "high": close + spread / 2.0,
        "low": close - spread / 2.0,
    })


def _osc_spread(n: int) -> np.ndarray:
    # oscillates ~0.05..0.95 so vol rises above and falls below its trailing median
    return 0.5 + 0.45 * np.sin(np.arange(n) / 13.0)


def _trending(n: int = 400) -> pd.DataFrame:
    close = 100.0 + np.cumsum(np.full(n, 0.15)) + np.sin(np.arange(n) / 7.0) * 0.1
    return _ohlc(close, _osc_spread(n))


def _ranging(n: int = 400) -> pd.DataFrame:
    close = 100.0 + np.sin(np.arange(n) / 6.0) * 1.5
    return _ohlc(close, _osc_spread(n))


# --------------------------------------------------------------------------- #
# range / consistency fixtures                                                #
# --------------------------------------------------------------------------- #
def _segment(i: int, *, base: float = 0.010, drop=None, top=None) -> dict:
    m = {
        "return": float(base + 0.0009 * np.sin(i)),
        "drawdown": float(-0.05 - 0.002 * (i % 3)),
        "trade_count": float(30 + (i % 5)),
        "turnover": float(1.2 + 0.05 * (i % 4)),
        "exposure_time": float(0.6 + 0.01 * (i % 3)),
        "win_rate": float(0.52 + 0.003 * (i % 4)),
        "avg_holding_period": float(5 + (i % 3)),
        "long_short_balance": float(0.5),
    }
    if drop:
        for d in drop:
            m.pop(d, None)
    rec = {"segment_index": i, "metrics": m}
    if top:
        rec.update(top)
    return rec


def _segments(n: int, *, base: float = 0.010, drop=None):
    return [_segment(i, base=base, drop=drop) for i in range(n)]


def _rpol(**over):
    kw = dict(seed=12345, block_length=3, n_resamples=500, min_unique_segments=6)
    kw.update(over)
    return freeze_range_policy(**kw)


def _reference(base: float = 0.010):
    return build_reference_ranges(_segments(24, base=base), policy=_rpol())


def _cpol(**over):
    kw = dict(checkpoints=(30, 60, 90), min_observations=20, min_trades=30,
              persistence_min_periods=2, multi_metric_min=2)
    kw.update(over)
    return freeze_consistency_policy(**kw)


def _required(ref):
    return list(ref["policy"]["required_metrics"])


def _mid(ref):
    g = ref["groups"]["__ALL__"]["metrics"]
    return {k: (g[k]["lower"] + g[k]["upper"]) / 2.0 for k in _required(ref)}


def _observed(ref, values, *, cpol=None, trades=60, obs=200, excursions=None, breach=False,
              include=("range", "range_policy", "consistency"), absolute_risk=True,
              comparability=True, **over):
    cph = consistency_policy_hash(cpol or _cpol())
    metrics = {}
    for k, v in values.items():
        cell = {"observed": v}
        if excursions and k in excursions:
            cell["excursion_periods"] = excursions[k]
            cell["n_periods"] = excursions[k] + 3
        metrics[k] = cell
    o = {"trade_count": trades, "observation_count": obs, "metrics": metrics}
    if "range" in include:
        o["expected_range_hash"] = ref["range_hash"]
    if "range_policy" in include:
        o["expected_range_policy_hash"] = ref["policy_hash"]
    if "consistency" in include:
        o["expected_consistency_policy_hash"] = cph
    if absolute_risk:
        o["absolute_risk"] = {"breached": breach, "detail": "max drawdown limit"}
    if comparability:
        # by default the observation declares the SAME basis it was built on
        o["comparability"] = copy.deepcopy(ref["comparability_basis"])
    o.update(over)
    return o


def _rehash_reference(ref):
    """Recompute policy_hash and range_hash so a tampered reference has NO simple hash mismatch
    (mirrors how build_reference_ranges computes them)."""
    ref = copy.deepcopy(ref)
    ref["policy_hash"] = range_policy_hash(ref["policy"])
    without = {k: v for k, v in ref.items() if k != "range_hash"}
    ref["range_hash"] = sha256_text(canonical_json(without))
    return ref


# =========================================================================== #
# REGIME                                                                      #
# =========================================================================== #
def test_regime_policy_freeze_is_deterministic_and_hashable():
    p1, p2 = freeze_regime_policy(), freeze_regime_policy()
    assert p1 == p2 and regime_policy_hash(p1) == regime_policy_hash(p2)
    assert validate_regime_policy(p1) == (True, "")
    assert set(p1["required_columns"]) == {"open_time", "close", "high", "low"}


def test_regime_policy_validation_rejects_malformed():
    good = freeze_regime_policy()
    bad = dict(good); bad["version"] = "regime-v9"
    assert classify_regimes(_trending(200), bad)["classification"] == INVALID
    bad2 = dict(good); bad2["thresholds"] = {"trend_threshold": -1.0, "vol_high_multiple": 1.0}
    assert classify_regimes(_trending(200), bad2)["classification"] == INVALID
    bad3 = dict(good); bad3["lookback_windows"] = dict(good["lookback_windows"], fast_window=50, slow_window=50)
    assert classify_regimes(_trending(200), bad3)["classification"] == INVALID
    bad4 = dict(good); bad4.pop("timestamp_assumptions")
    assert classify_regimes(_trending(200), bad4)["classification"] == INVALID
    assert classify_regimes(_trending(200), ["nope"])["classification"] == INVALID
    assert validate_regime_policy({"method": "x"})[0] is False


def test_regime_warmup_rows_are_not_available():
    res = classify_regimes(_trending(400))
    warm = res["warmup"]
    assert res["classification"] == PASS
    assert all(lbl == LABEL_NA for lbl in res["labels"][:warm])
    assert res["labels"][warm] != LABEL_NA


def test_regime_produces_trend_high_vol():
    assert classify_regimes(_trending(400))["counts"].get(TREND_HIGH_VOL, 0) > 0


def test_regime_produces_trend_low_vol():
    assert classify_regimes(_trending(400))["counts"].get(TREND_LOW_VOL, 0) > 0


def test_regime_produces_range_high_vol():
    assert classify_regimes(_ranging(400))["counts"].get(RANGE_HIGH_VOL, 0) > 0


def test_regime_produces_range_low_vol():
    assert classify_regimes(_ranging(400))["counts"].get(RANGE_LOW_VOL, 0) > 0


def test_regime_labels_are_causal_no_lookahead():
    full_df = _trending(400)
    full = classify_regimes(full_df)
    truncated = classify_regimes(full_df.iloc[:300].copy())
    assert full["labels"][:300] == truncated["labels"]


def test_regime_non_dataframe_and_missing_column_invalid():
    assert classify_regimes([1, 2, 3])["classification"] == INVALID
    assert classify_regimes(_trending(200).drop(columns=["high"]))["classification"] == INVALID


def test_regime_non_finite_close_invalid_and_frame_unmutated():
    df = _trending(200)
    snapshot = df.copy(deep=True)
    df.at[10, "close"] = np.inf
    assert classify_regimes(df)["classification"] == INVALID
    df.at[10, "close"] = snapshot.at[10, "close"]
    pd.testing.assert_frame_equal(df, snapshot)


def test_regime_incoherent_bar_geometry_invalid():
    df = _trending(200)
    df.at[120, "low"] = df.at[120, "high"] + 5.0
    res = classify_regimes(df)
    assert res["classification"] == INVALID
    assert res["offending"]["bad_bar_count"] >= 1


def test_regime_bad_timestamps_and_empty():
    assert classify_regimes(_trending(0))["classification"] == NOT_AVAILABLE
    df = _trending(50)
    df.at[10, "open_time"] = int(df.at[9, "open_time"])
    assert classify_regimes(df)["classification"] == INVALID


def test_regime_policy_drift_changes_evaluation_behavior():
    drifted = dict(DEFAULT_REGIME_POLICY); drifted["version"] = "regime-vX"
    assert regime_policy_hash(drifted) != regime_policy_hash(DEFAULT_REGIME_POLICY)
    assert classify_regimes(_trending(300), drifted)["classification"] == INVALID
    alt = freeze_regime_policy(fast_window=5, slow_window=10, vol_window=5, vol_ref_window=20)
    base_res = classify_regimes(_trending(300))
    alt_res = classify_regimes(_trending(300), alt)
    assert alt_res["classification"] == PASS
    assert alt_res["warmup"] != base_res["warmup"]


def test_regime_execution_timing_is_frozen_and_validated():
    et = DEFAULT_REGIME_POLICY["execution_timing"]
    assert et["label_available_after"] == "bar_t_close"
    assert et["earliest_permitted_execution"] == "bar_t_plus_1_open"
    assert "bar_t_open" in et["prohibited"] and "intrabar_before_bar_t_close" in et["prohibited"]
    # dropping the execution-timing contract fails policy validation
    bad = dict(DEFAULT_REGIME_POLICY); bad.pop("execution_timing")
    assert validate_regime_policy(bad)[0] is False


def test_regime_timestamp_contract_is_validated_and_honest():
    ta = DEFAULT_REGIME_POLICY["timestamp_assumptions"]
    assert ta["order"] == "strictly_increasing_unique" and ta["order_enforced"] is True
    # the code does NOT verify uniform cadence, and the frozen policy must not claim it does
    assert ta["uniform_cadence_verified"] is False
    dishonest = dict(DEFAULT_REGIME_POLICY)
    dishonest["timestamp_assumptions"] = dict(ta, uniform_cadence_verified=True)
    assert validate_regime_policy(dishonest)[0] is False


def test_regime_label_alignment_prevents_same_bar_execution():
    labels = classify_regimes(_trending(400))["labels"]
    aligned = align_labels_to_execution(labels)  # earliest execution = bar t+1 open
    assert len(aligned) == len(labels)
    assert aligned[0] == LABEL_NA                       # no prior closed bar
    # the label acting at bar i's open is the PRIOR bar's label — never bar i's own
    assert all(aligned[i] == labels[i - 1] for i in range(1, len(labels)))
    # and the shift is real: there exists a bar where the aligned label differs from its own
    assert any(aligned[i] != labels[i] for i in range(len(labels)))
    # bar-t-open / intrabar execution is prohibited outright
    with pytest.raises(ValueError):
        align_labels_to_execution(labels, execution="bar_t_open")
    with pytest.raises(ValueError):
        align_labels_to_execution(labels, execution="intrabar_before_bar_t_close")


# =========================================================================== #
# REFERENCE RANGES                                                            #
# =========================================================================== #
def test_range_policy_freeze_and_hash():
    p = _rpol(seed=1, block_length=2, n_resamples=100)
    assert p["seed"] == 1 and p["order_field"] == "segment_index"
    assert p["missing_data_policy"] == "fail_closed"
    assert range_policy_hash(p) == range_policy_hash(_rpol(seed=1, block_length=2, n_resamples=100))
    assert validate_range_policy(p) == (True, "")


def test_range_policy_hash_detects_seed_drift():
    assert range_policy_hash(_rpol(seed=1)) != range_policy_hash(_rpol(seed=2))


def test_range_all_required_pass_counts_as_evidence():
    ref = _reference()
    assert ref["classification"] == PASS
    assert ref["all_required_ranges_available"] is True
    assert ref["counts_as_evidence"] is True
    assert set(ref["available_required_metrics"]) == set(ref["required_metrics"])
    assert ref["unavailable_required_metrics"] == []


def test_range_not_pass_when_a_required_metric_missing():
    ref = build_reference_ranges(_segments(24, drop=["drawdown"]), policy=_rpol())
    assert ref["groups"]["__ALL__"]["metrics"]["return"]["classification"] == PASS
    assert ref["classification"] == NOT_AVAILABLE
    assert ref["counts_as_evidence"] is False
    assert "drawdown" in ref["unavailable_required_metrics"]


def test_range_optional_metric_missing_does_not_block():
    ref = build_reference_ranges(_segments(24, drop=["long_short_balance"]), policy=_rpol())
    assert ref["classification"] == PASS
    assert ref["groups"]["__ALL__"]["metrics"]["long_short_balance"]["classification"] == NOT_AVAILABLE
    assert ref["counts_as_evidence"] is True


def test_range_missing_required_not_inferred_optional():
    ref = build_reference_ranges(_segments(24, drop=["turnover"]), policy=_rpol())
    assert ref["classification"] == NOT_AVAILABLE
    assert "turnover" in ref["unavailable_required_metrics"]


def test_range_non_finite_required_fails_closed_with_indexes():
    ev = _segments(24)
    ev[0]["metrics"]["return"] = float("nan")
    ev[1]["metrics"]["return"] = float("inf")
    ref = build_reference_ranges(ev, policy=_rpol())
    ret = ref["groups"]["__ALL__"]["metrics"]["return"]
    assert ret["classification"] == NOT_AVAILABLE
    assert ret["non_finite_count"] == 2
    assert ret["non_finite_segment_order_indexes"] == [0, 1]
    assert ret["lower"] is None and ret["upper"] is None
    assert ref["classification"] == NOT_AVAILABLE


def test_range_malformed_value_is_invalid():
    ev = _segments(24)
    ev[3]["metrics"]["return"] = "oops"
    r = build_reference_ranges(ev, policy=_rpol())
    assert r["classification"] == INVALID
    assert r["offending"]["malformed_count"] == 1
    assert r["offending"]["malformed"][0]["segment_order_index"] == 3
    ev2 = _segments(24); ev2[2]["metrics"]["trade_count"] = True
    assert build_reference_ranges(ev2, policy=_rpol())["classification"] == INVALID


def test_range_numpy_scalar_values_accepted():
    ev = _segments(24)
    for s in ev:
        s["metrics"]["return"] = np.float64(s["metrics"]["return"])
        s["metrics"]["trade_count"] = np.int64(int(s["metrics"]["trade_count"]))
    assert build_reference_ranges(ev, policy=_rpol())["classification"] == PASS


def test_range_hash_is_deterministic_and_embeds_policy():
    ref1, ref2 = _reference(), _reference()
    assert ref1["range_hash"] == ref2["range_hash"]
    assert range_policy_hash(ref1["policy"]) == ref1["policy_hash"]


def test_range_seed_change_moves_bounds():
    ev = _segments(24, base=0.01)
    a = build_reference_ranges(ev, policy=_rpol(seed=1))
    b = build_reference_ranges(ev, policy=_rpol(seed=999))
    ra = a["groups"]["__ALL__"]["metrics"]["return"]
    rb = b["groups"]["__ALL__"]["metrics"]["return"]
    assert (ra["lower"], ra["upper"]) != (rb["lower"], rb["upper"])
    assert a["range_hash"] != b["range_hash"]


def test_range_duplicate_counts_global_and_group():
    ev = _segments(24) + [copy.deepcopy(_segment(0))]
    ref = build_reference_ranges(ev, policy=_rpol())
    assert ref["n_segments_raw"] == 25 and ref["n_segments_unique"] == 24
    assert ref["duplicate_count"] == 1
    grp = ref["groups"]["__ALL__"]
    assert grp["duplicate_count"] == 1
    assert grp["metrics"]["return"]["duplicate_count"] == 1


def test_range_small_sample_is_not_available_extend():
    ref = build_reference_ranges(_segments(3), policy=_rpol())
    m = ref["groups"]["__ALL__"]["metrics"]["return"]
    assert m["classification"] == NOT_AVAILABLE and R_EXTEND in m["limitations"]
    assert ref["classification"] == NOT_AVAILABLE


def test_range_block_count_approximation_naming_and_legacy_alias():
    ref = build_reference_ranges(_segments(24), policy=_rpol(block_length=4))
    m = ref["groups"]["__ALL__"]["metrics"]["return"]
    # primary field is the descriptive block-count diagnostic
    assert m["block_count_approximation"] == pytest.approx(m["usable_count"] / 4.0)
    assert "NOT a statistically estimated" in m["block_count_approximation_note"]
    assert "NOT guaranteed to be conservative" in m["block_count_approximation_note"]
    # legacy alias kept, same value, and explicitly NOT described as a formal/conservative ESS
    assert m["block_effective_sample_size"] == m["block_count_approximation"]
    assert "legacy alias" in m["block_effective_sample_size_note"]


def test_range_note_does_not_claim_conservative_ess():
    # the generated top-level note must NOT repeat the outdated "conservative approximation" claim
    ref = build_reference_ranges(_segments(24), policy=_rpol())
    note = ref["note"]
    outdated = "block_effective_sample_size = N / block_length is a conservative approximation"
    assert outdated not in note
    assert "conservative approximation" not in note
    # and it must carry the corrected, honest wording
    assert "descriptive block-count diagnostic" in note
    assert "NOT a statistically estimated effective sample size" in note
    assert "NOT guaranteed to be" in note and "legacy alias" in note


def test_range_interval_semantics_and_comparability_are_frozen():
    pol = _rpol()
    assert "NOT a prediction interval" in pol["interval_semantics"]
    cc = pol["comparability_contract"]
    assert cc["ordering"] == pol["order_field"] and cc["cost_basis"] == pol["cost_basis"]
    # cost-basis / aggregation are frozen into the hashes -> drift is detectable
    alt = freeze_range_policy(seed=12345, block_length=3, n_resamples=500,
                              min_unique_segments=6, cost_basis="stress_costs")
    assert range_policy_hash(pol) != range_policy_hash(alt)
    a = build_reference_ranges(_segments(24), policy=pol)
    b = build_reference_ranges(_segments(24), policy=alt)
    assert a["range_hash"] != b["range_hash"]
    # a policy stripped of the comparability contract fails validation
    bad = dict(pol); bad.pop("comparability_contract")
    assert validate_range_policy(bad)[0] is False


def test_range_policy_validation_rejects_bad_metric_definitions():
    # duplicate required
    with pytest.raises(ValueError):
        freeze_range_policy(required_metrics=("return", "return", "drawdown"))
    # overlapping required/optional
    with pytest.raises(ValueError):
        freeze_range_policy(required_metrics=("return", "drawdown"),
                            optional_metrics=("drawdown",))
    # malformed (non-string) required
    with pytest.raises(ValueError):
        freeze_range_policy(required_metrics=("return", 123))
    # empty required
    with pytest.raises(ValueError):
        freeze_range_policy(required_metrics=())
    # validate_range_policy rejects the same when handed a hand-built dict
    good = _rpol()
    for mutate in (
        lambda p: p.update(required_metrics=[], metrics=list(p["optional_metrics"])),
        lambda p: p.update(required_metrics=["return", "return"], metrics=["return", "return"] + p["optional_metrics"]),
        lambda p: p.update(optional_metrics=["return"]),  # overlap with a required metric
    ):
        p = copy.deepcopy(good); mutate(p)
        assert validate_range_policy(p)[0] is False


def test_range_ordering_field_required_and_validated():
    ev = _segments(24)
    del ev[5]["segment_index"]
    assert build_reference_ranges(ev, policy=_rpol())["classification"] == INVALID
    ev2 = _segments(24)
    ev2[5]["segment_index"] = ev2[4]["segment_index"]
    assert build_reference_ranges(ev2, policy=_rpol())["classification"] == INVALID


def test_range_group_by_missing_field_is_invalid():
    ev = [_segment(i, top={"asset": "BTC"} if i < 10 else {}) for i in range(20)]
    r = build_reference_ranges(ev, policy=_rpol(min_unique_segments=4), group_by=["asset"])
    assert r["classification"] == INVALID


def test_range_segmentation_fold_asset_regime_cost():
    pol = _rpol(min_unique_segments=4)
    ev_a = [_segment(i, top={"asset": "BTC" if i < 10 else "ETH"}) for i in range(20)]
    r_a = build_reference_ranges(ev_a, policy=pol, group_by=["asset"])
    assert r_a["classification"] == PASS and len(r_a["groups"]) == 2
    ev_m, idx = [], 0
    for fold in range(2):
        for regime in ("TREND_LOW_VOL", "RANGE_HIGH_VOL"):
            for cost in ("base", "stress"):
                for _ in range(6):
                    ev_m.append(_segment(idx, top={"walk_forward_fold": fold, "regime": regime,
                                                    "cost_scenario": cost}))
                    idx += 1
    r_m = build_reference_ranges(ev_m, policy=pol,
                                 group_by=["walk_forward_fold", "regime", "cost_scenario"])
    assert r_m["classification"] == PASS and len(r_m["groups"]) == 8


def test_range_evidence_is_not_mutated():
    ev = _segments(24)
    original = copy.deepcopy(ev)
    build_reference_ranges(ev, policy=_rpol())
    assert ev == original


def test_range_malformed_and_empty_fail_closed():
    assert build_reference_ranges(123)["classification"] == INVALID
    assert build_reference_ranges([{"no_metrics": 1}])["classification"] == INVALID
    assert build_reference_ranges([])["classification"] == NOT_AVAILABLE


# =========================================================================== #
# CONSISTENCY / CHECKPOINTS                                                   #
# =========================================================================== #
def test_consistency_policy_freeze_hash_includes_schedule():
    p = _cpol()
    assert p["checkpoints"] == [30, 60, 90]
    assert list(p["precedence_order"]) == list(PRECEDENCE_ORDER)
    assert consistency_policy_hash(p) == consistency_policy_hash(_cpol())


def test_consistency_policy_hash_changes_with_checkpoints():
    assert consistency_policy_hash(_cpol()) != consistency_policy_hash(_cpol(checkpoints=(25, 50, 75)))


def test_consistency_in_range_at_checkpoint_is_consistent():
    ref = _reference()
    res = evaluate_consistency(_observed(ref, _mid(ref)), ref, policy=_cpol(), checkpoint=60)
    assert res["classification"] == CONSISTENT
    assert res["counts_as_evidence"] is True
    assert res["all_required_checks_available"] is True


def test_consistency_negative_pnl_in_range_is_consistent():
    ref = _reference(base=-0.02)
    vals = _mid(ref)
    assert vals["return"] < 0.0
    g = ref["groups"]["__ALL__"]["metrics"]["return"]
    assert g["lower"] <= vals["return"] <= g["upper"]
    res = evaluate_consistency(_observed(ref, vals), ref, policy=_cpol(), checkpoint=60)
    assert res["classification"] == CONSISTENT


def test_consistency_positive_pnl_does_not_hide_drift():
    ref = _reference()
    vals = _mid(ref)
    gret = ref["groups"]["__ALL__"]["metrics"]["return"]
    gwr = ref["groups"]["__ALL__"]["metrics"]["win_rate"]
    vals["return"] = gret["upper"]
    assert vals["return"] > 0.0
    vals["win_rate"] = gwr["upper"] + 5.0
    res = evaluate_consistency(_observed(ref, vals, excursions={"win_rate": 3}), ref,
                               policy=_cpol(), checkpoint=60)
    assert res["classification"] == DRIFT
    assert "win_rate" in res["drifted_metrics"]


def test_consistency_isolated_extreme_is_not_drift():
    ref = _reference()
    vals = _mid(ref)
    vals["win_rate"] = ref["groups"]["__ALL__"]["metrics"]["win_rate"]["upper"] + 5.0
    res = evaluate_consistency(_observed(ref, vals), ref, policy=_cpol(), checkpoint=60)
    assert res["classification"] == CONSISTENT
    assert res["drifted_metrics"] == []
    assert any("isolated" in l for l in res["limitations"])


def test_consistency_persistent_single_metric_is_drift():
    ref = _reference()
    vals = _mid(ref)
    vals["win_rate"] = ref["groups"]["__ALL__"]["metrics"]["win_rate"]["upper"] + 5.0
    res = evaluate_consistency(_observed(ref, vals, excursions={"win_rate": 4}), ref,
                               policy=_cpol(), checkpoint=60)
    assert res["classification"] == DRIFT


def test_consistency_coherent_multi_metric_is_drift():
    ref = _reference()
    vals = _mid(ref)
    m = ref["groups"]["__ALL__"]["metrics"]
    vals["return"] = m["return"]["upper"] + 1.0
    vals["trade_count"] = m["trade_count"]["upper"] + 50.0
    res = evaluate_consistency(_observed(ref, vals), ref, policy=_cpol(), checkpoint=60)
    assert res["classification"] == DRIFT


def test_consistency_absolute_risk_breach_is_failed():
    ref = _reference()
    res = evaluate_consistency(_observed(ref, _mid(ref), breach=True), ref, policy=_cpol(), checkpoint=60)
    assert res["classification"] == FAILED and res["counts_as_evidence"] is True


def test_consistency_breach_fires_before_checkpoint():
    ref = _reference()
    res = evaluate_consistency(_observed(ref, _mid(ref), trades=5, breach=True), ref,
                               policy=_cpol(), checkpoint=30)
    assert res["classification"] == FAILED


def test_consistency_absolute_risk_missing_is_not_available():
    ref = _reference()
    res = evaluate_consistency(_observed(ref, _mid(ref), absolute_risk=False), ref,
                               policy=_cpol(), checkpoint=60)
    assert res["classification"] == NOT_AVAILABLE


def test_consistency_absolute_risk_malformed_is_invalid():
    ref = _reference()
    o = _observed(ref, _mid(ref)); o["absolute_risk"] = {"breached": "yes"}
    assert evaluate_consistency(o, ref, policy=_cpol(), checkpoint=60)["classification"] == INVALID


@pytest.mark.parametrize("trades,expected", [
    (59, EXTEND), (60, CONSISTENT), (61, EXTEND), (75, EXTEND), (90, EXTEND)])
def test_consistency_exact_checkpoint_semantics(trades, expected):
    ref = _reference()
    res = evaluate_consistency(_observed(ref, _mid(ref), trades=trades), ref,
                               policy=_cpol(), checkpoint=60)
    assert res["classification"] == expected


def test_consistency_continuous_mode_never_consistent_or_drift():
    ref = _reference()
    vals = _mid(ref)
    vals["return"] = ref["groups"]["__ALL__"]["metrics"]["return"]["upper"] + 1.0
    vals["trade_count"] = ref["groups"]["__ALL__"]["metrics"]["trade_count"]["upper"] + 50.0
    res = evaluate_consistency(_observed(ref, vals), ref, policy=_cpol(), checkpoint=None)
    assert res["classification"] == EXTEND and res["mode"] == "continuous"


def test_consistency_offschedule_checkpoint_rejected():
    ref = _reference()
    res = evaluate_consistency(_observed(ref, _mid(ref), trades=45), ref, policy=_cpol(), checkpoint=45)
    assert res["classification"] == INVALID


def test_consistency_missing_required_metric_never_consistent():
    ref = _reference()
    vals = _mid(ref)
    del vals["drawdown"]
    res = evaluate_consistency(_observed(ref, vals), ref, policy=_cpol(), checkpoint=60)
    assert res["classification"] == NOT_AVAILABLE
    assert res["all_required_checks_available"] is False
    assert "drawdown" in res["unavailable_required_metrics"]


def test_consistency_reference_incomplete_never_consistent():
    ref = build_reference_ranges(_segments(24, drop=["drawdown"]), policy=_rpol())
    vals = {k: 0.0 for k in ref["policy"]["required_metrics"]}
    res = evaluate_consistency(_observed(ref, vals), ref, policy=_cpol(), checkpoint=60)
    assert res["classification"] == NOT_AVAILABLE


@pytest.mark.parametrize("drop_hash", ["range", "range_policy", "consistency"])
def test_consistency_missing_required_hash_is_not_available(drop_hash):
    ref = _reference()
    include = tuple(x for x in ("range", "range_policy", "consistency") if x != drop_hash)
    o = _observed(ref, _mid(ref), include=include)
    assert evaluate_consistency(o, ref, policy=_cpol(), checkpoint=60)["classification"] == NOT_AVAILABLE


def test_consistency_missing_required_regime_hash_is_not_available():
    ref = _reference()
    o = _observed(ref, _mid(ref))
    res = evaluate_consistency(o, ref, policy=_cpol(), checkpoint=60,
                               regime_policy_hash="rgh-abc", require_regime=True)
    assert res["classification"] == NOT_AVAILABLE


def test_consistency_tampered_bound_with_unchanged_range_hash_is_invalidated():
    ref = _reference()
    tampered = copy.deepcopy(ref)
    tampered["groups"]["__ALL__"]["metrics"]["return"]["lower"] = 999.0
    res = evaluate_consistency(_observed(ref, _mid(ref)), tampered, policy=_cpol(), checkpoint=60)
    assert res["classification"] == INVALIDATED


def test_consistency_tampered_required_metrics_is_invalidated():
    # a STRUCTURALLY-VALID tamper (move turnover required->optional) that is not re-hashed still
    # trips the frozen policy_hash and is INVALIDATED
    ref = _reference()
    tampered = copy.deepcopy(ref)
    new_required = [m for m in tampered["policy"]["required_metrics"] if m != "turnover"]
    new_optional = tampered["policy"]["optional_metrics"] + ["turnover"]
    tampered["policy"]["required_metrics"] = new_required
    tampered["policy"]["optional_metrics"] = new_optional
    tampered["policy"]["metrics"] = new_required + new_optional  # keep policy structurally valid
    res = evaluate_consistency(_observed(ref, _mid(ref)), tampered, policy=_cpol(), checkpoint=60)
    assert res["classification"] == INVALIDATED


def test_consistency_tampered_checkpoint_policy_is_invalidated():
    ref = _reference()
    o = _observed(ref, _mid(ref), cpol=_cpol())
    res = evaluate_consistency(o, ref, policy=_cpol(checkpoints=(25, 50, 75)), checkpoint=50)
    assert res["classification"] == INVALIDATED


def test_consistency_valid_matching_hashes_is_consistent():
    ref = _reference()
    rgh = regime_policy_hash(DEFAULT_REGIME_POLICY)
    o = _observed(ref, _mid(ref), expected_regime_policy_hash=rgh)
    res = evaluate_consistency(o, ref, policy=_cpol(), checkpoint=60,
                               regime_policy_hash=rgh, require_regime=True)
    assert res["classification"] == CONSISTENT


def test_consistency_regime_hash_mismatch_is_invalidated():
    ref = _reference()
    o = _observed(ref, _mid(ref), expected_regime_policy_hash="WRONG")
    res = evaluate_consistency(o, ref, policy=_cpol(), checkpoint=60,
                               regime_policy_hash="rgh-real", require_regime=True)
    assert res["classification"] == INVALIDATED


def test_consistency_numpy_scalar_inputs_supported():
    ref = _reference()
    vals = {k: np.float64(v) for k, v in _mid(ref).items()}
    o = _observed(ref, vals, trades=np.int64(60), obs=np.int64(200))
    assert evaluate_consistency(o, ref, policy=_cpol(), checkpoint=60)["classification"] == CONSISTENT


def test_consistency_precedence_invalidated_beats_failed():
    ref = _reference()
    tampered = copy.deepcopy(ref)
    tampered["groups"]["__ALL__"]["metrics"]["return"]["lower"] = 999.0
    o = _observed(ref, _mid(ref), breach=True)
    res = evaluate_consistency(o, tampered, policy=_cpol(), checkpoint=60)
    assert res["classification"] == INVALIDATED


def test_consistency_precedence_failed_beats_not_available():
    ref = _reference()
    vals = _mid(ref)
    del vals["drawdown"]
    o = _observed(ref, vals, breach=True)
    res = evaluate_consistency(o, ref, policy=_cpol(), checkpoint=60)
    assert res["classification"] == FAILED


def test_consistency_malformed_inputs_are_invalid():
    ref = _reference()
    assert evaluate_consistency("nope", ref, policy=_cpol(), checkpoint=60)["classification"] == INVALID
    assert evaluate_consistency(_observed(ref, _mid(ref)), "nope", policy=_cpol(), checkpoint=60)["classification"] == INVALID
    assert evaluate_consistency(_observed(ref, _mid(ref)), ref, policy={}, checkpoint=60)["classification"] == INVALID


def test_consistency_evidence_hash_deterministic_and_side_effect_free():
    ref = _reference()
    obs = _observed(ref, _mid(ref))
    cpol = _cpol()

    roots = ["app/forward/shadow", "tests", "knowledge_base", "."]
    fs_before = {r: sorted(os.listdir(r)) for r in roots if os.path.isdir(r)}
    obs_before = copy.deepcopy(obs)
    ref_before = copy.deepcopy(ref)
    cpol_before = copy.deepcopy(cpol)

    a = evaluate_consistency(obs, ref, policy=cpol, checkpoint=60)
    b = evaluate_consistency(obs, ref, policy=cpol, checkpoint=60)

    assert a["evidence_hash"] == b["evidence_hash"]
    assert obs == obs_before and ref == ref_before and cpol == cpol_before
    fs_after = {r: sorted(os.listdir(r)) for r in roots if os.path.isdir(r)}
    assert fs_before == fs_after
    assert a["consistency_policy_hash"] and a["range_hash"] and a["reached_checkpoint"] == 60
    assert "changed NOTHING" in a["note"]


def test_consistency_empty_required_metrics_rehashed_cannot_be_consistent():
    # the key regression: empty required_metrics with FULLY re-hashed integrity (no simple hash
    # mismatch) must be INVALID, never CONSISTENT.
    ref = _reference()
    tampered = copy.deepcopy(ref)
    tampered["policy"]["required_metrics"] = []
    tampered["policy"]["metrics"] = list(tampered["policy"]["optional_metrics"])
    tampered = _rehash_reference(tampered)  # recompute policy_hash AND range_hash
    obs = _observed(ref, _mid(ref),
                    expected_range_hash=tampered["range_hash"],
                    expected_range_policy_hash=tampered["policy_hash"])
    res = evaluate_consistency(obs, tampered, policy=_cpol(), checkpoint=60)
    assert res["classification"] == INVALID
    assert res["classification"] != CONSISTENT


def test_consistency_structurally_invalid_embedded_policy_is_invalid():
    # a valid hash alone is not proof: recompute the hashes over a policy whose metrics list is
    # inconsistent with required+optional -> still INVALID
    ref = _reference()
    tampered = copy.deepcopy(ref)
    tampered["policy"]["required_metrics"] = ["return"]  # metrics list left inconsistent
    tampered = _rehash_reference(tampered)
    obs = _observed(ref, _mid(ref), expected_range_hash=tampered["range_hash"],
                    expected_range_policy_hash=tampered["policy_hash"])
    assert evaluate_consistency(obs, tampered, policy=_cpol(), checkpoint=60)["classification"] == INVALID


@pytest.mark.parametrize("value,expected", [
    ("oops", INVALID),           # string
    (True, INVALID),             # bool
    ([1, 2], INVALID),           # list
    ({"a": 1}, INVALID),         # dict
    (1 + 2j, INVALID),           # complex
    (float("nan"), NOT_AVAILABLE),
    (float("inf"), NOT_AVAILABLE),
    (float("-inf"), NOT_AVAILABLE),
    (None, NOT_AVAILABLE),       # explicit None
])
def test_consistency_malformed_vs_unavailable_observed_metric(value, expected):
    ref = _reference()
    vals = _mid(ref)
    vals["return"] = value
    res = evaluate_consistency(_observed(ref, vals), ref, policy=_cpol(), checkpoint=60)
    assert res["classification"] == expected


def test_consistency_missing_observed_metric_is_not_available():
    ref = _reference()
    vals = _mid(ref)
    del vals["return"]  # entirely absent
    assert evaluate_consistency(_observed(ref, vals), ref, policy=_cpol(), checkpoint=60)["classification"] == NOT_AVAILABLE


def test_consistency_accepts_finite_python_and_numpy_scalars():
    ref = _reference()
    vals = _mid(ref)
    vals["return"] = np.float64(vals["return"])   # numpy float
    vals["trade_count"] = np.int64(int(vals["trade_count"]))  # numpy int
    res = evaluate_consistency(_observed(ref, vals, trades=np.int64(60), obs=np.int64(200)), ref,
                               policy=_cpol(), checkpoint=60)
    assert res["classification"] == CONSISTENT


def test_consistency_malformed_observed_metric_beats_failed():
    # INVALID (malformed datatype) has precedence over FAILED per the frozen order
    ref = _reference()
    vals = _mid(ref)
    vals["return"] = "oops"
    res = evaluate_consistency(_observed(ref, vals, breach=True), ref, policy=_cpol(), checkpoint=60)
    assert res["classification"] == INVALID


# ---- round 4: observed/reference comparability, precedence, metrics container -------------- #
def test_consistency_matching_comparability_basis_is_consistent():
    ref = _reference()
    res = evaluate_consistency(_observed(ref, _mid(ref)), ref, policy=_cpol(), checkpoint=60)
    assert res["classification"] == CONSISTENT


def test_consistency_missing_comparability_is_not_available_even_with_matching_hash():
    # copying the reference's expected hash is NOT enough to establish comparability
    ref = _reference()
    o = _observed(ref, _mid(ref), comparability=False)  # hashes present, basis NOT declared
    res = evaluate_consistency(o, ref, policy=_cpol(), checkpoint=60)
    assert res["classification"] == NOT_AVAILABLE
    assert res["classification"] != CONSISTENT


def test_consistency_malformed_comparability_is_invalid():
    ref = _reference()
    for bad in ("nope", 123, ["a"], {"cost_basis": "x"}):  # wrong type / missing keys
        o = _observed(ref, _mid(ref), comparability=False)
        o["comparability"] = bad
        assert evaluate_consistency(o, ref, policy=_cpol(), checkpoint=60)["classification"] == INVALID


@pytest.mark.parametrize("field,value", [
    ("cost_basis", "stress_costs"),
    ("segment_horizon", "weekly"),
    ("aggregation", "sum_only"),
    ("metric_definition_version", "metrics-v2"),
    ("ordering_basis", "fold_index"),
])
def test_consistency_different_comparability_basis_cannot_be_consistent(field, value):
    # an observation produced on a different basis is INVALIDATED, never CONSISTENT
    ref = _reference()
    o = _observed(ref, _mid(ref))
    o["comparability"][field] = value
    res = evaluate_consistency(o, ref, policy=_cpol(), checkpoint=60)
    assert res["classification"] == INVALIDATED
    assert res["classification"] != CONSISTENT


def test_consistency_different_grouping_basis_cannot_be_consistent():
    ref = _reference()
    o = _observed(ref, _mid(ref))
    o["comparability"]["grouping_basis"] = ["asset"]  # reference was built ungrouped ([])
    assert evaluate_consistency(o, ref, policy=_cpol(), checkpoint=60)["classification"] == INVALIDATED


@pytest.mark.parametrize("container", [[1, 2], "metrics", True, 3.5])
def test_consistency_malformed_metrics_container_is_invalid(container):
    # a present-but-non-dict metrics container is INVALID, never silently emptied to unavailable
    ref = _reference()
    o = _observed(ref, _mid(ref))
    o["metrics"] = container
    assert evaluate_consistency(o, ref, policy=_cpol(), checkpoint=60)["classification"] == INVALID


def test_consistency_precedence_is_honest_structural_invalid_before_integrity():
    # PRECEDENCE_ORDER declares INVALID before INVALIDATED, and the implementation honours it:
    # a structurally malformed input wins over a tampered (integrity-broken) reference.
    assert list(PRECEDENCE_ORDER)[:2] == [INVALID, INVALIDATED]
    ref = _reference()
    tampered = copy.deepcopy(ref)
    tampered["groups"]["__ALL__"]["metrics"]["return"]["lower"] = 999.0  # -> would be INVALIDATED
    o = _observed(ref, _mid(ref))
    o["metrics"] = "not-a-dict"  # structural INVALID
    assert evaluate_consistency(o, tampered, policy=_cpol(), checkpoint=60)["classification"] == INVALID
