# WP5-2 historical walk-forward v1

New methodology `wp5_2.historical_close_to_close.v1`, not srp-matrix-v1/v2.
Implementation authorization: user requests completion of Phase 5; development
uses synthetic temporary roots only. Dedicated review required before completion.

API `run_walkforward(policy, *, evidence_directory, read_bars, select_parameters,
signal, regime)`; all callbacks are trusted application code, not sandboxed code.
No callback can be given a future prefix by this adapter; filesystem/network
access by arbitrary malicious callbacks is outside this process boundary.

Policy exact fields: experiment_id (64 lowercase hex), dataset_id (same),
strategy_id (nonempty, H025 excluded), start_ms/end_ms, train_span_ms,
test_span_ms, step_ms, mode rolling/expanding, min_train_rows/min_test_rows,
embargo_ms, cost_bps, slippage_bps, timeframe, callback_version (64 hex),
regime_policy_ref (64 hex), aggregation='disjoint_oos'. All time spans and minimum
counts are explicit positive integers; embargo/costs are nonnegative. step >= test
span rejects overlapping OOS. Partial final test windows are not evaluated.
No scientific defaults. Gaps between windows are permitted and disclosed.

read_bars(start,end) yields ordered exact dictionaries with sample_id, open_time,
close_time, close, label_start_ms, label_end_ms. Prices positive finite; integer
open/close and labels, open < close <= range end; strict ordered nonoverlapping
bars; sample IDs unique. Labels start >= bar close, end > start. Data must cover
both requested range boundaries exactly; gaps inside ranges are reported as
INSUFFICIENT_DATA. New version requires contiguous source bars (no implicit
market calendar). Caller binds dataset and callback versions; complete dataset
hash verification and registry integration remain upstream requirements.

Per window: read training range; run WP5-1; additionally discard labels ending
AFTER train end, so selection cannot use outcomes unavailable at cutoff. Feed
only retained rows to selector. Freeze returned JSON object and policy/window
bindings using exclusive-create, file fsync, directory fsync, readback before any
OOS read. Persist OPEN_OOS event before invoking read_bars(test bounds). Every
callback receives canonical copies. Verify frozen bytes before and after signal
and regime callbacks. Signals are {-1,0,1}; regime labels are nonempty strings.
Each receives only training price history + OOS prefix ending at decision bar.
Selection uses purged training rows; signal warmup uses all historical feature
bars, not their future label metadata (stripped to time/close fields).

Historical execution delegates to app.strategy_research.evaluation.backtest.
Compute decision on last training bar for first OOS return and each OOS bar.
Backtest uses only last training bar plus test bars, with previous decision shifted
one bar, starting each window flat (entry cost charged). Training context is not
part of OOS equity. Gross and net are separate engine calls; costs = gross-net.
No next-open-fill or prospective timing validity claim. Runtime decision ledger
records max prefix close <= decision time < priced bar close; regime is fixed at
window start from training history, so regime summaries have no retrospective
classification. Fixed params are never reoptimized within OOS.

Return windows with train/test/param_hash/gross_metrics/net_metrics/regime_tag/
coverage/window_status, aggregate gross/net/by_regime with timestamped returns,
walkforward_status VALID/INVALID, checks/reason_code, report hash, and explicit
upstream_binding=NOT_PERFORMED. Failure stops later windows (NOT_EVALUATED), clears
aggregate; insufficient data cannot produce VALID/flat returns. Hashes exclude
filesystem paths and wall clock. Input hashes + parameter freezes + OPEN_OOS
ordering persist; a replay in a NEW directory is deterministic.

Checks: WINDOW_POLICY -> PARAM_FREEZE -> LOOKAHEAD_GUARD -> PURGE_EMBARGO ->
COST_APPLICATION -> WF_HASH describe failures, not chronological data operations.
Reason vocabulary from master; IO/callback errors refuse at their current stage.
Report is only published after completion; publication failure raises OSError,
never returns a report falsely claiming durable completion.

## Architecture decision: owned run evidence

Phase5 owns append-only run-local governance artifacts, historical engine retains
numerical authority. Caller supplies an existing, dedicated absolute directory;
there is NO constructor/default root creation, migration, repair or resume.
Exclusive RUN.json is a permanent claim on the directory; retries require a new
explicit directory. Preexisting files/symlinks refuse. Frozen params/event/report
files are exclusive-create, fsynced and read back. Crashes preserve partial files
and never authorize replay/OOS reopening within that directory. Directory inode
and filenames are held through a directory descriptor, O_NOFOLLOW for reads.
Supported threat model is cooperative single-writer local filesystem; hostile
OS actors, rollback of the entire root and external anchoring are not covered.
Partial failures preserve original artifacts; no cleanup or overwrite.
No legacy equivalence claim: numerical parity with existing backtest is tested,
while window selection/freezing is intentionally a separate methodology.
Production activation, real roots, H025, existing versions and PR publication are
outside this code/synthetic-test package. Whole Phase5 remains incomplete until
upstream lifecycle + remaining WP5 packages are integrated.

Review clarification: timeframe is passed to the legacy engine but its annualized
metrics are discarded. This adapter emits only OOS return arrays, OOS counts,
compounded total and per-observation mean. No annualized metric is published;
source timeframe duration remains unverified. Warmup's leading zero is excluded
from every published metric. Regime grouping contains gross and net returns by frozen tag.

Integrity additions: mutation of a callback parameter copy refuses PARAM_NOT_FROZEN,
even though stored parameters remain unchanged. Gross/net observations <= -1
refuse COST_MODEL_INVALID; no bankruptcy/leverage continuation is defined.

Full historical-pipeline profile accepts an additional validation_plan_hash bound
into the adaptive method hash/experiment identity. It persists decision_positions
for exact downstream base replay. Existing policy shapes/hashes and reports stay
unchanged when this field is absent; full-pipeline callers must provide it.


## Full-pipeline profile integration

The additional validation_plan_hash policy field selects the new governed profile.
Existing policies retain their original outputs. This profile binds the frozen
validation plan into the method hash and persists each window's decision_positions
and frozen_artifact_hash. The outer phase5_pipeline recomputes the WF report seal
and verifies the full parameter artifact/OPEN_OOS receipt before MC and again before
holdout. This is historical integration, not six-event execution-time attestation.
