# Random-timing trade matching: matrix v3

## Scope and provenance

User work package: verify and correct the random-timing benchmark's trade matching.
This is a methodology change, not a behavior-preserving correction to v1/v2.
Implementation is isolated on `codex/random-timing-trade-match-v3`, based on
`origin/main` commit `b683163`. The Phase 6/AWS work is separate and paused.

| Matrix | Hash | Random-timing method | Pooling |
|---|---|---|---|
| `srp-matrix-v1` | `a740f3acbf6572d6` | Legacy Bernoulli redraw | Concatenation |
| `srp-matrix-v2` | `4c2aa61eccfd276c` | Legacy Bernoulli redraw | Per asset |
| `srp-matrix-v3` | `3b838b31e151d7d1` | `executed-runs-v1` | Per asset |

The matrix version binds the method; no field was added to v1/v2's canonical
serialization. Their hashes and executable benchmark behavior remain unchanged.
`DEFAULT_MATRIX` remains v1; Research Core's active/default and `latest` alias,
and existing CLI defaults, remain v2. **Explicitly select `--matrix v3` for the
corrected comparison.** Adding this version does not migrate existing callers,
activate an experiment, rerun the corpus, or alter H-025.

## What is matched

The target is `run_backtest(...).metrics["num_trades"]`: the number of maximal
runs of constant **nonzero executed position**. A still-open final run counts.
A flat-to-long entry starts a trade; a long-to-short reversal starts another;
a long-to-flat exit alone does not. This is neither order count nor turnover.

The engine executes `signal[t-1]` at bar `t`, with bar 0 flat. For `n` input bars,
only `m = n-1` signal slots execute. The unused final signal repeats the previous
signal and cannot add a trade. There is no artificial terminal liquidation.

For every asset at the headline cost/slippage, v3 takes the pipeline's existing
reported OOS `num_trades`, including zero, and requires **every random seed** to
produce exactly that count under the actual backtest engine. A disagreement
raises before a benchmark report can be returned. The old pipeline's forced
minimum target of one remains only in v1/v2.

This target is the existing count obtained by shifting concatenated OOS signals.
The walk-forward engine slices returns from full-frame parameter backtests;
correcting execution/cost accounting at fold boundaries is outside this package.
Exact benchmark matching does not audit or correct those fold boundaries.

## Frozen sampling rule

Each sample uses `numpy.random.Generator(numpy.random.PCG64(base_seed + k))` for
`k = 0 .. n_seeds-1`. The sampler reads length, target count and direction, not
prices. It samples a complete historical null path, not a prospective strategy.

- **Long/short (`long_short`), positive target T:** choose the initial sign uniformly
  from -1/+1, then choose `T-1` distinct reversal boundaries uniformly without
  replacement from `1 .. m-1`. Alternate signs. This is uniform over the
  `2 * C(m-1, T-1)` fully exposed sign paths with T runs.
- **Long/cash (`long_only`), positive target T:** choose `2T` distinct boundaries
  uniformly without replacement from `0 .. m`, sort them, and hold long over each
  consecutive pair's half-open interval. Hold cash elsewhere. This is a bijection
  with the `C(m+1, 2T)` binary paths containing T long runs; each run and each
  intervening cash gap has at least one slot. Leading/trailing cash are optional.
- **T = 0:** all cash for both directions. Every requested seed is retained and
  has zero Sharpe, return, drawdown, turnover and exposure.

Feasible targets are `0 .. m` for long/short and `0 .. ceil(m/2)` for long/cash.
Empty frames are rejected. A one-bar frame permits only zero trades. Fractional,
boolean or negative counts, nonpositive seed counts, negative/noninteger seeds,
unsupported directions and infeasible targets raise; none are silently clamped.
The default seed count/base remain 25/12345. Runtime/library versions should be
retained with any future research run; the fixed examples below used Python 3.14,
NumPy 2.5.1 and pandas 3.0.3.

## Costs and statistical limits

Only trade count is matched. Time in market, long/short balance, holding durations,
turnover and total cost are not conditioned on the strategy. For T > 0:

- Long/short is exposed in every executable slot and has turnover `2T-1`.
- Long/cash has variable exposure and turnover `2T` when ending flat, or `2T-1`
  when its final run remains open.

The same engine and fee/slippage rates apply, but equal trade counts do not imply
equal cost bills. The per-seed activity diagnostics make that distinction visible.
The `p95` is the empirical 95th percentile of the sampled Sharpe values, using the
existing NumPy percentile convention. It is not a confidence interval or proof
that a strategy has or lacks timing skill. Existing across-asset averaging of
these percentiles and verdict thresholds are unchanged.

The review's separate findings about `sharpe_ci95` being an asset range in v2,
and the overstrong lookahead guarantee, remain unresolved. V3 inherits the
existing per-asset aggregation and report field semantics; this work does not
validate those claims or establish profitability.

