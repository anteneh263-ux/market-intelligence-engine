# Sharpe dispersion, sampling uncertainty and causal-input limits

## Scope and version boundary

This package implements the user's next two review tasks: stop labeling an
across-asset min–max as a 95% confidence interval, then narrow the lookahead claim
and check the registered strategy universe's causal-input behavior.

`srp-matrix-v4` (hash `0ee7bc22dac18f76`) inherits v3's exact trade-count benchmark
and v2's per-asset pooling. It changes result semantics, not the evaluated signals,
returns, verdict thresholds or benchmark. Use explicit `--matrix v4`.

V1–v3 matrix hashes, evaluation outputs and existing defaults remain unchanged.
The legacy `sharpe_ci95` field stays in legacy evaluation output for reproduction;
**it must not be interpreted as a confidence interval in v2/v3**. Current rendering
correctly labels its asset range without rewriting stored historical records.
The standalone CLI and Research Core resolve v4 explicitly; symmetric
request/matrix identity checks prevent v4 requests from silently using older code.

This branch builds on draft PR14, commit `9c6a5dc`; the follow-up PR is separately
reviewable against that branch. It does not merge PR14 or change Phase 6/AWS work.

## Distinct statistical meanings

The headline in v2–v4 is an equal-weight arithmetic mean of **per-asset** Sharpe
estimates. It is not the Sharpe of an aggregate investable portfolio.

| Quantity | Meaning | V4 representation |
|---|---|---|
| Across-asset dispersion | Smallest/largest per-asset Sharpe at the headline cost | `sharpe_asset_range` |
| Number of contributing assets | Assets with a headline result | `sharpe_asset_count` |
| Sampling uncertainty | Uncertainty of an estimated quantity under a stated sampling model | `sharpe_uncertainty`, explicitly not estimated |

All range endpoints are per-bar Sharpe, consistent with `sharpe_unit="per_bar"`.
No calibrated sampling-uncertainty estimator is introduced in this package.
Specifying and validating one requires its own assumptions and research contract.

A v4 headline contains no `sharpe_ci95`. It includes:

```json
{
  "sharpe_asset_range": [-0.2, 0.3],
  "sharpe_asset_count": 2,
  "sharpe_uncertainty": {
    "status": "not_estimated",
    "interval": null,
    "confidence_level": null,
    "method": null,
    "reason": "No sampling uncertainty estimator is specified for the equal-weight mean of per-asset Sharpe. Asset min/max is descriptive."
  }
}
```

The endpoints above illustrate the schema; they are not a measured strategy result.
With **one asset**, the descriptive range is `[x,x]` and count is one, while
sampling uncertainty remains unestimated. Equal endpoints do not mean certainty.
With **no contributing assets**, the range is null and count is zero; uncertainty
remains unestimated. Neither case fabricates a `[0,0]` confidence interval.

Legacy v1 does compute a percentile interval using IID resampling of its pooled
returns. Its current display explicitly says **nominal 95% IID-bootstrap interval**
and states that serial-dependence and post-selection coverage are not established.
The v1 insufficient-data `[0,0]` fallback is displayed as unavailable when fewer
than two observations exist. This package does not repair or validate v1's
pooling, uncertainty coverage or statistical assumptions.

## Actual path and persistence

`versioning.resolve("v4")` / `--matrix v4` → `pipeline.evaluate` → v4 headline →
`render_step5_markdown` / `write_results` → separate v4 archive.

The three new fields survive both full-report serialization and the actual
Orchestrator → Knowledge Base → results writer path. Tests use a temporary KB
owned by the test, with the synthetic `TEST-UNCERTAINTY` identity. Existing v1–v3
archives remain byte-identical. Explicit writing retains the existing latest
pointer and same-version replacement semantics; it is not an append-only store.
No real corpus result was generated, relabeled, replaced or migrated here.

Current rendering of v2/v3 calls the values a descriptive range across assets and
says sampling uncertainty was not estimated. The renderer does not mutate its
input report. Old saved Markdown dossiers are not silently regenerated.

## The precise timing claim

A signal decided at close t first affects the engine's return at t+1. The one-bar
lag prevents the signal from affecting the same bar's return. **It does not inspect
or validate how that signal was computed.** A signal reading future prices can
still leak through the backtest function.

