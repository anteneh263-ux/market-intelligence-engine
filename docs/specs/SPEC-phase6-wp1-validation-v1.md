# WP6-1 — pure structural validation v1

Date: 2026-10-08. Implementation: app/research/phase6_validation.py, public
`validate(request)` and canonical encoding utility `canonical_hash(value)`.
This additive clarification resolves the reviewed WP6-0 pure input ambiguities;
WP6-0 runtime/stores/authorization semantics remain unchanged. No real data access.

## Input and boundaries

Request is a Python dictionary or UTF-8 JSON text/bytes, with exact fields:
`document, plan, reservation, prior_receipts, prior_segment, payloads, clock_probes`.
JSON rejects duplicate keys, BOM, invalid UTF-8 and nonfinite constants. For decoded
dicts, earlier duplicate serialized keys are unknowable; this path asserts only
supplied structure. canonical_hash is an encoding utility, not a validator.
Every schema's integers are nonnegative signed64 excluding bool; decimal fields
are exact canonical strings per WP6-0. No rounding/arithmetic engine is added.
Unknown versions/fields/types are SCHEMA_INVALID; a lexical but unsupported plan
profile or receipt kind is POLICY_UNSUPPORTED. Malformed unknown document kind is
reported as PLAN solely as a failure-envelope fallback; runtime_accepted remains false.

| document | plan | reservation | prior_receipts | prior_segment | maps |
|---|---|---|---|---|---|
| PLAN | null | null | [] | null | {} |
| BAR | P | null | [] | null | {} |
| RECEIPT | P | R | Complete receipt prefix excluding document | null | All referenced normalized payloads/probes |
| SEGMENT | P | R | Complete receipt prefix through accepted E, inclusive | Immediate preceding S, or null at genesis | All referenced normalized payloads/probes |

Required context absent is IDENTITY_MISMATCH. Wrong container/type/schema anywhere
is SCHEMA_INVALID even if another identity check would fail. Prior context must
pass the same local shape/binding/content checks; a caller's asserted valid flag is
not accepted. `reservation.registry_identity` is a64lowercase-hex commitment.
R.plan_hash=H(P); each E/S.run_id=H(R). R sequence/genesis/time are checked, but no
registry authenticity, actual consumed reservation, owner approval or anchor is proven.

payloads maps SHA256 to normalized B/Q JSON objects; clock_probes maps SHA256 to T.
Every supplied entry is shape-checked, domain-checked and independently hashed;
keys are not trusted. Hash-key disagreement is HASH_MISMATCH; a missing referenced
entry is IDENTITY_MISMATCH. Raw provider payload hashes are opaque commitments here;
raw bytes, adapter finality, clock source trust and external policy/history/method
references are UPSTREAM_REQUIRED. No disk/network resolution occurs.

Each receipt prefix starts1 and is contiguous with exact predecessor hash, stable
clock session, monotonic increasing observation counter and nondecreasing UTC.
Every locally bound clock probe and normalized payload is checked. Received bar
must be closed and within lateness bound, including the final bar after plan.end.
Receipt validation authenticates no actual observation. Conflicting/duplicate bar
receipts may be structurally represented, but only the first receipt of a bar key
can be selected as a segment's accepted receipt; later choices fail SEQUENCE_INVALID.
They cannot silently produce another accepted segment or replace earlier data.

Segment expected interval is P.start+(segment_seq-1)*timeframe. It binds the exact
bar and accepted receipt. Immediate predecessor checks include shape, run, ordinal,
interval, bar/receipt references and genesis rule. Current predecessor hash must
match H(prior_segment). Earlier segment ancestry beyond the supplied immediate
predecessor is not independently available and remains upstream; the complete
receipt prefix is available and checked. No claim of anchored full-run integrity.

## Result and precedence

Exact result fields follow p6.validation.v1 from WP6-0. Errors are collected by
category after safe shape checks, then first category selected:
SCHEMA_INVALID → IDENTITY_MISMATCH → POLICY_UNSUPPORTED → CLOCK_INVALID →
DATA_INVALID → SEQUENCE_INVALID → HASH_MISMATCH.
No arbitrary exception strings are exposed as refusal codes. VALID yields H(document);
INVALID yields null. Always external_reference_verification=UPSTREAM_REQUIRED and
runtime_accepted=false. This is not trial registration or runtime acceptance.

## Verification scope

Tests exercise frozen synthetic vectors, malformed encodings/fields/decimals,
run-to-plan bindings, deadline/clock faults, payload tampering, duplicate/revised
segment selection, predecessor substitutions, positive multi-segment linkage and
lack of input mutation/I/O. Test results are saved outside the source tree.
No Phase5 numerical modules, records, source pins or pilot evidence changed.
WP6-2 execution/storage contracts are still required before implementation.
