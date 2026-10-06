# Read-only registry inspection v1

User-approved follow-up to REGISTRY_OPEN-design-decision.md. This is a closed
internal inspection, not a complete public REGISTRY_OPEN/ledger report or an
active mode dispatcher. No repair, replay, reservation or recovery.

API: inspect_registry(registry_path, anchor_path) returns exactly
inspection_status (STRUCTURE_VERIFIED, RECOVERY_REQUIRED, REFUSED), reason_code
(null or REGISTRY_CORRUPT, REGISTRY_SCHEMA_UNSUPPORTED, RESERVATION_RACE),
registry_identity (null on refusal), pending_detected (null on refusal), and
ledger_verification (always NOT_PERFORMED). No connection escapes. Pending
presence yields RECOVERY_REQUIRED after structural validation; no validity of
pending contents, ledger chain, counters or recovery is asserted.

Inputs: canonical absolute built-in string or pathlib.Path paths, existing regular
files without symlink components. Mutex is anchor_path + '.lock'. Distinct inode
identities required for three artifacts; no H-025-named path may be opened.
No source artifacts are created. Tests use synthetic temporary fixtures only.

G-1 resolution: exactly 'file:' + urllib.parse.quote(path, safe='/') + '?mode=ro'
for registry, '?mode=rw' for mutex. uri=True, isolation_level=None, timeout=0.
No immutable flag, retry, URI injection or creation-capable connect. query_only=ON
on both connections. Refuse existing SQLite -journal/-wal/-shm sidecars before
opening; this initial inspection supports quiescent rollback-mode databases only.
Concurrent hostile filesystem replacement is not proven safe by URI mode alone;
require a controlled inspection scope and compare file identities before return.
No promise about all filesystem/durability implementations.

Order: O1 registry nonempty; O2 open existing mutex, exact Golden Schema R2 DDL
and sentinel; O3 BEGIN DEFERRED and a sentinel read inside the transaction to
acquire the SHARED lock; O4 open registry read-only, BEGIN DEFERRED;
O5 integrity_check; O6 exact seven registry objects (Golden canonicalization
removes 'IF NOT EXISTS ' once only); O7 exactly schema/identity metadata plus
optional pending_anchor_update; O8 path-bound registry identity as initialized;
O9 exactly init_state=COMPLETE; O10 existing canonical anchor, exact top-level
keys, valid document hash and per-program R3A binding hashes. Finally classify
all stored rows as exact eleven-field reservations or nine-field outcomes.
Chain links, row hash recomputation and anchor high-water comparison are NOT run.

Failure mapping follows P0-R3 O1–O9; absent/malformed anchor maps to
REGISTRY_SCHEMA_UNSUPPORTED (hardening R3 A_form). Mutex open/validation errors
map to REGISTRY_CORRUPT. O3 SQLite lock failure maps to RESERVATION_RACE; no retry.
SQLite/data-access errors otherwise map to REGISTRY_CORRUPT. Malformed persisted
rows map to REGISTRY_CORRUPT. Pending is an administrative stop, not a new reason.
Each return closes/rolls back both read transactions via finally.

Anchor format: schema_version wp5_0a2.anchor.v1, registry_identity, positive int
(non-bool) document_sequence, programs dict, document_hash. Each nonempty string
program key maps exactly to reservation_high_water/chain_high_water (positive
non-bool ints), head_row_hash and binding_hash (lowercase hex64). Hash body:
research_program_id, reservation_high_water, chain_high_water, head_row_hash,
registry_identity. Document hash excludes only document_hash. Bytes must equal
canonical JSON with no trailing newline. No silent rewrite or recovery.

Row inspection: physical program is nonempty str, chain_index nonnegative int (zero-based);
row_json is JSON with no duplicate keys or nonfinite constants. Exact frozen key
sets and scalar types required; program equals physical key; hashes lowercase
hex64; reservation/outcome sequence positive int, timestamps nonnegative int;
zone and terminal outcome enums exact. Sequence monotonicity and identity/hashes
are subsequent ledger checks, not structural PASS.

Sources: Golden Schema R2 §§2–6; P0-R3 §3 O1–O10; BM2 R3 M4-C10–14;
hardening R3 anchor form amended by R3A §1.3; original matrix §6.1, R3 §6 and R5 A8.
Existing initializer, nine prior package files and frozen tests are unchanged.
New files only: registry_inspection.py, test_phase5_registry_inspection.py, this
spec. RED first, GREEN, byte/directory-preservation and connection/order tests,
Phase5/full-suite regression, independent review. No commit/push/merge/PR.
