# WP6-2H — diagnostic state and event payloads v1

Date: 2026-10-12. Owner: app.paper.session for pure diagnostic strategy state;
app.research for pure event checks. Additive implementation under ADR-MIE-004;
no durable store, migration, runtime admission, deployed pilot or method replacement.
The 25/75 diagnostic exercises execution; it is not a researched trading strategy.

## Diagnostic serialization

Params exact: schema_version=p6.diagnostic_params.v1, warmup_bars, targets.
Targets is exactly ["0.25","0.75"], not a tuning surface. warmup_bars is a
nonnegative signed64 integer, never bool. State exact:
schema_version=p6.diagnostic_state.v1, params_hash, bar_count, last_segment_hash.
Hashes are lowercase hex64. bar_count is nonnegative signed64; last_segment_hash
is zero iff bar_count=0. initial(params) returns count0 state bound to H(params).
advance(state,params,segment_seq,segment_hash) requires matching params_hash,
seq=count+1 within signed64, nonzero hash different from the preceding hash.
Return exactly {state,kind,target}; increment count and last hash. Through warmup,
kind=WARMUP,target=null. Thereafter kind=DECISION, target=targets[(count-warmup-1)%2].
The first post-warmup target is .25 irrespective of warmup parity. Inputs unchanged.
No bar prices, quotes, filesystem, network, hidden state, clocks or randomness.
Only immediate duplicate rejection is established, not full segment ancestry.

encode(record) accepts only these two exact record schemas and emits C(record).
decode(bytes) caps4096 bytes, rejects invalid UTF8/ASCII, duplicate keys, floats,
nonfinite values, booleans in integer fields, unknown fields/schemas, and any bytes
not equal to canonical re-encoding (including whitespace, key order, escapes/BOM).
Invalid encode/decode/initial raise ValueError(INPUT_INVALID); advance binding or
sequence mismatches raise IDENTITY_MISMATCH/SEQUENCE_INVALID. Overflow refuses.
Coordinator still must resolve code/method pins, match params.warmup_bars to P,
P.params_hash to H(params), require P.max_target>=.75, validate segments and invoke
exactly once after manifest durability. This helper grants none of those controls.

## Typed payloads

Every payload is an exact dictionary with schema_version equal to
p6.payload.<lowercase event kind>.v1 and the following additional fields:

| Kind | Fields |
|---|---|
| MANIFEST_VERIFIED | manifest_hash |
| BAR_ACCEPTED | segment_hash, receipt_hash, bar_hash, accepted_prefix_hash |
| WARMUP / DECISION | strategy_input_hash, strategy_output_hash, bar_count, target, callback_started_utc_ms, callback_finished_utc_ms, callback_started_monotonic_ns, callback_finished_monotonic_ns, clock_session_id |
| NO_TRADE | decision_event_hash, reason_code |
| ORDER_INTENT | decision_event_hash, side, quantity, clock_session_id |
| SIMULATED_FILL | intent_event_hash, quote_receipt_hash, side, quantity, price, fee |
| REJECTED | intent_event_hash, quote_receipt_hash, reason_code |
| CHECKPOINT_PREPARED | state_hash, strategy_state_hash, accepted_prefix_hash, last_receipt_seq, pending_intent_hash |
| STOP | reason_code, pending_intent_hash |

Hashes hex64; pending_intent_hash alone may be null. Counters positive signed64.
Side BUY/SELL. Quantity positive canonical decimal <=1e30 with <=8 fractional
places; price positive <=3e12 with <=9 places (ask<=1e12, slippage multiplier<2,
upward tick increment<1e12); fee nonnegative <=1e30 with <=16.
Target DECISION canonical0..1 with <=8 places; WARMUP target must be null.
clock_session_id nonempty ASCII token <=128. NO_TRADE reason ZERO_QUANTITY;
REJECTED reason CASH_CAP_ZERO. STOP uses the closed WP0 runtime reason set.
Future meanings require a reviewed version, not arbitrary exception text.

Callback times are nonnegative signed64: manifest.persisted<=started<=finished
<=event.utc; monotonic started<=finished<=event.monotonic. These bounds describe
supplied observations, not authenticated clocks or callback completion proof.
ORDER_INTENT deliberately does not claim its own durable publication timestamp:
an envelope written before persistence cannot prove the post-publication barrier.
A later governed runtime must acquire and preserve that barrier separately.
CHECKPOINT_PREPARED references pre-checkpoint state, never final K (avoids cycle).

## Pure event envelope verification

verify_event({event,payload,manifest,previous_event}) uses one bounded detached
plain-primitive snapshot (WP6-2F bounds). Manifest exactly matches WP0 M fields:
all pins/ids hex64, engine_version ASCII token, persisted time nonnegative signed64.
J exactly matches WP0 fields; kind must be listed above, seqpositive, timesvalid.
Validate previous_event envelope when present; its payload is not provided/resolved.
Schema failures precede IDENTITY_MISMATCH, CLOCK_INVALID, SEQUENCE_INVALID,
HASH_MISMATCH. Bind run=M.run_id, session=H(M) for current and predecessor.
Current utc>=M.persisted; with predecessor utc nondecreasing, monotonic strictly
increasing. First is seq1 MANIFEST_VERIFIED, predecessorhash=H(M); subsequent seq
increments from supplied predecessor, kind cannot repeat MANIFEST_VERIFIED.
For the predecessor too, seq1 iff kind MANIFEST_VERIFIED; when seq1 its own
previous_event_hash must equal H(M). These locally provable genesis invariants
do not authenticate earlier ancestry of a later supplied predecessor.
MANIFEST_VERIFIED payload.manifest_hash=H(M); BAR_ACCEPTED.segment_hash=M.segment_id.
Finally payload_hash=H(payload), previous_event_hash=H(predecessor) or H(M).

Output exact schema p6.event_validation.v1, structural_status VALID/INVALID,
reason_code, canonical_hash (only VALID), external_reference_verification
UPSTREAM_REQUIRED, runtime_accepted=false. No I/O or admission.
This validates one local link, not a whole journal or event state machine. It
cannot prove referenced input/output states, receipts, prefix, fills/arithmetic,
no duplicate/non-adjacent action, max-target sizing, plan delays or callback order.
J lacks clock_session_id: same-clock comparability across events is upstream even
when supplied numeric timestamps increase. No full ancestry from one predecessor.
No existing engine or event artifact is relabeled. Production call site and durable
journal/coordinator remain future work; integration tests compose pure strategy,
canonical bytes and event hashes only, not prospective execution.

## Verification

Negative-first tests; dedicated independent contract and code review. Test strict
round trips, unknown/duplicate/float/bool input, warmup parity and state carry,
changed params, zero/duplicate/missing/out-of-order/overflow counters; all ten typed
payloads and local links, tampered identity/hash/times, unknown fields, detached
inputs, no I/O and the reducer target handoff in synthetic pure integration.
