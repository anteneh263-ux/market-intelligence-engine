"""Phase-2 targeted tests: market-data quality validation (fail-closed) and SHADOW
admission eligibility (fail-closed).

Deterministic and offline. Imports ONLY the Phase-2 modules under review; NEVER
consistency.py, ranges.py or regime.py. Eligibility evaluation starts nothing.
"""
from __future__ import annotations

import copy
import os
from datetime import datetime
from decimal import Decimal

import numpy as np
import pandas as pd

from app.forward.shadow.dataquality import (
    INVALID,
    INVALIDATED,
    NOT_AVAILABLE,
    PASS,
    classify_observation,
    frame_content_sha256,
    normalize_timestamps,
    validate_frame,
)
from app.research_safety.shadow_admission import (
    ELIGIBLE_FOR_PROMOTE,
    ELIGIBLE_FOR_SHADOW,
    REJECT,
    evaluate_shadow_admission,
)
from app.research_safety.shadow_admission import INVALID as ADM_INVALID

BASE = 1_514_764_800_000            # 2018-01-01 00:00 UTC — a Monday
DAY = 86_400_000
CONT = {"mode": "continuous", "cadence_ms": DAY}


def _clean(n=12, base=BASE, step=DAY):
    return _frame((base + np.arange(n) * step).astype("int64"))


def _frame(ot_series):
    n = len(ot_series)
    close = 100.0 + np.arange(n) * 1.0
    open_ = close - 0.5
    return pd.DataFrame({"open_time": ot_series, "open": open_,
                         "high": np.maximum(open_, close) + 1.0,
                         "low": np.minimum(open_, close) - 1.0,
                         "close": close, "volume": np.full(n, 10.0)})


def _equity(n_weeks=2, base=BASE):
    days = [base + (w * 7 + d) * DAY for w in range(n_weeks) for d in range(5)]  # Mon–Fri
    return _frame(np.array(sorted(days), dtype="int64"))


def _sessions(holidays, tz="+00:00"):
    return {"mode": "sessions", "cadence_ms": DAY, "session_weekdays": [0, 1, 2, 3, 4],
            "session_timezone": tz, "holidays": list(holidays)}


# ===========================================================================
# 1. Timestamp normalisation
# ===========================================================================
def test_ts_integer_epoch_ms_passes():
    chk, ms = normalize_timestamps(_clean()["open_time"])
    assert chk["status"] == PASS and ms is not None and ms.dtype == np.int64


def test_ts_timezone_aware_datetime_normalises():
    ot = pd.Series(pd.to_datetime((BASE + np.arange(6) * DAY), unit="ms", utc=True))
    chk, ms = normalize_timestamps(ot)
    assert chk["status"] == PASS and list(ms) == [BASE + i * DAY for i in range(6)]


def test_ts_timezone_naive_datetime_invalid_by_default():
    ot = pd.Series(pd.to_datetime((BASE + np.arange(6) * DAY), unit="ms"))
    assert normalize_timestamps(ot)[0]["status"] == INVALID
    assert normalize_timestamps(ot, tz_naive_policy=NOT_AVAILABLE)[0]["status"] == NOT_AVAILABLE


def test_ts_strings_invalid():
    assert normalize_timestamps(pd.Series(["2018-01-01", "2018-01-02"]))[0]["status"] == INVALID


def test_ts_mixed_types_invalid():
    assert normalize_timestamps(pd.Series([BASE, "2018-01-02", BASE + 2 * DAY], dtype=object))[0]["status"] == INVALID


def test_ts_float_invalid_unless_lossless_policy():
    ot = pd.Series((BASE + np.arange(4) * DAY).astype(float))
    assert normalize_timestamps(ot)[0]["status"] == INVALID
    chk, ms = normalize_timestamps(ot, allow_float_ms=True)
    assert chk["status"] == PASS and list(ms) == [BASE + i * DAY for i in range(4)]
    assert normalize_timestamps(pd.Series((BASE + np.arange(4) * DAY).astype(float) + 0.5),
                                allow_float_ms=True)[0]["status"] == INVALID