The negative-control test deliberately uses the next price's direction. With
zero costs, after the execution lag it obtains 849 positive and zero negative
return periods on an 850-bar synthetic fixture. This demonstrates the engine's
boundary; it is not evidence that a registered strategy leaks.

Full-frame precomputation in walk-forward evaluation likewise requires causal
signals. Prefix invariance and future-only perturbation are finite regression
checks. They cannot establish causality for every possible input or prove that
market data was available at the historical decision time. Revisions, release
lags, feed availability, parameter selection and execution accounting require
separate controls. Existing later safety gates are not replaced or strengthened
by merely adding these tests.

The README and backtest/walk-forward documentation state this bounded claim.
For v4, newly generated dossiers use `dossier-gen-v2`, whose input hash includes
that generator version and whose timing statement makes the same distinction.
V1–v3 still use `dossier-gen-v1` to preserve exact reproduction, and existing
dossiers are preserved by `ensure_dossier`.

Strategy source/spec files, including `strategies/base.py` and
`strategies/implementations_ext.py`, remain byte-identical because historical
manifests bind those source-file hashes. Some frozen comments and legacy generator
text therefore retain the old absolute wording. **That wording is superseded by
the bounded claim above and must not be used as a documented guarantee.** No
historical manifest or source identity is regenerated or repaired.

## Registry-derived causal-input coverage

`tests/test_registered_strategy_causality.py` obtains its test IDs from the actual
registry at collection rather than a manually selected four-strategy shortlist.
The verified built-in registry contains 28 strategies, H-002 through H-029.
For every one it checks:

- The default call and all registered grid entries: 28 defaults plus 59 grid
  configurations (some defaults duplicate grid entries).
- Two fixed PCG64 OHLCV fixtures, seeds 7 and 29, each 850 daily bars from 2020.
- Eighteen prefix cuts: 1, 2, 5, 10, 21, 199, 200, 252, 304, 305, 306, 399, 400,
  600, 761, 762, 763, 849. These include warmup and month-boundary neighborhoods.
- Both removal of future bars and valid but changed future OHLCV, leaving the
  prefix unchanged. Missing-value masks and finite values are compared separately.
- Fresh copied strategy objects, parameters and frames for each call, to avoid
  state/cache sharing masking a failure.

The suite additionally registers and tests one generated VOTE recipe in an
isolated temporary in-memory registry, and verifies that intentional future
reading is detected by truncation and by an opposite future price movement.
This is coverage of the collected built-ins plus that dynamic example, not a
universal certificate for arbitrary strategies registered later. New registrations
must undergo their own checks; registration itself does not run a causal gate.

H-025 is included **only as a synthetic source-level position-function unit test**
because the user requested all registered strategies. No H-025 market evaluation,
manifest, journal, receipt, registry, experiment or prospective artifact was
accessed or written. The older redundant H-025 unit was deselected from the
combined run; the new registry-derived cases cover its code for both fixtures.

## Executed validation and review

The initial interval tests failed three cases: single/multiple-asset v3 output
still used `sharpe_ci95`, and legacy rendering called the range a 95% CI. They now
pass against the explicit v4 contract and corrected legacy view.

The first causal run passed all registered-strategy cases but exposed a weak
negative control: a changed future price had retained the same direction. The
negative control now forces an opposite boundary move, demonstrating detection.
No strategy implementation was changed to make the tests pass.

Final targeted regression: **168 passed, 1 deselected, 0 failed, 0 skipped** in
11.20s across interval, registry causality, v3 benchmark, strategy research,
platform and dossier-generator tests. Independent review separately passed the
**81 new tests** (20 interval/compatibility and 61 causal-input tests) in 8.28s.
These runs overlap; they are not 249 distinct tests. No full-repository suite or
real-market validation is claimed.

```sh
python -m pytest tests/test_sharpe_uncertainty_v4.py \
  tests/test_registered_strategy_causality.py tests/test_random_timing_v3.py \
  tests/test_strategy_research.py tests/test_platform.py tests/test_dossier_generator.py \
  -k 'not test_h025_is_long_only_and_causal' -q -p no:cacheprovider
```

All new results are synthetic implementation validation in memory or disposable
pytest-owned temporary storage. This is not prospective evidence, profitability
validation, a Phase 6 pilot or permission to trade.
