# WP6-2G — durable local start-binding attempt v1

Date: 2026-10-12. Scope: synthetic integration only. Architecture parent:
ADR-MIE-004. Owner: app.research, using the existing WP6-2B object store.
This additive transaction is not an execution engine, runtime admission, a
strategy serializer, or an external service. No migration or historical changes.

## API and identity

StartBindingAttempt(writer, context, *, clock). Context has exactly plan,
authorization, reservation, genesis, registry_identity, strategy_state_hash, trust.
All records are bounded detached plain primitives using WP6-2F snapshot rules.
The candidate genesis and its caller-supplied strategy commitment are checked by
WP6-2F; this package does not establish strategy serialization authority.
The clock is a required callable returning a nonnegative signed-64 integer UTC ms.
It is supplied diagnostic evidence, not authenticated time. No implicit clock.

Preflight composes WP6-2F with empty receipts and null head/challenge: only its
expected ANCHOR_INVALID with null anchor_reason_code permits preparation; all
schema, plan, identity and preliminary time checks must have passed. Additionally
H(trust) must equal P.anchor_trust_hash. Exact trust schema/key checking remains
deferred to finish; preparation only publishes candidate context. The writer's owner/run/root must equal
A.owner/H(R)/A.evidence_root. Its normalized root property is read-only context;
physical identity checks remain enforced on every publication and inspection.
Preflight failure does not claim or write the supplied writer.

## Causal publication

After exclusive one-time writer claim, publish P, A, R and candidate G as canonical
WP0 bytes, then the context as canonical bytes. Generate secrets.token_hex(32)
after those publications. Read clock and require preflight <= issued < P.start_ms.
Publish p6.start_challenge.v1 with context_hash and challenge {nonce,issued_utc_ms}.
Only then acknowledge AWAITING_BINDINGS and expose a detached challenge snapshot.
This makes nonce generation local; transport, authentic response acquisition and
independent retention are still absent. G is a candidate initial state, not evidence
that execution or an actual strategy callback has occurred.

finish(receipts, head) accepts detached primitive records, samples clock, requires
issued <= now < P.start_ms, constructs the exact WP6-2F request exclusively from
pinned context plus those observations and the internally generated challenge.
Publish that request before invoking verify_start_bindings. Publish a record with
schema_version p6.start_result.v1, challenge_hash, request_hash, validation. The
validation is the exact pure result, including runtime_accepted=false.
Close the writer and read-only inspect the complete expected inventory before
acknowledging BOUND_LOCAL for VALID or REJECTED for INVALID. This is the terminal
local binding outcome; it never grants READY or transfers a writer to execution.
A stored VALID result records a local check, not completion of subsequent close
and inspection, nor an admission token.

## Failure and ownership

Bound to the creating process/thread. Foreign calls refuse without poisoning the
owner. Same-thread reentrant mutation poisons the attempt. Constructor post-claim,
clock, random generation, snapshot, publication, verifier, close and inspection
failures are terminal INCOMPLETE; preserve partial bytes and original exception.
Check lifecycle after callback/I/O boundaries before acknowledgement. No finish
retry, restart, nonce reuse, automatic fresh reservation, or recovery writes.
close while awaiting is terminal CLOSED; close during an operation is INCOMPLETE.
Snapshot returns detached context hash, challenge, record hashes, acknowledged inventory and
lifecycle, technical_verification LOCAL_ONLY, runtime_accepted false. On failure
acknowledged inventory can omit uncertain publications and is not completeness
proof. Constructor failure provides no usable attempt instance.

## Verification and remaining scope

Negative-first tests cover absent implementation, ownership/root/run/time errors,
nonce generation order, pinned request/challenge, signatures, mutation, publication
faults including write-then-raise, verifier faults, reentry, thread/process ownership,
close/inspection failure, and immutable inspection. Tests use temporary synthetic
roots and test-only signing keys. Required independent contract/code review.
No real root creation, feed, network service, account or pilot. No profitability
or scientific acceptance inference. Next: diagnostic strategy serialization and
WP0 event payloads, authentic clock/service adapters and governed admission, then
the continuous coordinator, CLI and separately approved prospective pilot.