def test_ts_nat_invalid():
    s = pd.to_datetime((BASE + np.arange(4) * DAY), unit="ms", utc=True).to_series().reset_index(drop=True)
    s.iloc[2] = pd.NaT
    assert normalize_timestamps(s)[0]["status"] == INVALID


def test_ts_out_of_range_invalid():
    assert normalize_timestamps(pd.Series([BASE, BASE + DAY, 10 ** 15], dtype="int64"))[0]["status"] == INVALID


def test_cutoff_after_datetime_normalisation():
    ot = pd.Series(pd.to_datetime((BASE + np.arange(10) * DAY), unit="ms", utc=True))
    r = validate_frame(_frame(ot), asset="BTC/USD", cutoff_ms=BASE + 5 * DAY, cadence_policy=CONT)
    assert r["classification"] == INVALID
    assert any(c["check"] == "cutoff_separation" and c["status"] == INVALID for c in r["checks"])


# ===========================================================================
# 2. Gap-policy evidence semantics
# ===========================================================================
def test_clean_data_with_policy_is_complete_evidence():
    r = validate_frame(_clean(), asset="BTC/USD", cadence_policy=CONT)
    assert r["classification"] == PASS
    assert r["all_required_checks_passed"] is True and r["counts_as_evidence"] is True


def test_no_policy_is_pass_with_limitations_not_complete_evidence():
    r = validate_frame(_clean(), asset="BTC/USD")
    assert r["classification"] == PASS and r["all_required_checks_passed"] is False
    assert r["counts_as_evidence"] is False and r["pass_with_limitations"] is True
    assert "missing_candles" in r["missing_checks"] and r["limitations"]


def test_require_gap_validation_without_policy_not_available():
    r = validate_frame(_clean(), asset="BTC/USD", require_gap_validation=True)
    assert r["classification"] == NOT_AVAILABLE and r["counts_as_evidence"] is False


# ===========================================================================
# 3. Session calendars, holidays, and local timezone
# ===========================================================================
def test_weekend_is_not_a_gap():
    r = validate_frame(_equity(2), asset="SPY", cadence_policy=_sessions([]))
    assert next(c for c in r["checks"] if c["check"] == "missing_candles")["status"] == PASS
    assert r["classification"] == PASS


def test_explicit_holiday_is_not_a_gap():
    df = _equity(1)
    df = df[df["open_time"] != BASE + DAY].reset_index(drop=True)      # drop Tue 2018-01-02
    r = validate_frame(df, asset="SPY", cadence_policy=_sessions(["2018-01-02"]))
    assert next(c for c in r["checks"] if c["check"] == "missing_candles")["status"] == PASS


def test_missing_holiday_policy_cannot_claim_calendar_validation():
    policy = {"mode": "sessions", "cadence_ms": DAY, "session_weekdays": [0, 1, 2, 3, 4],
              "session_timezone": "+00:00"}                           # no holidays
    r = validate_frame(_equity(2), asset="SPY", cadence_policy=policy)
    assert next(c for c in r["checks"] if c["check"] == "missing_candles")["status"] == NOT_AVAILABLE


def test_genuine_missing_weekday_session_detected():
    df = _equity(1)
    df = df[df["open_time"] != BASE + DAY].reset_index(drop=True)      # drop Tue, NOT a holiday
    r = validate_frame(df, asset="SPY", cadence_policy=_sessions([]))
    gap = next(c for c in r["checks"] if c["check"] == "missing_candles")
    assert gap["status"] == NOT_AVAILABLE and gap["measured"]["n_missing"] >= 1
    assert r["classification"] == NOT_AVAILABLE


