"""Regression coverage for the batch-diagnostics integration."""
from __future__ import annotations

import os

from app.research_safety.batch import content_trial_id, run_safety_batch
from app.research_safety.input_audit import run_audit
from app.research_safety.promotion_gate import evaluate_promotion
from app.research_safety.target_control import compare
from app.strategy_research.providers import DataRequest, get_provider
from app.strategy_research.strategies import available_strategies, get_strategy


PK = {"root": "data_csv"}
CLK = lambda: "2026-07-23T00:00:00Z"


def _btc():
    return get_provider("csv", **PK).get(
        DataRequest(asset="BTC/USD", timeframe="1d")
    ).df


def test_every_registered_h_strategy_has_accurate_declared_inputs():
    df = _btc()
    ids = [sid for sid in available_strategies() if sid.startswith("H-")]
    assert len(ids) == 28
    for sid in ids:
        audit = run_audit(get_strategy(sid), df)
        assert audit["declared_present"] is True, sid
        assert audit["promotable"] is True, (sid, audit["deviations"])


def test_control_exposes_units_tolerance_settings_and_reason():
    rec = compare("H-020", "H-020", ["BTC/USD"], provider_kwargs=PK)
    assert rec["verdict"] == "inconclusive"
    assert rec["sharpe_unit"] == "per_bar"
    assert rec["sharpe_tolerance"] == 0.05
    assert rec["settings"]["cost_bps"] == 5.0
    assert "within" in rec["verdict_reason"]


def test_inconclusive_control_is_not_available_not_fail():
    rep = evaluate_promotion("H-020", ["BTC/USD"], control_id="H-020",
                             provider_kwargs=PK, write=False)
    gate = next(g for g in rep["gates"] if g["gate"] == "target_control")
    assert gate["status"] == "NOT_AVAILABLE"


def test_batch_reports_sharpe_units_eligibility_and_fixed_family_dsr(tmp_path):
    rep = run_safety_batch(["BTC/USD"], provider="csv", provider_kwargs=PK,
                           root=str(tmp_path), strategies=["H-002", "H-005"],
                           clock=CLK, write=False)
    assert rep["final_dsr_trial_count"] == rep["ledger"]["effective_dsr_trials"]
    assert rep["final_raw_dsr_trial_count"] == rep["ledger"]["raw_dsr_trials"]
    assert rep["family_sharpe_population_count"] == rep["final_dsr_trial_count"]
    for row in rep["strategies"]:
        assert row["sharpe_unit"] == "per_bar"
        assert row["sharpe_annual"] is not None
        assert row["bar_frequency"] == "1d"
        assert row["return_aggregation"]
        assert row["family_dsr"] is not None
        dsr_gate = next(g for g in row["promotion_gates"]
                        if g["gate"] == "dsr_overfitting")
        assert dsr_gate["measured"]["source"] == "trial_ledger"
        assert dsr_gate["measured"]["family_dsr"] == round(row["family_dsr"], 4)
        assert row["promotion_eligible"] == (row["status"] == "Supported")


def test_trial_identity_changes_with_params_and_provider():
    common = ("H-025", ["BTC/USD"], "1d", "srp-matrix-v2", 5.0, 5.0)
    a = content_trial_id(*common, params={"length": 10}, provider="csv")
    b = content_trial_id(*common, params={"length": 20}, provider="csv")
    c = content_trial_id(*common, params={"length": 10}, provider="http")
    assert len({a, b, c}) == 3


def test_new_report_does_not_rewrite_older_timestamped_evidence(tmp_path):
    batches = tmp_path / "research_safety" / "batches"
    batches.mkdir(parents=True)
    old = batches / "batch-20260722T125906Z.json"
    old.write_text("original evidence", encoding="utf-8")
    run_safety_batch(["BTC/USD"], provider="csv", provider_kwargs=PK,
                     root=str(tmp_path), strategies=["H-005"], clock=CLK, write=True)
    assert old.read_text(encoding="utf-8") == "original evidence"
    assert os.path.exists(batches / "batch-20260723T000000Z.json")


