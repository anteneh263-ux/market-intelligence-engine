# Governed observed-session trial v1

New simulated-session governance profile, not a replacement for historical WF or
prospective execution. Versioned app.paper.session.observed_engine_v1 retains numeric execution authority;
legacy engine.py is unchanged. User
renewed completion request authorizes code/synthetic integration; real experiment
and explicit physical evidence ownership are still caller prerequisites.

Caller owns an existing empty evidence root and existing v2 registry/anchor. Phase5
claims RUN.json exclusively, persists frozen native manifest, full-dataset metadata,
segment commitment and method pin, reserves trial BEFORE source read, then explicitly
creates session child owned by observed_session. No implicit root, resume, migration,
legacy cutoff, H025, holdout access or reconstruction. Interrupted reads/observations
leave counted reservation and incomplete evidence, never silent retry or fake outcome.

Full dataset identity is SHA256 of wp5.session_dataset.v1 (timeframe_ms, rows of
open_time/row_hash, pinned coverage). This explicit identity is not a renamed legacy
file hash. Segment binds source-row-array hash and existing session engine dataset
hash. Verify all accessed rows against the dataset-root commitments before engine.
Only TRAIN/VAL segments allowed. Session parameters and method hash bind WP0 config;
method pins frozen native manifest/segment/coverage/strategy state and engine/observer
source. Zero settlement, signal and submission latency only, otherwise preflight
refuses. Values are simulated event observations, never wall-clock assertions.

Coverage v1: CONTINUOUS_24_7 requires exact complete grid. SESSION_CALENDAR requires
exact opens produced by the existing execution profile's UTC day-level weekday model
and binds calendar_id/calendar_hash; no exchange-holiday/DST claim. SPARSE_DECLARED
requires exact pinned declared membership and coverage_manifest_hash, with every bar
on declared timeframe grid and allowed by the execution profile. No missing declared
member is imputed. The separately versioned observed engine accepts
integer-multiple gaps; legacy default cadence predicate is unchanged. Broker/risk
policies continue to decide whether such gaps require stopping.

After actual observed run, correlate executions to full covered source/fill members
and call v2 TIMING_VALIDATION against persisted trial/contract. Pure timing helper's
deferred calendar/sparse membership is closed by this independent complete-coverage
check. Reports distinguish VERIFIED_FOR_EXECUTED_DECISIONS from NO_EXECUTIONS and
retain all nonfilled/pending statuses. No invented fill for flat/rejected orders.

Record computability, not performance filtering: COMPLETED or RISK_STOPPED native
sessions with eligible finite >=30 returns are COMPLETED_VALID. Poor computable
returns remain valid observations; data/signal invalidity or degenerate/short returns
are COMPLETED_INVALID and still count in N. DSR is separate; N1 cannot pass.
Equity points preserve exact simulated timestamps. First close is the statistical
baseline (no artificial initial flat return). A terminal safe-exit adjustment at
the same time replaces that endpoint rather than adding a zero-duration return.
A later terminal adjustment is an explicit new timed endpoint.

Preregistered return_policy is part of method identity: schema session_returns.v1,
baseline FIRST_CLOSE, frequency UTC_GRID or SESSION_BAR, step_ms and calendar policy
hash. UTC_GRID requires every observed return interval to equal the declared step
(one timeframe for continuous, an explicit multiple for sparse). SESSION_BAR
requires exact consecutive close endpoints of the pinned execution-profile calendar.
No annualization, gap imputation, return resampling or unobserved valuation. Missing
snapshots, interrupted irregular intervals and unmatched terminal exits are
NONCOMPARABLE and COMPLETED_INVALID even when their execution evidence verifies.
The calendar is the explicitly named UTC weekday model, not a real exchange calendar.
All completed-valid observations in one bound Sharpe vector must carry the same
return_frequency_hash. Mixing new sampling profiles or implicit legacy-v1 returns
refuses; observations are never silently discarded to make the vector comparable.

New exact trial_result.v2 extends v1 by execution_evidence_hash and
return_frequency_hash. Execution evidence references EXECUTION_RECEIPT.json, binding
frozen RUN hash, native observation report, timing verification, coverage, eligibility,
interval status and actual return hash. Outcome hashes therefore bind returns AND
execution/governance provenance. Method pin additionally covers governance/coverage
source, not only native engine/observer code. Bound statistics accepts exact v1 or v2 only;
existing v1 behavior is preserved. Version separation is mandatory. Ledger/results
remain actual authority, not a supplied success boolean. Dedicated review required.


Read-only verify_session_trial obtains the committed result binding using actual
registry VERIFICATION_REPLAY, then checks the receipt, full observed event chain,
original journal and native order equivalence, derived timed equity/returns and
actual TIMING_VALIDATION plus complete membership. It never calls read_bars or
strategy, increments N, reopens performance data or reconstructs missing artifacts.
It explicitly does not relabel cached statistics as current verified statistics.