def test_session_calendar_uses_local_timezone_not_utc():
    # candles at 01:00 UTC == 20:00 previous day in ET(-05:00). Missing slot's LOCAL date
    # (2018-01-02, a Tue) is a declared holiday, so it is NOT a gap under the local calendar.
    c2 = BASE + DAY + 3_600_000
    c4 = BASE + 3 * DAY + 3_600_000
    ot = np.array([c2, c4], dtype="int64")
    local = validate_frame(_frame(ot), asset="SPY",
                           cadence_policy=_sessions(["2018-01-02"], tz="-05:00"))
    assert next(c for c in local["checks"] if c["check"] == "missing_candles")["status"] == PASS
    # evaluated in UTC instead, the same holiday would NOT match the slot's UTC date (2018-01-03)
    utc = validate_frame(_frame(ot), asset="SPY",
                         cadence_policy=_sessions(["2018-01-02"], tz="+00:00"))
    assert next(c for c in utc["checks"] if c["check"] == "missing_candles")["status"] == NOT_AVAILABLE


def test_session_missing_timezone_not_available_when_required():
    policy = {"mode": "sessions", "cadence_ms": DAY, "session_weekdays": [0, 1, 2, 3, 4],
              "holidays": []}                                         # no session_timezone
    r = validate_frame(_equity(2), asset="SPY", cadence_policy=policy, require_gap_validation=True)
    assert r["classification"] == NOT_AVAILABLE and r["counts_as_evidence"] is False


# ===========================================================================
# 4. Cadence alignment (interval multiples + absolute grid)
# ===========================================================================
def test_continuous_real_gap_detected():
    df = _clean(10).drop(index=5).reset_index(drop=True)
    r = validate_frame(df, asset="BTC/USD", cadence_policy=CONT)
    gap = next(c for c in r["checks"] if c["check"] == "missing_candles")
    assert gap["status"] == NOT_AVAILABLE and gap["measured"]["n_missing"] == 1


