# WP6-2E — diagnostic reservation ledger v1

Architecture refinement under ADR-MIE-004, 2026-10-12. Owner app.research. Separate
schema/store from Phase5 v2; no migration, equivalence, repair or historical-counter
inference. Scope: durable diagnostic reservation allocation with synthetic tests.
No feed, strategy, anchor service or runtime admission is activated. Real owner,
registry identity, roots and approval provenance must be established externally.

## Store and lifecycle

ReservationLedger(root, *, owner, registry_identity, max_bytes) requires an existing
empty absolute normalized non-symlink root, excludes H025-like components, and holds
an exclusive descriptor-bound advisory directory lock. It reuses the reviewed
object-store descriptor/path/publication primitives, not the per-run header.
Header registry.json exact: schema_version=p6.diagnostic_registry.v1, owner,
registry_identity (64 lowercase hex), max_bytes (positive signed64). Header is a
permanent creation claim. No reopening for writes, even after orderly close; v1
supports multiple reservations only in the original process/thread. Restart cannot
reset the counter or create a replacement identity automatically. A future approved
registry continuation/migration protocol is necessary after closure or interruption.
Earlier ledgers must remain disclosed; counts never mean complete scientific N.

Files: H(plan).blob, H(authorization).blob, reservation-NNNNNNNN.json for each
canonical WP0 reservation, sequentially starting1. No self-ID or future-data hash.
File fsync/chmod0400/fsync, directory fsync and readback precede acknowledgement.
No overwrite, deletion, symlink or hard-link acceptance. Revalidate known inventory
and root binding before each attempt. max_bytes covers all nonheader bytes, with
content dedup only for byte-identical plan/authorization blobs. Maximum1000reservations.

reserve(plan, authorization, *, reserved_utc_ms) snapshots bounded plain primitives
before validation; no user-defined deepcopy hooks. Validate PLAN using WP6-1. A has
exact WP0 fields, schema p6.authorization.v1; owner=plan.owner=registry owner,
plan_hash=H(plan), registry_root=the opened root, and evidence_root a distinct existing
safe directory. Neither root may contain the other. Roots must resolve to distinct
inodes; no implicit creation. approval_reference is nonempty ASCII<=512characters;
anchor_endpoint_id a WP0 token. Approved time and supplied reservation time are true
nonnegative signed64, with approved<=reserved<plan.start_ms; reservation time cannot
decrease relative to previous acknowledged reservation. These supplied times and
reference strings are checked constraints, not proof of authentic authorization or
clock acquisition. The enclosing coordinator must establish those trust facts.
Reject a repeated plan hash in this ledger; an attempt needs a new frozen plan and
real authorization. Consumed failures cannot be hidden as a retry.

R exact WP0 fields: schema_version=p6.reservation.v1, plan_hash, authorization_hash,
registry_identity, sequence, reserved_utc_ms, previous_reservation_hash (zero first,
H(previous R) thereafter). run_id=H(R). Publish P, then A, then R under the single
writer lock. Only after all publications return advance the acknowledged counter
and in-memory chain. R is the logical commit; no atomic transaction across files
is claimed. Any exception during an admitted reserve poisons/closes the ledger,
retains partial files and disallows further reservations. An unacknowledged R may
already exist and is an unknown consumed attempt, never permission to retry.
Cleanup failure must not hide the original failure. Foreign process/thread use
refuses before I/O and must not close the owner's descriptor. No explicit LOCK_UN.

Return exact: schema_version=p6.reservation_result.v1, reservation, run_id,
status=RESERVED_UNANCHORED, runtime_accepted=false, complete_family_N=null,
external_authorization_verification=UPSTREAM_REQUIRED.
This is not READY: reservation and genesis still require independently retained
anchors, authenticity checks and full coordinator preflight before intake.
Another distinct reservation may be allocated in this process; this consumes a
separate opportunity and does not start or retry an earlier run.

snapshot() returns detached header, acknowledged_reservations list, inventory
(name->{sha256,size}), lifecycle OPEN/CLOSED/INCOMPLETE, technical_verification
LOCAL_ONLY, runtime_accepted=false. Inventory excludes header and includes only
successful returned publications. Partial files may additionally exist. The last
acknowledged counter cannot prove absence of unknown later commits. No read path
manufactures STOP/COMPLETED or repairs a partial tail. close failure is INCOMPLETE.
No operational CLI/start command is introduced.

## Read-only publication inspection

inspect_reservations(root, *, header, inventory) accepts explicit expected values,
shared nonblocking directory lock (active writers refuse), exact canonical header,
regular single-link files, byte counts/hashes and before/after identity/stat/name
checks. Reject unexpected/missing/partial files and unsafe paths. It neither infers
expected inventory nor re-evaluates a strategy. Result: schema_version=
p6.registry_inspection.v1, structural_status=VALID, technical_verification=LOCAL_ONLY,
file_count,total_bytes,runtime_accepted=false. Caller expectation authenticity and
semantic/externally anchored ledger completeness remain upstream.

Closed refusals: INPUT_INVALID, PLAN_INVALID, AUTHORIZATION_INVALID,
DUPLICATE_PLAN, CLOCK_INVALID, REGISTRY_STOPPED, STORAGE_REFUSED,
INTEGRITY_FAILURE. Unexpected exceptions propagate with a terminal ledger.
Tests: consecutive distinct reservations, exact plan/A/R binding and prefix;
pre-start timing, mismatched approvals/roots, duplicates, no implicit root, actual
partial-write/process interruption and no reopen, competing writers/fork/thread,
mutated inputs/snapshots, all publication failure windows, read-only inspection,
no scientific count or runtime admission. All writes use new temporary test roots.

Implementation bounds: snapshot max10,000values, depth6, list1000, dict4000,
ASCII string4096 and key128; exact primitive types only. Inventory max3000files,
names lowercase64hex.blob or reservation-NNNNNNNN.json with sequence1..1000;
values exactly sha256 (lowercase64hex) and size (nonnegative signed64). Blob name
must match its expected digest. Same-thread reentrant reserve/close poisons the
outer call before I/O; outer publication/acknowledgement checks remain OPEN.
