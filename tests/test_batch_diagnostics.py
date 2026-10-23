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
    assert len(ids) == 29  # H-002..H-030 (H-030 pre-registered 2026-10-23)
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
