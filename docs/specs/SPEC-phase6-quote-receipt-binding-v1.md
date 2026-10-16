# WP6-2Q — durable supplied quote-receipt extension binding v1

2026-10-16. bind_quote_receipts(bundle) requires owned acknowledged run_bar intent,
count4/current intent head, prior bind_intake, no quote execution or prior quote
binding. Retain complete original intake bundle privately. One bounded snapshot is
validated and persisted; any admitted failure terminal INCOMPLETE, no retry/rebind.

Validate complete extended bundle with verify_intake_prefix. Plan, reservation,
segments, prefix and original receipt list prefix must match canonical bytes exactly.
All prior normalized payload/probe map entries must remain byte-identical. Extension
must be nonempty and QUOTE-only; new bars/duplicate bar receipts at this stage are
explicitly unsupported. Existing max64receipt/map limits apply.

Each appended receipt must share the owned intent barrier clock session. Receipt
mono<=barrierMono requires UTC<=barrierUTC; mono>barrierMono requires UTC>=barrierUTC.
Crossing clock pairs refuse. Eligible means mono>barrierMono and UTC>=barrierUTC.
Exactly the final receipt must be the first eligible in the supplied extension;
earlier pre-barrier quotes are retained, no earlier eligible observation is skipped.
A quote after timeout may bind and produce the existing QUOTE_TIMEOUT result.
This proves ordering only within supplied observations, not completeness of a live
stream or true first market observation. Raw source/adapter/clock trust is upstream.

Three publications: whole bundle, validation result, p6.quote_receipt_binding.v1
with run_id,session_id,intake_binding_hash,barrier_hash,intent_event_hash,bundle_hash,
validation_hash,quote_receipt_hash. Final root/lifecycle checks precede private ack;
intent head/count remain unchanged. Store detached exact quote observation and seq.

On intake-bound path run_quote now requires quote binding, exact observation bytes
and receipt hash before reducer execution. No fallback to unchecked quote reference.
Bound invocation p6.quote_invocation.v2 adds quote_binding_hash to prior v1 fields.
Legacy wholly unbound path retains v1 semantics. This intentionally tightens the
optional bound path; its previous tests now supply real synthetic receipt artifacts.
Fills/rejections/timeouts keep existing typed event and arithmetic semantics.

After actual bound quote call, checkpoint last_receipt_seq must equal exact bound
quote sequence; without quote it remains bound bar sequence. Result remains LOCAL_ONLY,
runtime_accepted=false,external_reference_verification=UPSTREAM_REQUIRED. No claim
of independently observed time, authentic provider data, prior-state continuity,
external anchors, runtime admission or restart. No actual services/roots activated.
