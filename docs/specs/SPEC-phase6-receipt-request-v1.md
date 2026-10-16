# Receipt append request preflight v1

Scope: additive pure request preflight under app.research. No durable service,
AWS deployment, signing, authentication, retention, pilot or historical changes.
This is a proposed transport input contract, not operational acceptance.

`validate_append_request` snapshots plain bounded input using the existing anchor
validator primitive policy and returns a detached request. Exact input fields:
schema_version=p6.anchor_append_request.v1, run_id, anchor_seq, subject_kind,
subject_hash, previous_anchor_hash, service_identity. IDs/hashes use lowercase
64-character hex; service identity uses the existing token grammar. Sequence is
a positive signed64 integer, excluding bool. Sequence1 is RESERVATION with zero
predecessor; sequence2 is GENESIS; later sequences are CHECKPOINT and require a
nonzero predecessor. Reservation subject_hash must equal run_id because run_id=H(R). Actual predecessor existence and equality remain upstream.

Caller-supplied timestamps, signatures and retention assertions are rejected as
unknown fields. The canonical request hash commits all input fields. The
idempotency key is run_id plus anchor_seq; a future service compares the full
request commitment, returning the original result only for identical content and
refusing conflicting content. This helper does not implement that transaction.

Output schema p6.anchor_append_preflight.v1: status VALID/INVALID, reason_code
null/SCHEMA_INVALID/SEQUENCE_INVALID/IDENTITY_MISMATCH, detached request, request_hash,
idempotency_key and runtime_accepted=false. On rejection all three data fields
are null. VALID establishes syntax and internal sequence shape only, never caller
authority, current state, signature, persistence or runtime admission.

Before implementing a durable service, the architecture decision must define
serialized ownership, exact conditional publication, retention-version inspection,
crash reconciliation and authoritative challenged head reads. No in-memory lock
or Lambda concurrency setting alone establishes distributed atomicity.

Verification includes malformed/unknown/missing fields, bool/float/overflow,
sequence/predecessor shape, snapshot detachment, caller assertion injection,
custom-object refusal and same-key/different-content commitments. Cloud fault
injection and assembled runtime testing remain future work.
