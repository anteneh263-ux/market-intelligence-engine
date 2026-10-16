# WP6-2U — Gated checkpoint continuation v1

Scope: continued Phase6 implementation and synthetic integration tests, based on
649ab32d71154f8899c709d3e3f0d36f22dacc40. No deployment, actual pilot/evidence-root,
provider, independent receipt service, restart authority or historical mutation.

`open_checkpoint_continuation(writer, manifest, proof, utc_ms, monotonic_ns)`
requires a nonempty WP6-2T checkpoint-chain proof. It uses EventJournal's new
explicit checkpoint_admission argument; older constructor paths remain available
for compatibility and are NOT automatically gated or production-authoritative.
This entry point does not support genesis. No overall session READY/ANCHORED status
is granted. All runtime/independent-retention flags remain false.

Before claiming the writer or publishing objects, verify_checkpoint_continuation
checks the exact manifest/proof/opening-time envelope, revalidates WP6-2T both at
the proof's recorded time and at opening time, rejects stopped/pending prior state,
and pins run/plan/params/strategy/engine identity, prior checkpoint and complete
signed anchor hashes. Manifest time follows recorded verification/retention and
precedes opening; opening monotonic time exceeds the prior checkpoint event.
Authentic opening time, live process continuity, trusted endpoint and nonce/current
head acquisition remain upstream. Passing these local checks is not proof of them.

After writer claim, package, result and p6.checkpoint_admission.v1 commit are
published before manifest/payload/event. Final descriptor/root checks precede
acknowledgement. Any admitted write failure closes the writer and leaves partial
objects; no retry or alternative manifest is published. Preclaim validation failures
publish nothing and leave the caller-owned writer available to close.

A gated diagnostic requires bind_intake, bind_prior_state and bind_prior_receipts.
Prior state must use the exact full checkpoint bundle selected in the admission,
not only an equal K hash. Existing bindings pin actual strategy/reducer inputs,
receipt ancestry and segment sequence. Immediately before strategy.advance, the
sampled clock must use the prior state's clock session and WP6-2T freshness is
rechecked at callback start. Failure is terminal before callback. Diagnostic
invocation v5 retains v4 fields and adds checkpoint_admission_hash. The ancestry
reader accepts this new hash reference but does not recursively resolve it.

Evidence schema p6.checkpoint_admission.v1: schema_version, run_id, session_id,
package_hash, validation_hash, prior_checkpoint_hash, prior_anchor_hash.
Validation schema p6.checkpoint_continuation_validation.v1: binding_status,
reason_code, prior_checkpoint_hash, last_anchor_hash, next_segment_seq,
runtime_accepted=false, independent_retention_verified=false,
external_authority_verification=UPSTREAM_REQUIRED, plus schema_version.
Package: manifest, proof, utc_ms. No older schemas are silently relabelled.

Tests use temporary durable reservations, actual bound diagnostic/bar/checkpoint
calls and synthetic signed heads. Cover actual continuation through next checkpoint,
missing bindings, all6 constructor publication failures, mismatched/rehashed prior
bundle, stale-at-open or stale-at-callback heads, foreign thread, root substitution,
caller mutation and publication order. This is bounded component integration,
not full Phase1–6 integration or scientific/prospective acceptance.

Remaining: mandatory genesis/start path, service-backed challenge/clock/intake,
owner process/session authority, durable post-checkpoint anchoring/transport lifecycle,
operator CLI and approved future pilot. External acquisition and full historical
execution verification are not inferred from supplied signed artifacts.
