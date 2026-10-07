# Ledger verification v1

Authorized by the user's Phase 5 completion request, 2026-10-01. Internal closed
read-only helper verify_registry_ledger(registry_path, anchor_path,
research_program_id=hex64). No writes, recovery or ten-key mode-envelope claim.

Reuse structural inspection via a private callback executed while BOTH existing
read transactions remain open. Structural validation precedes ledger verification;
file identity checks follow it. Public inspect_registry behavior stays unchanged.
Support and filesystem concurrency limitations remain those of inspection v1.

Walk ALL programs in physical program/index order. Require zero-based contiguous
indexes, recomputed canonical row hashes excluding row_hash only, genesis zero
sentinel and every previous link. Reservation count increases by one, recompute
five-field trial identity, forbid duplicate trial IDs; outcome count separately
increases by one, requires an earlier reservation and at most one terminal outcome.
Then refuse pending updates. Compare exact program sets, reservation counts, row
counts and head hashes with the structurally verified anchor. Document sequence
counts anchor replacements, so do NOT equate it to ledger length.

Return requested-program counts/head only after complete verification. Absent
program returns verified genesis. All counters/head/identity null on refusal.
Structural failure codes are preserved; chain/identity/reference faults map to
LEDGER_CHAIN_BROKEN; sequence/pending/anchor mismatch to COUNTER_NONMONOTONIC.
Invalid requested ID maps to WRONG_RESEARCH_PROGRAM_ID. Callback read errors map
to REGISTRY_BUSY for SQLite busy/locked; otherwise INTERNAL_STRUCTURAL_FAULT.

Sources: matrix R3 §6 amended by R5 A8, R4 C2–4; master R3A A3; hardening R3 §1.2
and ordered table §2.4, hardening R3A §§1.2–1.3 and 3.5. No recovery state altered.
