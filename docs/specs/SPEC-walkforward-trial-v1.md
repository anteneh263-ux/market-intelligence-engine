# Governed historical walk-forward trial v1

Owner Phase5; numerical authority remains historical adapter/backtest. This is a
new callable integration, not activation of a CLI, prospective engine or H025.
User authorization covers code and synthetic tests. Dedicated review required.

`run_walkforward_trial(reservation_request, policy, segment_manifest, *,
registry_path, anchor_path, evidence_directory, walkforward_directory,
read_bars, select_parameters, signal, regime, prior_result_artifacts)`.
Both evidence directories must already exist, be dedicated/empty, nonsymlink
absolute paths and distinct. Registry must be an existing v2 installation.
No defaults, initialization, migration, overwrite, resume, repair or cleanup.

Pure preflight binds the real WP0 contract and reservation fingerprint, strategy,
dataset ID and whole evaluation range inside one TRAIN or VAL zone (never
HOLDOUT). `walkforward_method_hash(policy)` commits every policy field except
experiment_id (avoids recursive identity); contract method_hash and attempt
method_hash must match. Contract param_hash binds the versioned adaptive selector
via `walkforward_selector_hash(policy)`, not a pretend preselected result. The
per-window selected values are subsequently frozen by WP5-2. These explicit
adaptive-method conventions require new experiment identities, never relabeling
an existing method. Caller is responsible for supplying the implementation named
by callback_version. Arbitrary callbacks are not a code-execution sandbox.

Manifest exact `{schema_version:'wp5_2.segments.v1', segments:[{start_ms,end_ms,
rows_hash}]}`. Exactly one entry for every distinct training or test range, in
sorted boundary order. Hash canonical raw reader dictionaries. Attempt
ordered_input_hashes must be `[hash(manifest)]`. This binds exactly which segment
contents the trial may consume; each reader result is verified BEFORE passing to
selection/signals. No full-dataset content-hash/Merkle membership claim: dataset_id
is contract-bound metadata, while range contents are verified against the input
manifest. Upstream dataset provenance remains required. Manifest contains only
hashes, so no OOS observations are opened by preflight.

Order: durable RUN binding -> reservation commit -> durable RESERVATION receipt
-> verify registry snapshot available -> bounded WP5-2 (freeze before OOS) ->
durable WF report -> durable `wp5_3.trial_result.v1` artifact -> outcome append
-> durable OUTCOME receipt -> read-only bound statistics -> durable RESULT.
Each run reserves once; duplicate invocation refuses via permanent RUN claim.
Reservation failure accesses no data. Crash preserves reservation/count and
partial artifacts. Unknown registry commit propagates CommitOutcomeUnknown;
never retry automatically. Exceptions after reservation preserve state and
propagate; no invented terminal outcome for an uncertain operation. An INVALID
WF report is a durable COMPLETED_INVALID outcome bound to that report's complete
canonical bytes; no result return vector is fabricated.

COMPLETED_VALID means a valid historical WF report with computable frozen
per-trial moments was persisted and its result hash recorded, not that DSR passed or the strategy was promoted. Statistics use
all supplied committed artifacts plus this result; missing historical artifacts
refuse statistics. Its failure never deletes a valid historical result or count.
Result's distinct lifecycle and statistics fields prevent promotion by conflation.

New run-local evidence files use the reviewed WP5-2 exclusive/fsync/readback
protocol. No new independent numerical engine/store schema. Registry authority is
v2 and result artifacts are bound to outcome hashes. Failures after append may
leave a valid terminal outcome without local OUTCOME receipt; operator reconciles
registry and preserved artifacts, no automatic reconstruction.

Review correction: before outcome append, run frozen statistics_math with N=1
solely for per-trial return/moment eligibility. Only finite computable moments and
reason INSUFFICIENT_TRIALS_FOR_DSR qualify as COMPLETED_VALID. T<30, zero variance
or invalid moments become COMPLETED_INVALID; preserve TRIAL_RESULT plus durable
ELIGIBILITY.json and completion_reason. Such attempts remain in N_total but never
poison the completed-valid Sharpe vector. This test is not a PSR/DSR threshold or
performance selection: no computable low-return result can be discarded by it.
