# Observed Monte Carlo v2 / validation plan v3

See ADR-MIE-002 for engine/storage authority, baseline equivalence and failures.
New plan v3 freezes the full MC policy before WF results. It requires WF policy v3
and AFTER_VALID_MC_RETAIN_DOWNGRADE. Earlier plan v1/v2 and MC v1 semantics remain.
No automatic promotion; current pipeline still reports LIMITED_PROFILE.

Policy is exact: master_seed uint64, profile_grid (absolute nondecreasing taker fee,
slippage and half-spread bps), numeric parameter_grid patches, repetitions,
execution_delay_bars, missed_fill_probability, order_perturbation NONE or
SHUFFLE_EXPOSURE_BLOCKS, n_scenarios, downgrade_rule. n_scenarios must equal the
entire profile×patch×repetition product, 1..10000; duplicate grid cells refuse.
All patches must match existing candidate keys and exact numeric types before
reservation/data reads. Unknown or incompatible parameters fail closed. Every
variant is retained without a best-parameter field, ranking, selection or base
mutation. The actual WF-selected base parameters still feed final holdout.

SCHEDULE.json enumerates every scenario/window's parameters, profile, stress policy
and destination before any MC source read. Seed=SHA256(canonical JSON of master
seed, plan hash, trial-result hash, cost-grid index, patch index, repetition and
window ID). Each attempted decision draws SHA256(canonical JSON of seed,
decision_id, attempt_open_ms), compared as an integer with the exact float probability
ratio. No wall clock or ambient RNG. Separate reorder stream hashes the window seed
and purpose EXPOSURE_BLOCK_REORDER; existing unbiased SHA-counter Fisher-Yates is used.

Native stress executes the full scenario grid with actual eligible order times,
simulator-suppressed fills, native costs and risk stops. It first replays each base
observed session by exact hash and verifies native zero-stress economic/order parity.
The execution profile remains limited to bps-on-notional cost grids. No inference
that other fee models or continuous prospective execution have been validated.

## Analytical exposure-block order perturbation

Every original close-to-close net return remains a whole sample, with its original
cost and overnight-gap contribution embedded. Classify each sample by BOTH endpoint
actual position quantities; adjacent equal pairs form a block. Include flat blocks.
Shuffle those blocks, preserving internal sample order and every original index
exactly once. Persist original block IDs/positions, permutation, return indexes,
derived hash and metrics. This is analytical exposure-state order fragility, not
independent completed-trade PnLs, not a simulated chronology, and not actual fills
at rearranged times. timestamps=null and completed_trade_interpretation=false.
Quantile calculations cannot manufacture alternate execution observations.

## Reporting, failure and verification

Each scenario retains every window's native evidence hash, lifecycle and complete
flag. A complete scenario uses arithmetic mean of its independent window total
returns and worst window drawdown; no compounding across capital resets. Survival
requires complete execution, no ruin, and the frozen return/drawdown limits.
Every failed/incomplete native scenario remains in the overall survival denominator.
Generated/valid-generated mean structurally generated, not financially successful.
If any scenario is incomplete, metric quantiles/worst case are null and all incomplete
IDs are listed; do not silently compute a favorable distribution from survivors.
When all are complete, linear-interpolated .05/.5/.95 quantiles and min-return/
max-drawdown summary are derived over the entire scenario set. No scenario is dropped.

Readiness is solely the frozen minimum-survival-fraction predicate: PRESERVED or
DOWNGRADED. Checks use the original ordered policy/seed/perturbation/generation/
completeness/base-immutability/aggregation/monotonicity/derivation/hash sequence.
Structural exceptions preserve partial files and stop without a valid report.
A broker unknown-outcome exception propagates; it is not recast as valid evidence.
A valid downgrade may proceed to holdout under plan v3 but remains a downgrade even
if holdout passes. DSR and N are unaffected by the MC stress stage.

Actual path: observed_pipeline -> version dispatch in observed_mc ->
run_observed_mc_v2 -> verified committed WF -> frozen schedule -> observed baseline/
zero-stress parity -> stress_session/stress_engine -> complete scenario artifacts ->
report. Before holdout, verify_observed_mc_v2 regenerates the schedule, matches native
manifests and actual observation chains, verifies baseline parity, derives every
reorder/result and rejects missing/extra/duplicate/altered artifacts. The caller
verifies committed WF and current ledger stats; standalone MC verification is an
artifact verifier, not independent ledger authorization. No source read or re-run
occurs during verification.

All numerical evidence in this development package is synthetic. No new real-data
root, holdout consumption, original pilot replay or H025 work is authorized here.
Full real-data acceptance, release/PR and prospective authority remain separate.