# --------------------------------------------------------------------------- #
# Follow-up regression coverage: explicit Sharpe-unit metadata, eligibility /
# exclusion_reason separation, control settings-match, undeclared-input audit
# detection, and idempotent family_dsr. All additive; no gate is weakened.
# --------------------------------------------------------------------------- #
def test_sharpe_metadata_has_explicit_named_fields_and_verified_daily_cadence(tmp_path):
    rep = run_safety_batch(["BTC/USD"], provider="csv", provider_kwargs=PK,
                           root=str(tmp_path), strategies=["H-005"], clock=CLK, write=False)
    sm = rep["strategies"][0]["sharpe_metadata"]
    for key in ("raw_sharpe", "annualized_sharpe", "bars_per_year", "bar_frequency",
                "start", "end", "observation_count"):
        assert key in sm, key
    assert sm["bar_frequency"] == "1d"
    assert sm["cadence_verified_daily"] is True          # bars proven daily, not assumed
    assert sm["bars_per_year"] == 365.0                  # calendar-day, never sqrt(252)
    assert sm["observation_count"] > 0
    assert sm["raw_sharpe"] == rep["strategies"][0]["oos_sharpe"]   # existing value preserved


def test_non_supported_is_ineligible_and_never_promotes(tmp_path):
    rep = run_safety_batch(["BTC/USD"], provider="csv", provider_kwargs=PK,
                           root=str(tmp_path), strategies=["H-002", "H-005", "H-020"],
                           clock=CLK, write=False)
    for row in rep["strategies"]:
        # eligibility is reported SEPARATELY from evaluation status
        assert row["promotion_eligibility"] in {"eligible", "conditional", "ineligible"}
        assert row["promotion_eligible"] == (row["status"] == "Supported")
        if row["status"] != "Supported":
            assert row["promotion_decision"] != "PROMOTE"     # never PASS
        if row["promotion_eligibility"] == "ineligible":
            assert row["exclusion_reason"]                    # explicit reason present
        if row["status"] == "Not Supported":
            assert row["promotion_eligibility"] == "ineligible"
            assert "not_supported" in row["exclusion_reason"]
        if row["status"] == "Graveyard":
            assert row["promotion_eligibility"] == "ineligible"


def test_control_settings_match_is_verified(tmp_path):
    rep = run_safety_batch(["BTC/USD", "ETH/USD"], provider="csv", provider_kwargs=PK,
                           root=str(tmp_path), strategies=["H-005"], clock=CLK, write=False)
    match = rep["strategies"][0]["control_settings_match"]
    assert match is not None and match["all_match"] is True
    for key in ("assets", "timeframe", "bar_frequency", "matrix", "cost_bps", "slippage_bps"):
        assert match[key] is True, key


def test_audit_detects_missing_and_undeclared_inputs():
    df = _btc()
    strat = get_strategy("H-005")                 # actually reads close/high/low
    # (a) declaring nothing -> the audit cannot pass (the original all-27 failure mode,
    #     when declared_inputs was None on every strategy)
    strat.declared_inputs = None
    none_declared = run_audit(strat, df, declared=None)
    assert none_declared["declared_present"] is False
    assert none_declared["promotable"] is False
    strat = get_strategy("H-005")                 # fresh instance for part (b)
    # (b) under-declaring -> used_not_declared is flagged and it is not promotable
    under = run_audit(strat, df, declared={"open"})
    assert under["promotable"] is False
    assert set(under["deviations"]["used_not_declared"]) >= {"close", "high", "low"}


def test_family_dsr_is_idempotent_across_identical_reruns(tmp_path):
    kwargs = dict(provider="csv", provider_kwargs=PK, root=str(tmp_path),
                  strategies=["H-002", "H-005"], clock=CLK, write=False)
    a = run_safety_batch(["BTC/USD"], **kwargs)
    from app.research_safety.trial_ledger import TrialLedger
    n1 = TrialLedger(root=str(tmp_path)).count_effective_dsr_trials()
    b = run_safety_batch(["BTC/USD"], **kwargs)
    n2 = TrialLedger(root=str(tmp_path)).count_effective_dsr_trials()
    assert n1 == n2                                            # no duplicate trials
    assert a["final_dsr_trial_count"] == b["final_dsr_trial_count"]
    fa = {r["strategy_id"]: r["family_dsr"] for r in a["strategies"]}
    fb = {r["strategy_id"]: r["family_dsr"] for r in b["strategies"]}
    assert fa == fb                                           # identical DSR values
