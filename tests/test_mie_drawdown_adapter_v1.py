"""Only synthetic fixtures; never run a real market trial or write evidence."""
from copy import deepcopy

import pytest

from app.discovery.mie_drawdown_adapter_v1 import report_from_evaluation, evaluate_with_drawdown
from app.strategy_research.evaluation.pipeline import EvaluationReport, evaluate
from app.strategy_research.evaluation.frozen import EvalRequest, MATRIX_V2


def fixture():
    row = {"asset": "FIXTURE", "returns": [.01, -.02, .03, -.01],
           "times": [1, 2, 3, 4], "timeframe": "1d", "cost_bps": 5,
           "slippage_bps": 5, "request_hash": "request", "matrix_hash": "matrix"}
    return EvaluationReport(
        strategy_id="G-FIXTURE", strategy_name="fixture", strategy_class="generated",
        provider="synthetic", is_real=False, request_hash="request", matrix_hash="matrix",
        matrix_version="fixture-v2", assets_evaluated=["FIXTURE"],
        headline={"cost_bps": 5, "slippage_bps": 5, "bar_frequency": "1d", "dsr": .1},
        risk_inputs=[row])


def diagnostic(rep, **kwargs):
    options = dict(assets=["FIXTURE"], block_lengths=[2], simulations=30, allow_synthetic=True)
    options.update(kwargs)
    return report_from_evaluation(rep, **options)


def test_report_is_bound_and_original_unchanged():
    rep = fixture()
    before = deepcopy(rep)
    out = diagnostic(rep)
    assert out["status"] == "illustrative_only"
    assert out["mie_source"]["request_hash"] == "request"
    assert out["mie_source"]["original_dsr"] == .1
    assert out["mie_source"]["assets_in_column_order"] == ["FIXTURE"]
    assert rep == before
    assert "risk_inputs" not in rep.to_dict()


@pytest.mark.parametrize("key,value", [("request_hash", "other"), ("matrix_hash", "other"),
                                       ("cost_bps", 0), ("slippage_bps", 0),
                                       ("timeframe", "1h"), ("times", [1, 2])])
def test_source_conflicts_rejected(key, value):
    rep = fixture()
    rep.risk_inputs[0][key] = value
    with pytest.raises(ValueError):
        diagnostic(rep)


def test_summary_without_arrays_rejected():
    rep = fixture()
    rep.risk_inputs = []
    with pytest.raises(ValueError, match="Missing captured"):
        diagnostic(rep)


def test_synthetic_requires_explicit_opt_in():
    with pytest.raises(ValueError, match="Real data required"):
        diagnostic(fixture(), allow_synthetic=False)


def test_h025_excluded():
    rep = fixture()
    rep.strategy_id = "H-025"
    with pytest.raises(ValueError, match="H-025"):
        diagnostic(rep)
    request = EvalRequest("H-025", "synthetic", ["FIXTURE"], "1d")
    with pytest.raises(ValueError, match="H-025"):
        evaluate(request, MATRIX_V2, capture_risk_inputs=True)


def test_misaligned_assets_are_not_silently_intersected():
    rep = fixture()
    second = deepcopy(rep.risk_inputs[0])
    second.update(asset="OTHER", times=[2, 3, 4, 5])
    rep.risk_inputs.append(second)
    with pytest.raises(ValueError, match="calendars differ"):
        diagnostic(rep, assets=["FIXTURE", "OTHER"], weights=[.5, .5])


def test_actual_pipeline_to_adapter_with_synthetic_provider():
    request = EvalRequest("H-022", "synthetic", ["BTC/USD"], "1d", limit=300,
                          matrix_version=MATRIX_V2.version)
    original = evaluate(request, MATRIX_V2)
    rep, risk = evaluate_with_drawdown(
        request, MATRIX_V2,
        risk_options={"assets": ["BTC/USD"], "block_lengths": [5, 10],
                      "simulations": 50, "allow_synthetic": True})
    assert original.risk_inputs == []
    assert rep.to_dict() == original.to_dict()
    assert risk["status"] == "illustrative_only"
    assert risk["observed_periods"] == len(rep.family_dsr_inputs[0]["returns"])
    assert rep.risk_inputs[0]["returns"] == rep.family_dsr_inputs[0]["returns"]
    assert risk["mie_source"]["matrix_hash"] == MATRIX_V2.hash()
    assert risk["mie_source"]["request_hash"] == request.hash()
    assert len(risk["scenarios"]) == 2
