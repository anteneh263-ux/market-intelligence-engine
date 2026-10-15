# WP6-2M — local durable checkpoint binding v1

2026-10-15. Additive local integration under the Phase 6 contracts. No runtime
admission, independent anchor, restart authority or real evidence root.

EventJournal.prepare_checkpoint(*, last_receipt_seq, clock) requires an actual
acknowledged run_bar, optionally followed by its acknowledged run_quote. Retain
private detached execution state/commit/head/count at those acknowledgements;
require the journal still has that head/count. Retain BAR_ACCEPTED payload only
when the first append is acknowledged at event count 2. Generic appended execution
events cannot substitute for actual calls. Diagnostic state is the captured actual
run_diagnostic output; segment_seq derives from its bar_count, not receipt sequence.

Unresolved pending orders cannot checkpoint. The sole pending case is actual
run_quote ending stopped with QUOTE_TIMEOUT; its pending_intent_hash is the captured
original ORDER_INTENT hash. Terminal exposure is never liquidated. Warmup, zero
quantity, bar risk stop, fill, rejection, quote risk and timeout are supported.

last_receipt_seq must be a native positive int64 at least segment_seq. It remains
an asserted upstream sequence: no receipt-chain verification is claimed. Likewise
accepted_prefix_hash is copied from the retained BAR_ACCEPTED payload, whose actual
referenced prefix is unresolved. Previous checkpoint hash comes from the manifest;
no authentication or prior state continuity is implied by copying the reference.

Six publications in order (including deduplication/readback calls):
1. Captured full reducer output state.
2. Captured canonical diagnostic output state.
3. Exact CHECKPOINT_PREPARED payload with state_hash, strategy_state_hash,
   accepted_prefix_hash, last_receipt_seq, pending_intent_hash.
4. Its typed event, derived from the current acknowledged journal head.
5. Exact WP0 p6.checkpoint.v1 K, binding the new event head/count. Amounts/stopped
   come directly from captured reducer state; segment_seq from diagnostic state.
6. p6.checkpoint_commit.v1 with checkpoint_hash,event_hash,state_hash,
   strategy_state_hash,execution_commit_hash,diagnostic_commit_hash.

A same-session clock sample after both state publications must dominate execution
last-action UTC/monotonic and satisfy ordinary journal event chronology. No authentic
host-clock claim. The event references full state, never K's own hash, avoiding a
cycle. K exact fields remain those of SPEC-phase6-wp0-contracts-v1.md.

After final physical root/descriptor and lifecycle checks, acknowledge head/count
and seal CHECKPOINTED_LOCAL. Further append/run/checkpoint operations refuse without
changing the sealed state. close still closes the writer and verifies its full
expected inventory before CLOSED. Neither status means ANCHORED or COMPLETED.
Every admitted failure is terminal INCOMPLETE, retains last acknowledged head/count
and partial objects, and forbids retry. Foreign callers are refused before admission.

Result p6.checkpoint_result.v1 contains checkpoint_hash,event_hash,commit_hash,
technical_verification=LOCAL_ONLY,runtime_accepted=false,
external_reference_verification=UPSTREAM_REQUIRED. No caller-supplied state,
strategy state, prefix hash, previous checkpoint or journal head can override the
captured context. No scientific validation, deployment or prospective-ready claim.

Tests cover exact causal bindings and all supported outcomes, six uncertain write
windows, invalid sequence, unresolved intent, returned-state mutation, intervening
generic event, clock faults/reentry, final root substitution and sealed close faults.
