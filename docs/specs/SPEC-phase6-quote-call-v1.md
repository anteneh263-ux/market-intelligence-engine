# WP6-2L — local quote-after-barrier adapter v1

Date:2026-10-15. Policy p6.quote_after_barrier.v1. Explicit additive adapter under
ADR-MIE-004; raw reducer/old artifacts unchanged. Not prospective admission.

EventJournal.run_quote(observation,*,quote_receipt_hash,clock) consumes only the
private detached context from this instance's acknowledged run_bar pending outcome.
Require OPEN, count4, same intent head, not previously consumed; no caller state
or barrier substitution. Generic ORDER_INTENT or supplied lookalike cannot authorize.
quote_receipt_hash is hex64, an unresolved external reference; the supplied quote
is the first offered to this method, not proof it was the first market observation.
No rejection retry: early/wrong/malformed offered quote stops INCOMPLETE.

Input is exact reducer.quote observation. Require valid bid/ask, nonnegative int64
UTC/mono, same clock session as captured state/barrier; receivedUTC>=barrierUTC and
receivedMono>barrierMono. Validate raw state has a pending intent, not stopped,
barrier identity/session/intent link matches internal context, and barrier does not
precede raw state's last action. Construct separate adapted state by changing only
pending.intent_utc_ms/intent_monotonic_ns and last_action_utc_ms/monotonic_ns to the
captured barrier. state_check passes; all other fields preserved. Raw state is never
rewritten. Timeout is strictly greater than max_quote_delay in EITHER clock from
barrier; equality remains eligible. This changes the adapter's time origin, not the
legacy reducer algorithm or historical results. No real host-clock proof is implied.

Publish adapted state, observation, then invocation descriptor with exact fields:
schema_version=p6.quote_invocation.v1,policy=p6.quote_after_barrier.v1,run_id,
session_id,bar_commit_hash,raw_state_hash,adapted_state_hash,barrier_hash,
intent_event_hash,observation_hash,quote_receipt_hash. Raw state and full barrier
were published by the owned bar call; descriptor uses those captured identities.
Only after publication/readback and bound/lifecycle checks call reducer.quote ONCE.
Freeze returned state and canonical argument snapshots before any clock callbacks;
reject input mutation. No second reducer call to validate output.

Exact postconditions: timeout updates receipt/action clocks, sets QUOTE_TIMEOUT,
retains pending/mark/equity/peak/assets. Otherwise validate step-aligned actual qty
0..requested, unchanged count/config/bar identity; BUY maximal qty satisfying cash
and post-fill max-target exposure with rounded fee (next step infeasible unless full
request); SELL exactly requested qty, proportional basis floor16 except full sell
uses remaining basis. Price uses ask+slippage rounded up / bid-slippage rounded down;
fee ceil16. Recompute exact assets/basis/gross realizedPnL, midpoint/equity/peak and
risk-stop; compare canonical state. Zero BUY qty clears pending and may risk-stop.

Publish output state. Emit existing SIMULATED_FILL with actual qty/price/fee and
intent/receipt references, or REJECTED/CASH_CAP_ZERO, or STOP/QUOTE_TIMEOUT with
pending_intent_hash=intent event hash. After fill/rejection if state risk-stops,
append STOP/RISK_STOP with pending_intent_hash=null. Each event clock sample occurs
after output publication, matches session, UTC>=receiptUTC and monotonic>=receiptMono,
and passes ordinary journal chronology. Publish payload then event for each;
second event chains to first locally before head acknowledgement.

Final commit exact p6.quote_call_commit.v1,invocation_hash,output_state_hash,
event_hashes (ordered nonempty list). All publication/root/lifecycle checks precede
acknowledging final head/count and consuming quote slot. Normal path7publications:
adaptedstate,observation,descriptor,outputstate,payload,event,commit. Risk adds2.
Return p6.quote_call_result.v1,state,adapted_state_hash,event_hashes,commit_hash,
invocation_hash,technical_verification LOCAL_ONLY,runtime_accepted false,
prospective_quote_ready false,external_reference_verification UPSTREAM_REQUIRED.
Return/snapshot data detached. Any admitted failure terminal INCOMPLETE with last
acknowledged head unchanged; partial bytes retained, no retry/reopen/double fill.

No actual source receipt verification, ordered live quote intake, independent
clock/service authority, method deployment pins, checkpoint or full lifecycle.
A numeric local fill is not market execution, profitability or prospective validity.
Future runtime must prove the first eligible observed quote, actual receipts and
clock trust; this adapter alone does not make a run READY. Only synthetic roots.

Negative-first + dedicated review: causal publication, unchanged raw state, adapter
time origin/deadline equality and both-clock timeouts, sell/full basis, capped buy,
rejection/risk, all7normal and9risk publication failures, mutation/reentry, spoofed
context, early quote/clock mismatch, final root checks, no retry. No H025 changes.
