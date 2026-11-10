# Drawdown risk diagnostic v1

This is a standalone, read-only diagnostic, not an anti-overfitting certificate
or promotion gate. It does not modify evaluation matrices, discovery learning,
historical verdicts or ledgers. No files are written and no market tests run.

## Input contract

Supply chronologically ordered, consecutive **out-of-sample net simple returns**
as fractions, numeric timestamps and explicit block lengths. Multi-asset input
uses rows for synchronized periods and columns for assets; weights are explicit,
nonnegative and sum to one. The portfolio is rebalanced to those weights each
period. Additional portfolio rebalancing costs are not inferred or deducted.
Use an already-net portfolio series when those costs are material.

Provide strategy, evaluation-matrix, dataset identity and cost assumptions in
`provenance`. Prefer immutable upstream identifiers/hashes. These are caller
declarations: the module cannot certify OOS independence, cost correctness,
calendar alignment or complete DSR trial accounting. Missing/nonfinite rows
are rejected rather than dropped. Timestamps must increase; the caller checks
missing periods and calendars. Input arrays receive their own SHA-256 digest.

DSR is not an input to the drawdown calculation. A headline DSR or aggregate
report cannot reconstruct the return series. The optional MIE adapter captures
arrays from a new evaluation in memory; it does not reconstruct persisted
historical summaries or independently certify their underlying data.

## Method and output

Noncircular moving-block bootstrap draws contiguous observed blocks, with
replacement, until the declared horizon is reached; the last block is truncated.
The same time blocks apply to every asset. Each block length has a deterministic
random stream derived from the seed and length. The report records the horizon,
simulation count, seed, weights, provenance and block-length scenarios.

Maximum drawdown includes initial equity of 1.0. A return of -100% produces
ruin (100% drawdown); smaller returns are rejected. Calculations use log equity
for numeric stability. Output includes observed drawdown and median, 95th and
99th percentiles of simulated maximum drawdown. An optional limit reports the
fraction **strictly exceeding** it, with the count and simulation denominator.
Quantiles use linear interpolation; all drawdowns are positive fractions.

Choose horizon, block lengths, costs and simulation budget before viewing
results. Use several justified block lengths for sensitivity, not to select
the most flattering result. A whole-history block at the observed horizon
reproduces the observed path and provides no simulation diversity.

Bootstrap paths are conditional scenarios, not fresh observations or guarantees
about future extremes. Dependence beyond the chosen block length is lost;
unobserved crises and changing market regimes are not invented. A separate
validation design is needed before any automated approval threshold or claim
that learning improves candidate quality.

## Example (synthetic only)

```python
from app.discovery.drawdown_risk_v1 import drawdown_report, render_report

report = drawdown_report(
    [0.01, -0.02, -0.01, 0.015, 0.005, -0.005],
    times=[0, 1, 2, 3, 4, 5],
    block_lengths=[2, 3], simulations=5000, seed=7,
    horizon=6, drawdown_limit=0.10,
    provenance={"strategy": "synthetic-example", "matrix": "fixture-v1",
                "dataset": "synthetic-six-periods", "costs": "net, zero costs"},
)
print(render_report(report))
```

The six-period example demonstrates the API, not adequate research sample size.
The caller owns any explicit export destination. There is no default evidence
root, CLI activation or integration into strategy selection.

## MIE integration: explicit in-memory capture

`evaluate(..., capture_risk_inputs=True)` now makes headline-cost OOS returns
and timestamps available as `EvaluationReport.risk_inputs`. Default capture is
off. This transient field is excluded from `to_dict()`, so the serialized
historical evaluation schema, matrix hashes, metrics and verdicts are unchanged.

`report_from_evaluation(report, assets=[...], block_lengths=[...], ...)` uses
those arrays. It checks request/matrix hashes, headline costs, timeframe, array
lengths and exact multi-asset timestamp equality. Asset order is recorded;
weights are required for multiple assets. No silent intersection, filling,
cross-asset concatenation or inference from aggregate metrics occurs.

The separate `mie_source` section retains the original DSR and verdict as source
context; neither is recalculated or used as a drawdown approval threshold.
`input_sha256` identifies supplied arrays/timestamps/weights, not the upstream
market-data file or a verified dataset manifest. The provider's `is_real` flag
does not prove unseen data or data integrity. Calendar completeness remains a
caller responsibility. Synthetic input requires `allow_synthetic=True` and
produces `illustrative_only` output. H-025 capture and diagnostics are excluded.

For a **separately authorized and registered new trial**, the convenience API is:

```python
from app.discovery.mie_drawdown_adapter_v1 import evaluate_with_drawdown

# request and matrix must come from the caller's authorized trial setup.
evaluation, risk = evaluate_with_drawdown(
    request, matrix,
    provider_kwargs=provider_kwargs,
    risk_options={"assets": ["BTC/USD"], "block_lengths": [5, 10, 20],
                  "simulations": 5000, "seed": 7},
)
```

This helper executes an evaluation when called. It persists neither the result
nor the diagnostic and does not register trials or update DSR ledgers. It is
not wired into CLI, discovery learning or automatic strategy selection. The
caller must govern any market trial and explicitly choose any export location.
Integration was tested using the actual evaluation path with a synthetic
provider, including equality of serialized evaluation output with capture off
and on. No new real-market evaluation was run for this change.
