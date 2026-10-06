# Observed full-stage pipeline v1 — bounded synthetic integration

User continuation authorizes code and synthetic integration. Existing pilot001,
H-025, legacy artifacts and real holdout remain excluded. No new real-data root.
Publication still requires a separate reviewed PR. Phase5 full acceptance is not
inferred from this explicitly limited profile.

New wf_policy.v2 adds validation_plan_hash; v1 interpretation/replay remains valid.
The plan is part of the method identity before any performance read and freezes
CPCV applicability/pin, source catalog/holdout row-array hash, MC stress/bootstrap,
last-WF-training holdout parameter rule, fixed .95 DSR and corpus. It also pins
new adapter and consumed execution/governance code. Existing historical models
are neither changed nor relabeled.

Pipeline: persist plan → real CPCV split/purge adapter (or justified preregistered
NOT_APPLICABLE) → actual observed WF trial → current complete ledger statistics
→ observed cost stress and analytical bootstrap → reverify all prerequisite
artifacts → consumed-before-read observed holdout → ledger-anchored execution core.
CPCV here verifies geometry/purge/library parity, not CPCV strategy-performance.

MC v1: same native observed engine, fixed selected WF parameters and same fresh
capital/FIRST_CLOSE schedule. Exact baseline replay per window must match committed
WF native/observation results before stress. Full preregistered fee/slippage/spread
grid, never parameter selection. Profile values cannot reduce those baseline costs.
Each grid/window executes once; risk/data-stopped stresses remain failures, never
omitted. Every scenario uses one grid entry and bootstrap repetition across ALL
windows. No continuous cross-window compounded-return claim.

Bootstrap: SHA256 seed from master_seed, plan_hash, trial_result_hash, grid_index,
repetition and window_id. Reuse documented SHA256 counter/rejection RNG. Independently
within each window, sample starting indexes uniformly with replacement, take circular
blocks of preregistered block_length, truncate the final block to exactly the original
number of returns. Persist indexes and hash of derived returns/metrics, explicitly
ANALYTICAL_BOOTSTRAP with no fabricated execution timestamps. Each scenario survives
only if all its windows complete and satisfy frozen min_total_return/max_drawdown;
fraction over the entire grid × repetitions determines PRESERVED or DOWNGRADED.
Never upgrade or replace selected parameters. Delay, missing fills, trade reorder,
parameter sensitivity and distribution/model uncertainty are UNEVALUATED.

Holdout ownership: reuse app.research.holdout access-store v1 protocol, locks,
uniqueness and append-only transitions; no schema migration or new implicit store.
New observed_holdout adapter writes DISTINCT observed-evaluation/report artifacts.
The versioned core includes execution_evidence_hash AND returns/request/metrics;
COMPLETED.evaluation_hash commits this whole core. The execution receipt also
binds the exact pre-access prerequisite-proof hash, anchoring WF/statistics/CPCV/MC
and the plan through that same committed core. Thus execution linkage is anchored
by the access ledger, not merely a mutable side receipt. The legacy verifier cannot
claim this new core. Same strategy+dataset reuse guard applies across both versions.
No automatic recovery. Dangling RESERVED and CONSUMED remain non-reusable; failures
preserve them. A failed report write after completion does not authorize reexecution.

Holdout uses the last frozen WF training parameters with no warmup, fresh state/capital,
same profile, complete HOLDOUT membership, FIRST_CLOSE returns and actual observed
six timestamps. Its access authority is the holdout ledger, NOT a new TRAIN timing
validation falsely relabeled HOLDOUT. Use pure timing validator in HOLDOUT zone and
full membership checks; execution and timing are bound to the committed core. A
read-only verifier rederives the whole link without source reads or increments.

Before holdout, independently reverify actual committed WF receipt/outcome, unchanged
plan and per-window parameters, fresh complete statistics, CPCV seal/input geometry,
every baseline/stress execution and bootstrap scenario. Hash-check evidence after
each stage. No caller PASS booleans. Partial upstream failures stop before access.
After completion, source-less replay verifies all artifacts; cached statistics are
explicitly distinguished from current recomputation.
