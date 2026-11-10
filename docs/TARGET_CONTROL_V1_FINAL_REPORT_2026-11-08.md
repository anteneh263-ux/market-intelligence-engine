# Final validation: `target-control-test-v1` — NOT ACCEPTED

Date: 2026-11-08 · Code: commit `de942ed3487978a23f4a143629ea5d980e336bb7` (byte-identical files verified by SHA-256
before the run) · Spec: `docs/specs/SPEC-target-control-calibrated-v1.md` §5–§7 · Seeds: final base 900 000, used once.

> Synthetic calibration only. No real strategy was evaluated. Control comparison only, not a causal claim.

## Outcome

| Spec §7 criterion | Result | Met |
| --- | --- | --- |
| 1. False approvals at Δ = 0, every ρ: rate ≤ 5.0 % and upper Wilson ≤ 6.0 % | 5.15 % / 4.45 % / 5.75 % (ρ 0.3 / 0.5 / 0.8) | **No** (ρ 0.3 and 0.8) |
| 1b. Layer B null (true Δ −0.036): rate ≤ 5.0 % | 6.4 % | **No** |
| 2. Detection at Δ = 0.5, ρ = 0.5: rate ≥ 80 % | 65.4 % [63.3, 67.5] | **No** |

Per §7: the test is **not accepted**. The legacy gate (`target_control.compare`, 0.05 rule) stays the default; nothing
stored changes. A revision is a new version with new final seeds.

Per §7 (criterion 2): **MIE cannot reliably detect a 0.5 edge over the control with about 8.5 years of daily data on
these 5 assets.**

## Layer A — 2 000 replications per scenario, 1 710 common bars

Approval rate (`candidate_wins`), Wilson 95 % interval:

| True Δ | ρ 0.3 | ρ 0.5 | ρ 0.8 |
| --- | --- | --- | --- |
| 0.0 | 5.15 % [4.26, 6.21] | 4.45 % [3.63, 5.44] | 5.75 % [4.81, 6.86] |
| 0.25 | 22.4 % [20.6, 24.2] | 27.8 % [25.9, 29.8] | 47.0 % [44.8, 49.2] |
| 0.5 | 53.1 % [50.9, 55.3] | **65.4 % [63.3, 67.5]** | 93.0 % [91.7, 94.0] |
| 1.0 | 95.8 % [94.8, 96.6] | 99.2 % [98.6, 99.5] | 100 % [99.8, 100] |

Spread of the point estimate ΔSR across replications (SD): ≈ 0.30 at ρ 0.3, 0.25–0.27 at ρ 0.5, 0.16–0.18 at ρ 0.8.
Mean point ΔSR matches the true Δ within 0.05 in every scenario (no bias in the estimator).

## Layer B — 500 replications per candidate, end to end through MIE's walk-forward

| Candidate | True Δ (400-year run, seed 4242) | Approved | Wilson 95 % |
| --- | --- | --- | --- |
| Null (lag-20 control, no information) | −0.036 | 6.4 % (32/500) | [4.6, 8.9] |
| Edge (control + next-bar sign on q = 0.0531 of bars) | +0.504 | 100 % (500/500) | [99.2, 100] |

The edge candidate is almost the control itself (SD of point ΔSR 0.05), so its 100 % says little about the ρ 0.5 case.
`q` and the true Δ values were set on the development seed and re-used (`--truth dev/layer_B.json`), as §5 requires.

## Reading

- **Size.** Close to nominal but slightly liberal, and worse the more the candidate resembles the control
  (4.5 % → 5.8 % in Layer A as ρ rises; 6.4 % in Layer B where the candidate is a near copy). Consistent with the
  known finite-sample over-rejection of percentile bootstrap intervals for Sharpe differences. A v2 should address
  this on development seeds (studentized bootstrap per Ledoit & Wolf, or a stricter level), then use new final seeds.
- **Power.** Limited by data, not by the test: with ΔSR noise ≈ 0.27, a 0.5 edge is ≈ 1.9 SD, which gives ≈ 62–65 %
  one-sided detection for any valid 5 % test. No valid 5 % test can be expected to reach 80 % at ρ 0.5 on 1 710 bars. For comparison, the
  legacy rule needs roughly a 1.0 difference to approve (2026-10-23 calibration note).
- **Practical consequence for step 4.** A candidate that differs genuinely from the control needs an expected edge
  well above 0.5 (≈ 1.0 for reliable detection) or more data; small modifications of the control (ρ ≈ 0.8) are
  detectable at 0.5.

## Procedure notes

- Both layers were first launched at about 09:59 UTC and stopped after ~20 s, before any output, because Layer A would
  have hit a process time limit. Layer B was restarted once and ran to completion. Layer A was restarted, stopped again
  after ~2 min (still before any output; the runner writes only when a scenario finishes, the first took ~18 min) and
  started a third time as a detached process. Every launch used the same command and seeds; results are
  deterministic from the seeds, so restarts cannot change them.
- Run times: Layer B 10:00–10:10 UTC, Layer A 10:00–13:37 UTC (2026-11-08).
- Files: `layer_A.json`, `layer_B.json` (with commit and code SHA-256), `acceptance.json` (§7 evaluated by
  `control_test_calibration.acceptance`).
