# WP5-6 single-use store and executor — architecture decision v1

Owner Phase5, no numerical engine duplicated. New explicit existing empty root;
initializer creates only FORMAT, SQLite ledger and process mutex in that root.
No default root, migration, automatic initialization, legacy repair or H025 access.
Synthetic roots only during development. Existing engines/registries unchanged.

One append-only hash chain per dataset; SQLite rows have immutable UPDATE/DELETE
triggers. Each transition ALSO writes an exclusive canonical row receipt before
committing SQLite. Verify exact schema, complete row chain, state machine, unique
strategy+dataset reservation and matching receipt set before every transition.
Missing/extra/corrupt receipts fail closed. A crash between receipt persistence and
SQLite commit leaves an orphan receipt and blocks the store; no unsafe repair is
attempted. Whole-root rollback/coordinated replacement of database AND all trusted
receipts is outside this local cooperative threat model; external anchoring remains
necessary for stronger adversarial protection. Receipts detect row-only deletion,
including truncation. No row/artifact is deleted or overwritten by this protocol.

A nonsymlink OS flock mutex serializes full operations, including the data read and
evaluation. A concurrent loser receives HOLDOUT_ALREADY_RESERVED without reading.
Reservation key binds strategy_identity+experiment_id+dataset_hash; uniqueness is
strictly stronger on strategy_identity+dataset_hash across experiments. No timeout
or retry can restore AVAILABLE. Startup under exclusive lock appends BLOCKED for
any dangling RESERVED, with no live executor possible under that lock. CONSUMED
stays spent. Manual notes are append-only and do not change state or ordinal.

Gate sequencing: verify store -> trusted prerequisite verifier -> validate identity
and frozen rule -> RESERVED commit -> CONSUMED/read-authorization commit -> reader
-> canonical dataset hash -> trusted existing-engine evaluator -> immutable parameter
check -> durable evaluation core -> COMPLETED row binds core hash -> durable final
report binds completion row. Final core/receipt/report separation avoids cyclic
hashes. The read-authorization marker is conservative: a process crash immediately
after it but before physical IO still spends the attempt. It is NOT a claim that
bytes were actually read. RESERVED crash before authorization resolves BLOCKED.

The executor requires a trusted prerequisite verifier callable; arbitrary booleans
are NOT its API. Full pipeline must supply the verifier of actual bound WF/stats,
frozen CPCV applicability and MC reports. This low-level package by itself does not
prove those prerequisites. Registered strategy-family identity and dataset hash
must come from outer contract/plan. Evaluator returns net returns only; same frozen
min_total_return/max_drawdown rule yields PASSED/FAILED, never automatic promotion.

Verification replay reads persisted evaluation/report and chain only, has no data
reader/evaluator capability, and adds no row. A completed row without a final report
is not silently repaired. Failed evaluation after authorization remains CONSUMED;
exceptions never reset state. No real holdout is accessed in this implementation.

Review hardening: post-COMMIT SQLite exceptions are resolved under the retained
mutex by verifying the exact row plus complete receipt chain. Unresolvable state
raises HoldoutCommitOutcomeUnknown; no stale-state report or compensating BLOCKED
transition is issued. Cleanup attempts all handles, explicitly unlocks flock and
uses native SQLite close fallback, preserving already published completion.
