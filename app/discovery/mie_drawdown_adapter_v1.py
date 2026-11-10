"""Opt-in diagnostic over newly captured MIE OOS returns; no evidence writes."""
from __future__ import annotations

import numpy as np

from app.strategy_research.evaluation.pipeline import EvaluationReport, evaluate
from .drawdown_risk_v1 import drawdown_report


def report_from_evaluation(evaluation: EvaluationReport, *, assets, weights=None,
                           allow_synthetic=False, **risk_options):
    """Accept a live report with captured arrays, not a historical summary.

    Caller chooses assets and bootstrap settings. Multiple assets require exact
    timestamp equality: no silent intersection, filling or concatenation.
    """
    if evaluation.strategy_id.upper() == "H-025":
        raise ValueError("H-025 is excluded from this diagnostic")
    if evaluation.is_real is not True and not (allow_synthetic is True and evaluation.is_real is False):
        raise ValueError("Real data required; synthetic examples need explicit opt-in")
    if not assets or len(assets) != len(set(assets)):
        raise ValueError("Select a nonempty, unique ordered asset list")
    if not evaluation.request_hash or not evaluation.matrix_hash or not evaluation.matrix_version:
        raise ValueError("Missing evaluation identity")
    rows = evaluation.risk_inputs
    by_asset = {row["asset"]: row for row in rows}
    if len(by_asset) != len(rows):
        raise ValueError("Duplicate captured asset")
    if any(asset not in by_asset for asset in assets):
        raise ValueError("Missing captured OOS arrays; historical summaries cannot be reconstructed")
    chosen = [by_asset[asset] for asset in assets]
    head = evaluation.headline
    for row in chosen:
        if (row["request_hash"] != evaluation.request_hash
                or row["matrix_hash"] != evaluation.matrix_hash
                or row["cost_bps"] != head.get("cost_bps")
                or row["slippage_bps"] != head.get("slippage_bps")
                or row["timeframe"] != head.get("bar_frequency")):
            raise ValueError("Captured OOS identity or cost scenario conflicts with report")
        if len(row["returns"]) != len(row["times"]):
            raise ValueError("Captured returns and timestamps differ in length")
        if not np.array_equal(row["times"], chosen[0]["times"]):
            raise ValueError("Asset OOS calendars differ; explicit alignment policy required")
    result = drawdown_report(
        np.column_stack([row["returns"] for row in chosen]),
        times=chosen[0]["times"], weights=weights,
        provenance={
            "strategy": evaluation.strategy_id,
            "matrix": f"{evaluation.matrix_version}:{evaluation.matrix_hash}",
            "dataset": "captured OOS arrays identified by input_sha256; not an upstream dataset manifest",
            "costs": f"net; cost_bps={head['cost_bps']}; slippage_bps={head['slippage_bps']}",
        }, **risk_options,
    )
    result["mie_source"] = {
        "adapter_version": "mie-drawdown-adapter-v1", "request_hash": evaluation.request_hash,
        "matrix_hash": evaluation.matrix_hash, "provider": evaluation.provider,
        "is_real": evaluation.is_real, "assets_in_column_order": list(assets),
        "bar_frequency": head["bar_frequency"], "original_verdict": evaluation.verdict,
        "original_dsr": head.get("dsr"),
    }
    if evaluation.is_real is False:
        result["status"] = "illustrative_only"
    result["limitations"].append(
        "MIE source.is_real is a provider flag, not proof of independent holdout or upstream data integrity."
    )
    return result


def evaluate_with_drawdown(request, matrix, *, risk_options, provider_kwargs=None):
    """Explicit new evaluation + diagnostic; returns both, persists neither.

    This executes a strategy evaluation when called. It never invokes the
    orchestrator, records a result, or updates discovery/DSR trial ledgers.
    Caller must authorize/register any real market trial before calling.
    """
    evaluation = evaluate(request, matrix, provider_kwargs=provider_kwargs,
                          capture_risk_inputs=True)
    return evaluation, report_from_evaluation(evaluation, **risk_options)