## Fixed synthetic examples

The regression uses 10,000 bars, `close[t] = 100 + 0.001*t + sin(t/7)`,
`open_time[t] = t`, target 100, fee/slippage 5/5 bps and seeds 12345 through 12444.
The trade counts use the engine, not the sampler's own counter.

| Direction | Legacy mean / min / max | V3 mean / min / max |
|---|---|---|
| Long/short | 50.95 / 33 / 69 | 100 / 100 / 100 |
| Long/cash | 25.47 / 17 / 35 | 100 / 100 / 100 |

V3 long/short turnover is 199 for every seed and exposure is 9,999/9,999 slots.
Long/cash turnover ranges from 199 to 200 (mean 199.98); exposed slots range from
4,148 to 5,707 (mean 4,953.18). These values illustrate why the metric is explicitly
trade count rather than equal turnover/exposure.

A smaller fixed example uses n=8, target=3, seed=7:

| Direction | Signal vector (last element unused) | Executed vector | Trades | Turnover |
|---|---|---|---:|---:|
| Long/short | `[1,1,1,1,-1,1,1,1]` | `[0,1,1,1,1,-1,1,1]` | 3 | 5 |
| Long/cash | `[0,0,1,0,1,0,1,1]` | `[0,0,0,1,0,1,0,1]` | 3 | 5 |

With constant prices and fee/slippage 5/5 bps, the sum of net per-bar returns in
these examples is -0.005 (5 turnover units times 0.001); this is not the compounded
portfolio return. Changing the last signal has no effect on trades or costs.

Reproduce the diagnostic and integration tests without creating corpus evidence:

```sh
python -m pytest tests/test_random_timing_v3.py -q -s -p no:cacheprovider
```

## Production path and persistence

The implemented path is:

`versioning.resolve("v3")` / CLI `--matrix v3` → `evaluate` →
`random_timing_trade_matched` → actual `run_backtest` verification →
`headline.random_timing` → versioned result JSON and dossier rendering.

Both directions of a v3 request/matrix mismatch are rejected before data loading.
The standalone CLI rejects unknown matrix names instead of falling back to v1.

For each asset, `headline.random_timing.per_asset` records method, matched metric,
target, direction, bar/slot counts, RNG, seed identities, actual trade counts,
turnover, exposed bars and exposure fraction. The fraction's denominator is
`executable_slots`; it is zero when there are no executable slots. Arrays follow
seed order. `write_results` retains this same metadata in the result archive.

A caller explicitly using `--write` retains the existing writer semantics:
`<id>.srp-matrix-v3.json` is separate from the v1/v2 archives; the unversioned
`<id>.json` latest pointer is updated. Repeated writes of the same matrix version
still replace that version's record. This writer is not an append-only evidence
store. Dossier v3 sections are separate from v1/v2; INDEX labels now say v3.
No real corpus publication or historical rerun was performed for this package.

## Validation and storage ownership

Final local verification: **81 passed, 1 deselected** across
`test_random_timing_v3.py`, `test_strategy_research.py` and `test_platform.py`,
using `-k 'not h025'`. There were no failures or skips; the existing H-025 test
was deliberately excluded. Independent review separately ran the **49 new tests**
and passed all 49. These are overlapping runs, not 130 distinct tests.
`git diff --check` also passed. No full repository test-suite or real-market
validation is claimed.

All newly executed evaluation/persistence tests use `TEST-BENCHMARK` and pytest
owned temporary storage. This is disposable implementation validation, not a
strategy experiment, production verification or H-025 evidence.

- The original regression failed for both directions with exactly the reported
  50.95 and 25.47 means; it passes for v3 with exactly 100 trades per seed.
- Exhaustive short-path enumeration verifies a bijection with the intended path
  families, using the real engine to filter candidate paths.
- Boundary, zero-trade, invalid-input, repeatability, price-independence, index,
  lag, cost and fail-closed disagreement tests cover the sampler/engine contract.
- Actual pipeline tests cover both directions and zero targets. Orchestrator →
  temporary Knowledge Base integration verifies v3 diagnostics survive writing
  and that old v1/v2 archives and dossier sections remain byte-identical.
- `tests/fixtures/random_timing_legacy_reports.json` is a synthetic prechange
  fixture captured from unchanged scientific code at P2 commit `d542f51` (identical
  scientific files to base `b683163`). Its test fixes the legacy provider's
  process-salted hash hook to asset seeds 101/202. The provider itself is unchanged.
  Full v1/v2 report keys, labels and values are compared against this fixture.
- Dedicated independent review checked the sampling mathematics, callsite,
  version boundary and persistence. Its reverse-version mismatch finding was
  corrected and covered before final verification.

No Phase 6 infrastructure, broker connection, H-025 artifact, strategy, threshold,
backtest engine, walk-forward implementation or real historical result is changed.
