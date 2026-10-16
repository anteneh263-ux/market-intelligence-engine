# WP6-2S — supplied prior receipt ancestry binding v1

2026-10-16. bind_prior_receipts(proof) is optional at owned BAR_ACCEPTED/count2 after
successful checkpoint-mode bind_prior_state and before diagnostic. Genesis and
unbound legacy histories cannot satisfy this proof. Retain the prior-state package
privately and snapshot it with supplied proof. No historical artifacts are rewritten.

Proof exact fields: diagnostic_invocation,intake_binding,intake_bundle,
intake_validation,execution_invocation,quote_binding,quote_bundle,quote_validation.
Quote fields must be null on bar-only checkpoints. All schemas are dispatched
exactly. Diagnostic v2/v3/v4 supports its recorded intake hash; v1 is unsupported.
Execution supports bar invocation v1 or bound quote invocation v2. Canonical hashes
bind invocations to supplied prior diagnostic/execution commits, intake record to
diagnostic descriptor, bundle/result to intake record. Rerun pure intake validation
and compare exact result instead of trusting supplied VALID. Pin manifest/run/plan/
params/segment identities and bar decision/diagnostic links and diagnostic target.

Quote mode additionally resolves quote binding and bundle/result via quote descriptor;
intake, intent, barrier, selected receipt and derived observation hashes agree.
The extension preserves prior intake maps/receipts exactly, adds only quotes and
preserves bar segments/prefix. This verifies receipt linkage, not another proof of
first eligible timing: the barrier object is not part of this ancestry proof.

Selected prior bundle receipt length equals K.last_receipt_seq and its prefix hash
matches K.accepted_prefix_hash. Its exact receipt/segment prefixes and map entries
must remain in the current supplied intake; plan/reservation match. Previously only
scalar bounds were checked; now altered prior quote receipt bytes refuse even after
current chain is consistently rehashed and passes the prior-state checker.

Pure result p6.prior_receipt_validation.v1 reports supplied_receipt_ancestry_verified
only on success, prior_bundle_hash and prior_receipt_head_hash. runtime_accepted and
anchor_verified remain false. This is supplied local content linkage, not proof of
actual historical execution, full event payload ancestry, durable publication or
truth of raw provider/clock data. Earlier v3/v4 state/receipt binding references,
bar/quote raw and adapted execution inputs and full journal ancestry remain unresolved;
this proof does not recursively authenticate all earlier history or expected hashes.

Three publications package/result/p6.prior_receipt_binding.v1 commit before final
root/lifecycle acknowledgement. Binding commit fields: run_id,session_id,
prior_state_binding_hash,package_hash,validation_hash,bar_event_hash. Head/count stay
unchanged. Diagnostic v4 extends v3 with prior_receipt_binding_hash. Existing optional
paths remain available and do not claim ancestry. Failure is terminal INCOMPLETE;
no retry/rebind or restart authority. No new real evidence roots/services/pilot.
