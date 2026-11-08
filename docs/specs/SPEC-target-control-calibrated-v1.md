# SPEC: calibrated target-control test v1 (`target-control-test-v1`)

Status: **APPROVED 2026-11-04, frozen for final validation 2026-11-07** · written 2026-11-04 · branch `workspace/secure-2026-11-03` (base main `aa4376d`)
Round: "reliable evaluation", step 3 (see `MIE_STATUS_2026-10-23.md`).

> Research tooling only. This test compares a candidate with a simple control. It is not a causal claim,
> not a profitability claim and not a basis for trading.

## 1. Why

The current gate (`app/research_safety/target_control.py::compare`) compares two point estimates:
`candidate_wins` if the per-bar Sharpe difference exceeds a fixed tolerance of 0.05. It has no sampling
model. The 2026-10-23 calibration note found that in practice it needs roughly 1.0 in annual Sharpe
difference to approve, and that a fixed band in annual units would approve noise far too often.

Goal: a versioned statistical test whose error rates are measured before it is trusted, so that
MIE's "promising / rejected / too little data" means what it says.

## 2. What does not change

- `compare()` and its 0.05 rule, the promotion gate default, all matrix versions, all stored results and
  verdicts, H-025, holdout data and paper/forward state.
- The new test is additive and opt-in until the user decides, after seeing the final validation, whether
  it becomes the gate default. That decision is a separate, recorded change.
- No real strategy is evaluated as part of this work. Calibration uses synthetic data only.

## 3. Estimand

For each asset *a*, on the **identical out-of-sample bars** produced by the frozen walk-forward of the
active matrix (`srp-matrix-v4`: 5 splits, min_train 100), at the headline cost (5 bps fee + 5 bps
slippage per side):

    ΔSR_a = SR_ann(candidate net returns on a) − SR_ann(control net returns on a)
    ΔSR   = equal-weight mean of ΔSR_a over assets          (same pooling as v2–v4 headline)

`SR_ann` = mean / standard deviation of daily net returns × √252. **"0.5" in this spec always means
ΔSR = 0.5 annual net Sharpe of the candidate over the control**, never the candidate's own Sharpe.

## 4. Test

- Hypotheses: H0: ΔSR ≤ 0, H1: ΔSR > 0 (one-sided).
- Method: paired circular block bootstrap of daily net returns (after Ledoit & Wolf, 2008, *Robust
  performance hypothesis testing with the Sharpe ratio*, J. Empirical Finance 15(5)). The **same**
  resampled block indices are applied to candidate and control (preserves their correlation) and to all
  assets at once (preserves cross-asset correlation).
- Calendar: assets are aligned on common dates. For assets that trade on days outside the common calendar
  (crypto weekends), log returns are summed into the next common date. Locked.
- Locked parameters: block length 10 bars; B = 2 000 resamples; one-sided level 5 %.
- Decision on the bootstrap distribution of ΔSR:
  - `candidate_wins`: lower 5 % percentile > 0
  - `control_wins`: upper 95 % percentile < 0
  - `inconclusive`: otherwise
- Output records: method id, parameters, seed, B, point ΔSR, interval, per-asset ΔSR, number of common bars,
  matrix version and the line "control comparison only, not a causal claim".
- Block length and the percentile vs. studentized variant may be changed **only** on development seeds
  (§6). After that they are locked; any later change is a new version with new final seeds.

## 5. Calibration data (synthetic, locked before any run)

Shared asset model, matching the bundled data (`data_csv/`, common span about 8.5 years):

| Parameter | Value |
| --- | --- |
| Assets | 5 |
| Common daily bars | Layer B prices: 2 140 (≈ 8.5 years at 252/yr). Layer A test series: **1 710** (see note) |
| Annual volatility | 0.31, 0.65, 0.85, 0.19, 0.17 (AAPL, BTC, ETH, SPY, XAU in `data_csv`) |
| Cross-asset correlation | the observed matrix: AAPL–SPY 0.76, BTC–ETH 0.85, others 0.05–0.34 (exact values in code) |
| Innovations | Student-t, ν = 5, scaled to unit variance |
| Volatility clustering | GARCH(1,1), α = 0.08, β = 0.90, ω set to hit the annual volatility |
| Drift | none (no predictability in prices) |

