# Phase 5 new-store registry protocol v2

User authorization: complete all remaining Phase5 code. Reviewed design direction
from ADR-PROPOSAL-pending-state-v2, amended with compare-and-delete. Isolated new
code and synthetic stores only; no migration, v1 mutation, H-025 or activation.

Exact discriminators: registry_schema_version=wp5_0a2.registry.v2;
anchor schema_version=wp5_0a2.anchor.v2; report schema_version=wp5_0a2.report.v2;
pending schema_version=wp5_0a2.pending.v2. Registry identity hashes the same four
path/schema fields as v1, with these v2 version literals. Golden physical tables
and triggers unchanged. Mutex schema/sentinel remains wp5_0a2.mutex.v1.

Authority app.research. initialize_trial_registry creates fresh artifacts only;
no constructor creates implicit stores. execute_trial(request,registry_path,
anchor_path) implements all five logical modes using existing pure prefix/timing
components. Explicit absolute canonical paths, no symlinks/H025 names, no WAL or
sidecars; controlled rollback-journal scope, no hostile path-replacement claim.

Resolve R3A meta-vs-state conflict explicitly: metadata contains ONLY version and
identity. Mutable registry_state contains COMPLETE and optional pending_anchor_update.
Later BM1 hardening R1 §3.3 and R2 §3 show pending state insertion/deletion; these
are historical design/implementation evidence, not reusable passing-test evidence.
Pending exact R3A fields, with v2 schema literal, are inserted atomically with row.
Cleanup DELETE matches BOTH key and verified canonical value; assert rowcount1,
rollback/refuse otherwise. No metadata trigger weakening or existing-store repair.

Writer mutex BEGIN EXCLUSIVE precedes registry BEGIN IMMEDIATE. Reader mutex
BEGIN DEFERRED plus sentinel read precedes registry BEGIN DEFERRED/query_only.
All modes verify structure/identity/anchor and hash chains inside held lock.
Writers alone may roll forward validated pending state via RV0–RV9 (advance or
already-applied); readers refuse pending with COUNTER_NONMONOTONIC. Atomic row
commit precedes durable anchor file replace/fsync and exact pending clear. A
phase1 committed effect returns success even if publication/clear fails, preserving
R3A no-retry semantics; later writer recovers before a new requested effect.

Ten-key v2 envelope keeps frozen per-mode checks/results, PASS-only prefix and
NOT_EVALUATED tail, null counters before chain proof, sealed canonical hash. Clock
is private service dependency (time_ns//1e6), not caller request-assigned fields.
Repeated fingerprints create distinct counted trials. Outcome cannot rewrite a
reservation or append a second terminal result. Replay has no data-reader/writer
capability and verifies persisted inputs/method/result only.

Requires RED tests, integrity/negative cases, subprocess crash and deterministic
concurrency tests, full regression and independent review before acceptance.

## Explicit v2 commit-ambiguity boundary

If COMMIT raises with the transaction still active, return a failed append and
roll back. If SQLite reports no active transaction, inspect exact row AND exact
canonical pending value under the same held mutex. Matching committed content
proves success despite the exception. If that probe is unavailable/inconsistent,
raise CommitOutcomeUnknown instead of manufacturing an INVALID/no-effect envelope.
The caller must reconcile; automatic retry is forbidden. This explicit v2 exception
is a contract extension, not a new scientific reason code or proof of no effect.

RV0/RV1 validation (including malformed/empty JSON) occurs at REGISTRY_OPEN with
REGISTRY_SCHEMA_UNSUPPORTED under the newer R3A mapping. RV2/head/max, RV3 row hash,
and RV4 operation type precede target-counter/full-chain agreement. The mutable
pending marker is detected by presence, never truthiness. Registry journal checks
and anchor inode snapshots happen AFTER mutex acquisition so cooperative writers
are not mistaken for corruption. Reader/writer contention yields REGISTRY_BUSY.

Current tested scope includes real subprocess termination after phase1/before
publication and after publication/before pending clear, exact compare-delete,
cooperative lock barriers, read-only pending refusal, and cross-program preservation.
It does not claim every crash/disk-failure/filesystem state production-verified.