def test_one_and_a_half_cadence_interval_is_invalid():
    ot = np.array([BASE, BASE + DAY, BASE + DAY + DAY // 2, BASE + 2 * DAY + DAY // 2], dtype="int64")
    r = validate_frame(_frame(ot), asset="BTC/USD", cadence_policy=CONT)
    assert any(c["check"] == "cadence_alignment" and c["status"] == INVALID for c in r["checks"])
    assert r["classification"] == INVALID


def test_shorter_than_cadence_interval_invalid():
    ot = np.array([BASE, BASE + DAY, BASE + DAY + DAY // 3, BASE + 3 * DAY], dtype="int64")
    r = validate_frame(_frame(ot), asset="BTC/USD", cadence_policy=CONT)
    assert any(c["check"] == "cadence_alignment" and c["status"] == INVALID for c in r["checks"])


def test_absolute_grid_half_cadence_shift_detected():
    policy = {"mode": "continuous", "cadence_ms": DAY, "anchor_ms": BASE}
    shifted = (BASE + DAY // 2 + np.arange(6) * DAY).astype("int64")  # diffs all == DAY, but shifted
    r = validate_frame(_frame(shifted), asset="BTC/USD", cadence_policy=policy)
    grid = next(c for c in r["checks"] if c["check"] == "cadence_grid_alignment")
    assert grid["status"] == INVALID and r["classification"] == INVALID
    aligned = (BASE + np.arange(6) * DAY).astype("int64")
    r2 = validate_frame(_frame(aligned), asset="BTC/USD", cadence_policy=policy)
    assert next(c for c in r2["checks"] if c["check"] == "cadence_grid_alignment")["status"] == PASS


# ===========================================================================
# 5. Numeric source validation
# ===========================================================================
def test_text_price_is_invalid():
    df = _clean(); df["close"] = df["close"].astype(object); df.loc[3, "close"] = "oops"
    assert validate_frame(df, asset="BTC/USD", cadence_policy=CONT)["classification"] == INVALID


def test_decimal_nan_price_is_invalid():
    df = _clean(); df["close"] = df["close"].astype(object); df.loc[2, "close"] = Decimal("NaN")
    assert validate_frame(df, asset="BTC/USD", cadence_policy=CONT)["classification"] == INVALID


def test_mixed_numeric_text_volume_invalid():
    df = _clean(); df["volume"] = df["volume"].astype(object); df.loc[1, "volume"] = "n/a"
    r = validate_frame(df, asset="BTC/USD", cadence_policy=CONT)
    assert r["classification"] == INVALID
    assert any(c["check"] == "finite_volume" and c["status"] == INVALID for c in r["checks"])


def test_negative_volume_policy():
    df = _clean(); df.loc[2, "volume"] = -1.0
    assert validate_frame(df, asset="BTC/USD", cadence_policy=CONT)["classification"] == INVALID
    assert validate_frame(df, asset="BTC/USD", cadence_policy=CONT,
                          allow_negative_volume=True)["classification"] == PASS


def test_required_columns_option():
    df = _clean()
    assert validate_frame(df, asset="BTC/USD", cadence_policy=CONT,
                          required_columns=["vwap"])["classification"] == INVALID
    df2 = df.copy(); df2["vwap"] = df2["close"]
    assert validate_frame(df2, asset="BTC/USD", cadence_policy=CONT,
                          required_columns=["vwap"])["classification"] == PASS


def test_source_frame_not_modified_and_no_row_deletion():
    df = _clean(); df.loc[3, "close"] = np.nan
    before = df.copy(deep=True)
    r = validate_frame(df, asset="BTC/USD", cadence_policy=CONT)
    assert r["classification"] == INVALID and r["n_rows"] == len(df)
    pd.testing.assert_frame_equal(df, before)


# ===========================================================================
# 4b. Safe cutoff normalisation
# ===========================================================================
def test_cutoff_malformed_values_structured_invalid():
    for bad in ["abc", 1.5, float("inf"), float(BASE), 10 ** 15, True, [1]]:
        r = validate_frame(_clean(), asset="BTC/USD", cutoff_ms=bad, cadence_policy=CONT)
        assert any(c["check"] == "cutoff_normalization" and c["status"] == INVALID for c in r["checks"]), bad
        assert r["classification"] == INVALID
    ok = validate_frame(_clean(), asset="BTC/USD", cutoff_ms=BASE - DAY, cadence_policy=CONT)
    assert ok["classification"] == PASS


# ===========================================================================
# 6. Frozen-evidence integrity (value-sensitive, fail-closed, precedence)
# ===========================================================================
def test_frozen_integrity_is_value_sensitive_not_structural():
    a = _clean(); b = _clean(); b.loc[0, "close"] = float(b.loc[0, "close"]) + 1.0
    ha, hb = frame_content_sha256(a), frame_content_sha256(b)
    assert ha is not None and hb is not None and ha != hb           # equal cols+rows, diff values
    r = validate_frame(b, asset="BTC/USD", frozen_reference_sha256=ha)
    assert r["classification"] == INVALIDATED and r["counts_as_evidence"] is False
    # even the exotic fallback path stays value-sensitive (never a weak columns+row-count hash)
    x = _clean(); y = _clean()
    x["close"] = x["close"].astype(object); x.at[0, "close"] = [1, 2]     # unhashable cell
    y["close"] = y["close"].astype(object); y.at[0, "close"] = [3, 4]
    hx, hy = frame_content_sha256(x), frame_content_sha256(y)
    assert hx is not None and hy is not None and hx != hy


def test_frozen_frame_loses_column_is_invalidated():
    df = _clean(); frozen = frame_content_sha256(df)
    assert validate_frame(df.drop(columns=["volume"]), asset="BTC/USD",
                          frozen_reference_sha256=frozen)["classification"] == INVALIDATED


def test_frozen_frame_becomes_empty_is_invalidated():
    df = _clean(); frozen = frame_content_sha256(df)
    assert validate_frame(df.iloc[0:0], asset="BTC/USD",
                          frozen_reference_sha256=frozen)["classification"] == INVALIDATED


def test_frozen_frame_truncated_is_invalidated():
    df = _clean(); frozen = frame_content_sha256(df)
    assert validate_frame(df.iloc[:-2].reset_index(drop=True), asset="BTC/USD",
                          frozen_reference_sha256=frozen)["classification"] == INVALIDATED


def test_ordinary_bad_data_is_invalid_not_invalidated():
    df = _clean(); df.loc[0, "close"] = np.nan
    assert validate_frame(df, asset="BTC/USD", cadence_policy=CONT)["classification"] == INVALID


def test_classify_single_observation():
    assert classify_observation(1.23) == PASS
    assert classify_observation(np.nan) == NOT_AVAILABLE
    assert classify_observation(np.inf) == NOT_AVAILABLE
    assert classify_observation(None) == NOT_AVAILABLE


# ===========================================================================
# 5b + 6b. SHADOW admission — fail-closed schema + corrected semantics
# ===========================================================================
def _ev(**over):
    base = dict(
        backtest={"is_real": True, "causal": True},
        input_audit={"promotable": True, "declared_present": True},
        target_control={"verdict": "candidate_wins"},
        data_quality={"classification": "PASS", "all_required_checks_passed": True},
        promotion_gate={"decision": "HOLD"},
        absolute_risk={"breached": False},
        regime_coverage={"required": ["trend", "range"], "observed": ["trend", "range"]},
        costs_included=True, result_survives_costs=True,
        economic_mechanism="slow trend filter with a cash exit", economically_supported=True,
        preregistered=True, risk_rules_frozen=True, timing_valid=True, technical_failure=False,
        max_exposure=1.0, max_exposure_limit=1.0,
        family_dsr=0.5, effective_trial_count=200, trade_count=50,
    )
    base.update(over)
    return base


def test_admission_baseline_is_shadow():
    r = evaluate_shadow_admission(_ev())
    assert r["outcome"] == ELIGIBLE_FOR_SHADOW and r["shadow_eligible"] is True


def test_admission_technical_failures_invalid():
    for over in [dict(technical_failure=True), dict(backtest={"is_real": True, "causal": False}),
                 dict(timing_valid=False), dict(input_audit={"promotable": False}),
                 dict(costs_included=False), dict(economic_mechanism="  "),
                 dict(preregistered=False), dict(risk_rules_frozen=False)]:
        assert evaluate_shadow_admission(_ev(**over))["outcome"] == ADM_INVALID, over


def test_admission_negative_findings_are_reject_not_invalid():
    for over in [dict(result_survives_costs=False), dict(economically_supported=False),
                 dict(max_exposure=2.0, max_exposure_limit=1.0),
                 dict(absolute_risk={"breached": True}),
                 dict(target_control={"verdict": "control_wins"})]:
        r = evaluate_shadow_admission(_ev(**over))
        assert r["outcome"] == REJECT and r["technical_validity"] is True, over


def test_admission_missing_required_evidence_invalid_not_shadow():
    for over in [dict(backtest={}), dict(family_dsr=None), dict(family_dsr=float("nan")),
                 dict(target_control={}), dict(target_control={"verdict": "foo"}),
                 dict(absolute_risk={}), dict(promotion_gate={}), dict(input_audit={}),
                 dict(effective_trial_count=0), dict(effective_trial_count=1.5),
                 dict(trade_count=-1), dict(trade_count=None),
                 dict(max_exposure=None), dict(max_exposure_limit=None),
                 dict(technical_failure=None), dict(result_survives_costs=None),
                 dict(economically_supported=None)]:
        r = evaluate_shadow_admission(_ev(**over))
        assert r["outcome"] == ADM_INVALID and r["shadow_eligible"] is False, over


def test_admission_input_audit_schema_compat_promotable_only():
    # the real run_audit output — declared_present optional; promotable is the decisive field
    r = evaluate_shadow_admission(_ev(input_audit={"promotable": True}))
    assert r["outcome"] == ELIGIBLE_FOR_SHADOW
    real_audit = {"kind": "input_audit", "strategy": "H-005", "declared": ["close"],
                  "prepared": ["open_time", "open", "high", "low", "close", "volume"],
                  "read": ["close"], "central": [], "deviations": {}, "declared_present": True,
                  "promotable": True}
    assert evaluate_shadow_admission(_ev(input_audit=real_audit))["outcome"] == ELIGIBLE_FOR_SHADOW


def test_admission_data_quality_gates():
    assert evaluate_shadow_admission(_ev(data_quality={"classification": "NOT_AVAILABLE",
                                    "all_required_checks_passed": False}))["outcome"] == ADM_INVALID
    r = evaluate_shadow_admission(_ev(data_quality={"classification": "PASS",
                                    "all_required_checks_passed": False}))
    assert r["outcome"] == ADM_INVALID and any("incomplete" in b for b in r["blocking_reasons"])


def test_admission_shadow_reasons():
    assert any("family DSR" in u for u in evaluate_shadow_admission(_ev(family_dsr=0.5))["unresolved_reasons"])
    assert any("too few trades" in u for u in evaluate_shadow_admission(_ev(trade_count=5))["unresolved_reasons"])
    r = evaluate_shadow_admission(_ev(target_control={"verdict": "inconclusive"}))
    assert r["outcome"] == ELIGIBLE_FOR_SHADOW and any("inconclusive" in u for u in r["unresolved_reasons"])


def test_admission_promote_requires_gate_and_family_dsr():
    assert evaluate_shadow_admission(_ev(promotion_gate={"decision": "PROMOTE"},
                                    family_dsr=0.97))["outcome"] == ELIGIBLE_FOR_PROMOTE
    r = evaluate_shadow_admission(_ev(promotion_gate={"decision": "PROMOTE"}, family_dsr=0.5))
    assert r["outcome"] == ELIGIBLE_FOR_SHADOW and r["promote_eligible"] is False


def test_admission_never_raises_on_malformed_evidence():
    for bad in [None, "x", 5, [], {"family_dsr": "abc"}, {"backtest": "no"}]:
        r = evaluate_shadow_admission(bad)
        assert isinstance(r, dict) and r["outcome"] == ADM_INVALID


# ===========================================================================
# 9. Side-effect-free verification
# ===========================================================================
def _snapshot(root):
    snap = {}
    for dp, _dn, fs in os.walk(root):
        for f in fs:
            p = os.path.join(dp, f)
            try:
                st = os.stat(p)
                snap[p] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
    return snap


def test_admission_is_side_effect_free(tmp_path):
    roots = [str(tmp_path)]
    if os.path.isdir("knowledge_base/research_safety"):
        roots.append("knowledge_base/research_safety")
    before = {r: _snapshot(r) for r in roots}
    ev = _ev(); ev_copy = copy.deepcopy(ev)
    for e in (ev, _ev(technical_failure=True), _ev(promotion_gate={"decision": "PROMOTE"}, family_dsr=0.97)):
        assert "started NOTHING" in evaluate_shadow_admission(e)["note"]
    after = {r: _snapshot(r) for r in roots}
    assert before == after and ev == ev_copy


def test_existing_promotion_gate_unchanged():
    from app.research_safety.promotion_gate import DEFAULT_THRESHOLDS, evaluate_promotion  # noqa: F401
    assert DEFAULT_THRESHOLDS == {"max_drawdown": 0.60, "min_trades": 30, "dsr": 0.95}


# ===========================================================================
# Final fail-closed gaps: strict anchor, asset identity, malformed policy
# ===========================================================================
def test_strict_anchor_valid_shift_and_malformed():
    good = {"mode": "continuous", "cadence_ms": DAY, "anchor_ms": BASE}
    r = validate_frame(_clean(), asset="BTC/USD", cadence_policy=good, require_gap_validation=True)
    assert next(c for c in r["checks"] if c["check"] == "cadence_grid_alignment")["status"] == PASS
    assert r["classification"] == PASS and r["counts_as_evidence"] is True
    shifted = (BASE + DAY // 2 + np.arange(6) * DAY).astype("int64")   # half-cadence shift
    rs = validate_frame(_frame(shifted), asset="BTC/USD", cadence_policy=good, require_gap_validation=True)
    assert next(c for c in rs["checks"] if c["check"] == "cadence_grid_alignment")["status"] == INVALID
    assert rs["classification"] == INVALID
    # malformed anchors under strict validation -> NA, not evidence (no permissive int())
    for bad in [float(BASE), True, "123", 10 ** 15, 1.5]:
        pol = {"mode": "continuous", "cadence_ms": DAY, "anchor_ms": bad}
        r = validate_frame(_clean(), asset="BTC/USD", cadence_policy=pol, require_gap_validation=True)
        g = next(c for c in r["checks"] if c["check"] == "cadence_grid_alignment")
        assert g["status"] == NOT_AVAILABLE and g["blocking"] is True, bad
        assert r["classification"] == NOT_AVAILABLE and r["counts_as_evidence"] is False, bad
        assert r["all_required_checks_passed"] is False, bad


def test_missing_anchor_under_strict_validation_not_available():
    r = validate_frame(_clean(), asset="BTC/USD", cadence_policy=CONT, require_gap_validation=True)
    g = next(c for c in r["checks"] if c["check"] == "cadence_grid_alignment")
    assert g["status"] == NOT_AVAILABLE and g["blocking"] is True
    assert r["classification"] == NOT_AVAILABLE and r["counts_as_evidence"] is False


def test_asset_identity_fail_closed():
    r = validate_frame(_clean(), asset=None, cadence_policy=CONT)
    assert r["classification"] == INVALID and r["counts_as_evidence"] is False
    assert next(c for c in r["checks"] if c["check"] == "asset_identity")["status"] == INVALID
    assert validate_frame(_clean(), asset="  ", cadence_policy=CONT)["classification"] == INVALID
    assert validate_frame(_clean(), asset=None, expected_asset="BTC/USD",
                          cadence_policy=CONT)["classification"] == INVALID
    assert validate_frame(_clean(), asset="ETH/USD", expected_asset="BTC/USD",
                          cadence_policy=CONT)["classification"] == INVALID
    assert validate_frame(_clean(), asset="BTC/USD", expected_asset="BTC/USD",
                          cadence_policy=CONT)["classification"] == PASS
    r2 = validate_frame(_clean(), asset=None, cadence_policy=CONT, asset_identity_policy=NOT_AVAILABLE)
    assert r2["classification"] == NOT_AVAILABLE and r2["counts_as_evidence"] is False


def test_malformed_cadence_policy_never_raises():
    bad_policies = [
        "daily", [1, 2], 123, 4.5,
        {"mode": "continuous", "cadence_ms": "86400000"},
        {"mode": "continuous", "cadence_ms": 1.5},
        {"mode": "continuous", "cadence_ms": True},
        {"mode": "sessions", "cadence_ms": DAY, "session_timezone": "+00:00",
         "holidays": [], "session_weekdays": "abc"},
        {"mode": "sessions", "cadence_ms": DAY, "session_timezone": "+00:00", "holidays": "notalist"},
        {"mode": "sessions", "cadence_ms": DAY, "session_timezone": "+00:00",
         "holidays": [], "session_weekdays": [9]},
        {"mode": "bogus", "cadence_ms": DAY},
        {"cadence_ms": DAY},                                  # missing mode
    ]
    for policy in bad_policies:
        r = validate_frame(_clean(), asset="BTC/USD", cadence_policy=policy)   # must not raise
        assert r["classification"] in (INVALID, NOT_AVAILABLE), policy
        assert r["counts_as_evidence"] is False, policy


def test_holiday_calendar_strict_iso_validation():
    # accepted: exact ISO YYYY-MM-DD dates (candles present -> weekends are the only gaps)
    for hol in (["2018-01-01"], ["2018-01-01", "2018-12-25"], []):
        r = validate_frame(_equity(2), asset="SPY", cadence_policy=_sessions(hol))
        assert r["counts_as_evidence"] is True, hol
    # rejected: non-string, impossible, non-canonical, datetime, or mixed valid/invalid ->
    # structured NOT_AVAILABLE, not evidence, never raises
    for hol in (["not-a-date"], ["2018-99-99"], [123], ["2018-01-01", None],
                ["2018-1-1"], ["2018/01/01"], [datetime(2018, 1, 1)],
                ["2018-01-01", "2018-99-99"]):
        r = validate_frame(_equity(2), asset="SPY", cadence_policy=_sessions(hol))
        assert r["classification"] in (INVALID, NOT_AVAILABLE), hol
        assert r["counts_as_evidence"] is False, hol
        gap = next(c for c in r["checks"] if c["check"] == "missing_candles")
        assert gap["status"] == NOT_AVAILABLE, hol
