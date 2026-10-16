# WP6-2P — durable optional intake binding v1

2026-10-16. EventJournal.bind_intake(bundle) is an additive local call path. It
requires actual acknowledged event count2 BAR_ACCEPTED, no prior binding or
strategy call. One bounded detached bundle is validated by verify_intake_prefix,
persisted and retained. No rebind; admitted failures stop INCOMPLETE without retry.

Require plan hash=M.plan_hash, reservation hash=M.run_id, final segment hash=
M.segment_id. Final receipt must be that segment's accepted BAR receipt, excluding
trailing quotes/duplicates at this stage. Captured BAR_ACCEPTED segment/receipt/bar/
prefix fields must equal derived values. Receipt UTC precedes manifest persistence
and BAR event; receipt monotonic precedes BAR event. Clocks remain supplied, not
independently authenticated. Three publications: exact bundle, validation result,
p6.intake_binding.v1 {run_id,session_id,bundle_hash,validation_hash,bar_event_hash}.
Final root/lifecycle checks precede private context acknowledgement. Event head/count
unchanged; binding commit does not pretend to be another WP0 event or checkpoint.

When bound, run_diagnostic requires exact plan and segment sequence. Its descriptor
is p6.diagnostic_invocation.v2: all v1 fields plus intake_binding_hash. Unbound v1
bytes/behavior remain unchanged. The fixed diagnostic strategy consumes segment
identity/count only; this change does NOT claim numerical bar features were passed
to strategy.advance. It links the validated segment lineage to that actual call.

Bound run_bar requires exact canonical types/values for close_ms,close,received UTC,
received monotonic and clock_session_id from the captured bar/receipt. Existing
checks still bind decision times/config/state. Caller mutation cannot replace the
captured data. Quote input remains on the previous unresolved receipt path.

Bound checkpoint keeps the validated accepted-prefix hash. Without quote execution,
last_receipt_seq must equal bound bar receipt sequence. After actual quote execution
it must be greater; quote receipt content, exact sequence and intervening events
are still unverified. This deliberately does not certify the entire intake chain.
Actual caller-supplied prior reducer state and prior checkpoint continuity also
remain upstream. No runtime admission, resume, live completeness or source trust.

Return p6.intake_binding_result.v1 with bundle_hash,commit_hash,
technical_verification=LOCAL_ONLY,runtime_accepted=false,
external_reference_verification=UPSTREAM_REQUIRED. No real evidence root/provider
or service is created. Tests use temporary object roots and synthetic data only.