*Amendment 2026-11-04, before any calibration run:* the approved draft used 2 140 bars for Layer A. That is
the full data span. The test only sees the walk-forward out-of-sample bars on the common calendar; a sanity
check of the control against itself on `data_csv` gave **1 710** such bars (2019-09-16 to 2026-07-07).
Layer A therefore uses 1 710 bars, so its detection rate is not overstated. Layer B is unchanged: it
simulates 2 140 price bars and lets the real walk-forward produce its out-of-sample span.

*Amendment 2026-11-04, development stage (Layer B only):* the first Layer B development run produced a
one-day synthetic ETH move above +100 %, so a short position lost more than its capital. Student-t shocks on a
GARCH(1,1) path with α + β = 0.98 occasionally explode. Layer B price paths therefore cap the daily log return at
±0.5 (+65 % / −39 %; the largest daily falls in `data_csv` are about −40 %). How often the cap binds is reported.
Layer A is unchanged. An earlier Layer B development run was also void because synthetic timestamps were written
in the wrong unit; it is kept, marked, and not used.

**Layer A — the statistical test itself.** Paired daily net-return series for candidate and control are
simulated directly per asset, with the asset model above for their common shock. Control annual Sharpe
0.30 (mean of the control on `data_csv`). Candidate Sharpe = control Sharpe + Δ.

| Scenario axis | Values |
| --- | --- |
| True Δ | **0 (negative control)**, 0.25, **0.5 (target)**, 1.0 |
| Candidate–control correlation ρ | 0.3, **0.5 (primary)**, 0.8 |

At Δ = 0 the two series have identical marginal distributions: a genuine null with zero edge.

**Layer B — end to end through MIE's own code.** Synthetic prices from the asset model; the real
`TargetOnlyControl` (EMA-50) and the real walk-forward and cost model.
- Null candidate: a strategy with no information (positions independent of future returns) and turnover
  matched to the control. Its true Δ is measured on one 400-year simulation and reported.
- Edge candidate: the control's position, replaced on a fraction *q* of bars by the sign of the next bar's
  return. *q* is set on one 400-year simulation (development seed) so that true Δ = 0.50 ± 0.02, then locked.
  The resulting ρ is reported.

## 6. Replications and seeds

| | Layer A, per scenario | Layer B, per scenario |
| --- | --- | --- |
| Development (may change §4 parameters) | 200 | 100 |
| Final validation (no changes allowed) | 2 000 | 500 |

- Development seeds start at 10 000. Final-validation seeds start at 900 000 and are used **once**, after
  this spec, the code and §4 are frozen and committed. The commit hash is recorded in the result file.
- Rates are reported with Wilson 95 % intervals.

## 7. Acceptance (final validation)

The test is accepted as `target-control-test-v1` if all of these hold:

1. **False approvals** at Δ = 0, for **every** ρ: rate ≤ 5.0 % and upper Wilson bound ≤ 6.0 %.
   Layer B null: rate ≤ 5.0 % (relative to its measured true Δ ≤ 0).
2. **Detection** at Δ = 0.5, ρ = 0.5: rate ≥ 80 % (Wilson interval reported).
3. Detection at all other Δ and ρ, and in Layer B, is **reported, not required**.

**If 2 fails while 1 holds**, the result is recorded as: "MIE cannot reliably detect a 0.5 edge over the
control with about 8.5 years of daily data on these 5 assets". The level is not loosened to make it pass.
Rough planning estimate before any simulation: detection between about 60 % and 90 %, depending on ρ and on
how much the correlated assets behave as one; the target is therefore uncertain.

**If 1 fails**, the test is not accepted. A revision is a new version with new final seeds.

## 8. Deliverables

- `app/research_safety/control_test.py`: `compare_calibrated(...)`, version id `target-control-test-v1`.
- `app/research_safety/calibration/` (or `scripts/`): the simulation and runner, deterministic from seeds.
- Tests: decision rule on constructed series, seed determinism, calendar alignment, unchanged `compare()`.
- `knowledge_base/research_safety/calibration/target-control-test-v1/`: development and final results (JSON)
  and a short report with every scenario, rate and interval.
- Promotion gate: opt-in `control_test="target-control-test-v1"`; default unchanged.

## 9. Known limits

- Synthetic data cannot represent every real regime; real returns may have breaks the model lacks.
- One control strategy; equal-weight pooling across assets as in v2–v4.
- The test compares a candidate with the control; it does not test absolute profitability, costs beyond the
  model, or capacity.
- Calibration validates this test on this data shape only. Another universe or history length needs its own
  calibration.

## 10. Out of scope

Evaluating real strategies, choosing a candidate (step 4), changing stored verdicts, H-025, holdout,
Phase 6 and AWS.
