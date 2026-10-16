# WP6-2V — Gated first-segment admission v1

Authorized scope: continued Phase6 code, synthetic tests and local save. Base
 a09bf1ca25c53271883a312a2756a7a9a5d4076b. No real service, pilot, evidence-root,
trading account, restart authorization or historical evidence mutation.

open_genesis_session(writer, manifest, proof, utc_ms, monotonic_ns) requires proof
with exactly start and anchor. start is the WP6-2F request checked at its recorded
pre-start time. anchor is a new supplied current signed head/challenge over the
exact same two reservation/genesis receipts, with trust pinned by the plan.
The receipts must remain identical: no appended checkpoint or resigned replacement.
Freshness/signature/retention are checked at both recorded head-validation time
and actual supplied opening time. Retention covers the full plan horizon.
Manifest identity references the same run, plan, params, strategy, genesis and
signed genesis receipt; manifest time is at/after plan start and follows the
current head-validation time. Initial engine identity is still caller-supplied,
not authenticated code provenance. External service/clock/nonce authority and
pre-start process/intake readiness remain upstream.

Pure verify_genesis_admission takes exactly manifest, proof, utc_ms. Result schema
p6.genesis_admission_validation.v1 has binding_status, reason_code, genesis_hash,
last_anchor_hash, runtime_accepted=false, independent_retention_verified=false,
external_authority_verification=UPSTREAM_REQUIRED, plus schema_version.

EventJournal's explicit genesis_admission option and checkpoint_admission are
mutually exclusive. Both default off on legacy constructor paths. The new factory
refuses absent proof. Validation precedes writer claim and all object publication.
After claim, package/result/p6.genesis_admission.v1 commit are durable before the
manifest/payload/event; root and lifecycle are checked before acknowledgement.
Commit fields: schema_version, run_id, session_id, package_hash, validation_hash,
genesis_hash, prior_anchor_hash. Admitted publication failures close the writer,
retain uncertain partial objects and never retry; preclaim refusal publishes no blobs.

The new path requires bind_intake and bind_prior_state with the exact admitted
G document. Existing prior-state checks require the true initial reducer and
strategy states/config, and bound intake pins segment1 and receipt/bar inputs.
Diagnostic invocation v6 retains v3 fields and adds genesis_admission_hash; it
has no prior-receipt-binding field because genesis has no previous intake history.
The supplied ancestry reader accepts v6 but does not recursively authenticate its
admission reference. Before strategy.advance, freshness is checked again at the
sampled callback time and its clock-session id must match the bound first receipt.
Missing bindings, wrong segment or expired head stop before callback.

Tests use synthetic signatures and actual temporary reservation and journal calls:
first segment through warmup or simulated quote fill and checkpoint, v6
receipt-reader compatibility (quote invocation retains v2), missing bindings,
all6 publication failures, root replacement, ownership, mutation, freshness, mixed
admission and altered/resigned start receipts. They establish bounded component
integration only, not full Phase1–6 operation, independent retention or scientific
acceptance. Existing legacy paths remain explicitly available and ungated.

Next: post-checkpoint durable anchor publication/verification lifecycle, service-
backed challenge/clock/intake and process ownership, operator CLI and approved
prospective pilot. Local gates alone do not grant READY/ANCHORED or live execution.
