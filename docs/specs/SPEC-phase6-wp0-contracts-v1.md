# WP6-0 — prospective diagnostic contracts v1

Status: design candidate; not implemented or operationally approved.
Authority: ADR-MIE-004. Profile name: `p6.shadow.diagnostic.v1`.
This document freezes the proposed fields and refusal semantics for review;
synthetic vectors are examples, not research evidence or a real pilot proposal.

## 1. Encoding, primitives and identity

`C(x)` is UTF-8 JSON with sorted object keys, separators `,` and `:`, no whitespace,
no BOM/newline and no ASCII escaping. Reject duplicate keys, invalid UTF-8, NaN,
floats and unknown fields. Integers must be actual integers (booleans forbidden),
within signed64; timestamps nonnegative. Strings are ASCII in this profile.
Decimal amounts/prices are strings matching `-?(0|[1-9][0-9]*)(\.[0-9]*[1-9])?`;
reject negative zero. Decimal arithmetic is exact until a frozen quantization policy;
no binary-float round trips. Price>0, volume>=0. SHA fields are64lowercase hex.
`H(x)=SHA256(C(x))`. IDs never include their own ID. All schema objects below have
exactly the listed fields. For runtime acceptance, hash references must resolve to verified immutable objects,
not merely match a64-character pattern. WP6-1 is a pure structural/content validator;
it accepts explicitly supplied context objects and checks defined local bindings,
with external-reference authority always UPSTREAM_REQUIRED. It cannot grant runtime
acceptance. Unknown schema/profile fails closed.

Plan P fields:
`schema_version="p6.plan.v1", profile="p6.shadow.diagnostic.v1", owner,
program_context_hash, known_lineage_hash, strategy_code_hash, params_hash,
method_contract_hash, provider, instrument, timeframe_ms, start_ms, end_ms,
warmup_bars, max_lateness_ms, max_clock_skew_ms, max_decision_delay_ms,
max_quote_delay_ms, tick_size, quantity_step, initial_cash, max_target,
fee_bps, slippage_bps, max_drawdown, intake_limit_bytes, anchor_trust_hash`.

owner/provider/instrument: nonempty ASCII token `[A-Za-z0-9_.:-]{1,128}`.
Times satisfy start<end, both divisible by positive timeframe; end-start contains
at least warmup_bars+2bars. warmup>=0. Delay/clock bounds nonnegative.
Amounts initial_cash/tick/step>0;0<max_target<=1;0<=fees/slippage<10000;
0<max_drawdown<1. intake_limit_bytes>0. No defaults.
`plan_hash=H(P)`; method_contract_hash must resolve to this profile's execution
and state-policy document plus implementation pins when available; merely supplying
a user-selected hash cannot authorize a different method. program_context_hash is
a provenance reference, NOT a new scientific family ID. known_lineage_hash resolves
the disclosed history, even if incomplete. No `scientific_pass` field exists.

Authorization A fields:
`schema_version="p6.authorization.v1", plan_hash, owner, evidence_root,
registry_root, anchor_endpoint_id, approved_utc_ms, approval_reference`.
Owner matches P; approval_reference nonempty; approved_utc_ms<start_ms.
Roots must resolve to approved physical stores; no symlink/implicit creation.
Authorization is recorded from a real operator decision, not self-granted by code.

Reservation R fields:
`schema_version="p6.reservation.v1", plan_hash, authorization_hash,
registry_identity, sequence, reserved_utc_ms, previous_reservation_hash`.
Sequence starts1, monotonic per registry, previous zero-hash only at genesis.
`run_id=H(R)`; reserved_utc_ms<start_ms. Writer atomically allocates sequence under
exclusive lock, durably publishes R and anchors it before enabling intake.
Existing v2 registry cannot accept R. Reservation counts consumed diagnostic runs,
including failures; no result-dependent deletion. One R per attempt; fresh attempt
requires fresh approval/start interval and disclosure. Segments increment only
segment_seq; they never allocate a hidden new reservation. No statistical N derives
from this counter. Synthetic mode does not touch an actual registry.

Identity DAG: P → A → R/run_id → receipts → segment → manifest → journal/checkpoint
→ anchor receipt → next segment. Checkpoint refers to previous checkpoint, not to its
own external anchor; the next manifest binds the previous anchor. No circular hash.

## 2. Data and clocks

Bar B fields: `schema_version="p6.bar.v1", provider, instrument, open_ms,
close_ms, open, high, low, close, volume`.
close_ms=open_ms+timeframe_ms; UTC interval[open_ms,close_ms). Plan-aligned,
low<=min(open,close)<=max(open,close)<=high and all prices>0.
Final-only adapter policy: a bar must be declared final by the selected provider's
reviewed adapter, not inferred from a hash alone. Raw payload and adapter version
are retained via receipt bindings. One bar per segment, contiguous, no lookahead.

Receipt E fields: `schema_version="p6.receipt.v1", run_id, receipt_seq,
kind, provider_event_ms, received_utc_ms, received_monotonic_ns, clock_session_id,
clock_probe_hash, raw_payload_hash, normalized_payload_hash, previous_receipt_hash`.
kind is BAR_FINAL or QUOTE. Counter starts1 across both kinds, contiguous with no
reuse/gaps; first previous_receipt_hash is64zeroes, each subsequent value equals
H(the exact preceding E), including duplicate/rejected observations. The immutable
receipt body does not contain its own hash. clock_session_id is
a nonempty ASCII token; monotonic values strictly increase within it. A clock-session
change during a run is a stop, not an invitation to join unrelated monotonic clocks.
UTC must not decrease; provider_event_ms<=received_utc_ms+max_clock_skew_ms.
clock_probe_hash resolves T with exact fields `schema_version="p6.clock_probe.v1",
clock_session_id, measured_utc_ms, measured_monotonic_ns, valid_until_monotonic_ns,
offset_bound_ms, source_identity`. T.clock_session_id matches E; source_identity
is a nonempty ASCII token whose trust is pinned by the deployment clock policy.
Require measured_monotonic_ns<=E.received_monotonic_ns<=valid_until_monotonic_ns,
nonnegative offset_bound_ms<=P.max_clock_skew_ms and measured_utc_ms<=received_utc_ms.
The clock probe's source trust is an upstream deployment obligation; pure validation
verifies only structure, bounds and supplied hash bindings, never claims a real
clock was measured. A clock probe is not proof against a compromised host. Raw receipts are append-only; writer anchors batches at boundaries.
BAR_FINAL payload hash=H(B); require received_utc_ms>=B.close_ms and lateness<=bound.
QUOTE payload Q fields: `schema_version="p6.quote.v1", provider, instrument,
bid, ask`;0<bid<=ask, provider/instrument match P. A quote is a market observation,
not a fill. It is excluded from strategy features in this bar-only profile.

Same bar key with identical normalized bytes: retain duplicate receipt and mark
DUPLICATE_IGNORED in intake journal, no second segment/decision. Different content
for an already seen bar key: REVISION_CONFLICT, stop, preserve both raw observations.
No accepted-bar rewrite. Missing bar at close+max_lateness: DATA_GAP, stop. Late data
is retained but cannot reopen the gap. On-time bars queue in bounded durable intake;
if processing/anchoring cannot keep up with delay/size bounds, stop with LAG_EXCEEDED.
No offline historical backfill is admitted as contemporaneously observed data.

Segment S fields: `schema_version="p6.segment.v1", run_id, segment_seq,
start_ms, end_ms, bar_hash, accepted_receipt_hash, previous_segment_hash`.
Sequence starts1; previous zero only at genesis. Exact bar interval and receipt
binding; `segment_id=H(S)`. Data file canonical bytes must hash to bar_hash.
Closure time is receipt time; closing a segment does not authorize a historical fill.

## 3. Execution, state and chronology

Manifest M fields: `schema_version="p6.session_manifest.v1", run_id,
segment_id, plan_hash, engine_version, engine_code_hash, strategy_code_hash,
params_hash, prior_checkpoint_hash, prior_anchor_hash, persisted_utc_ms`.
Only a new versioned engine named by the implementation contract is allowed; old
virtual replay engines cannot be relabeled. Genesis references an explicitly frozen
initial checkpoint and its anchor. M durably persists and verifies before any
strategy callback. `session_id=H(M)`.

One callback per accepted bar, after manifest persistence, with only previously
accepted bar prefixes including the current completed bar. During first warmup_bars,
record WARMUP and generate no orders/scored return. Warmup uses only actual intake
inside the plan interval. Decision must occur within max_decision_delay of receipt;
record actual UTC and monotonic callback start/end. Later bars may queue but are
not visible to this callback. No decision/fill timestamp may be synthesized from
bar open or shifted backwards to disguise late processing.

Strategy outputs a target0..max_target from copied inputs/params; mutation or invalid
output stops. Long-only spot; no borrowing, shorts or external order submission.
Only one pending simulated instruction. Size is derived from prior verified state,
latest completed-bar mark and target under frozen method rules. Quantity rounds down
to quantity_step, never exceeds cash/holdings/max_target exposure; sub-step orders
are explicit NO_TRADE. Sell may reduce to zero. Fees/slippage are exact plan amounts.

A simulated fill may use only the first accepted QUOTE received strictly after
ORDER_INTENT publication in the same clock session (monotonic ordering).
Buy price=ask*(1+slippage_bps/10000), rounded UP to tick; sell price=
bid*(1-slippage_bps/10000), rounded DOWN. Fee=abs(quantity*fill_price)*fee_bps/10000.
Cap buy quantity to available cash including fees; sells<=held units. If cap yields
zero, record REJECTED, never fabricate a fill. Quote timeout stops with pending
intent preserved. Prices/fees remain MODELED even though reference quotes are real.
At most one fill per intent. Quotes already received before intent, OHLC extrema
and next bar open cannot be used to fill retroactively. No position closes merely
because a bar/segment ended. Drawdown breach stops before further strategy actions;
any terminal exposure remains reported, not automatically liquidated by this version.

Checkpoint K fields: `schema_version="p6.checkpoint.v1", run_id, session_id,
segment_seq, cash, units, cumulative_fees, cumulative_realized_pnl, cost_basis,
mark_price, equity, peak_equity, strategy_state_hash, accepted_prefix_hash,
last_receipt_seq, pending_intent_hash, journal_head_hash, journal_count,
previous_checkpoint_hash, stopped`.
Amounts use exact decimal strings. equity=cash+units*mark; peak=max(previous peak,
equity). Cost basis is total remaining acquisition notional excluding fees; average
cost handles partial sells, zero position forces zero basis; fees accounted once
in cash and separately cumulative_fees. Realized P&L excludes fees and must be
labeled gross. Complete journal includes input/output strategy-state artifacts;
no hidden mutable state outside those bindings. Versioned strategy serialization
must be explicitly supported; unknown strategy state is refused.
Initial checkpoint is a separate `p6.genesis.v1` object with exact fields
`schema_version,run_id,plan_hash,cash,units,cost_basis,cumulative_fees,
cumulative_realized_pnl,equity,peak_equity,strategy_state_hash`;
cash=equity=peak=initial_cash; all other amounts0. No future market mark in genesis.

## 4. Local event protocol and external anchor

Journal J fields: `schema_version="p6.event.v1", run_id, session_id, event_seq,
kind, utc_ms, monotonic_ns, payload_hash, previous_event_hash`.
Sequence starts1 for each session; genesis previous hash=H(M); payload is a separately
persisted exact-schema artifact under the implementation method contract. K binds
journal head/count; journal must not embed K's final hash (avoids a cycle).
Kinds: MANIFEST_VERIFIED, BAR_ACCEPTED, WARMUP, DECISION, NO_TRADE, ORDER_INTENT,
SIMULATED_FILL, REJECTED, CHECKPOINT_PREPARED, STOP. No untyped runtime payloads may
be implemented without reviewed per-kind schemas and chronology test vectors.

Anchor receipt X fields: `schema_version="p6.anchor_receipt.v1", run_id,
anchor_seq, subject_kind, subject_hash, previous_anchor_hash, retained_utc_ms,
service_identity, signature`.
subject_kind RESERVATION, GENESIS or CHECKPOINT. anchor_seq starts1 for RESERVATION
(previous_anchor_hash=64zeroes),2for GENESIS, then one per checkpoint, contiguous.
Each predecessor equals H(the entire preceding signed X). Subject hashes resolve
respectively R, genesis or K for the same run. Anchor service must independently
retain an append-only sequence, refuse conflicts and sign canonical unsigned fields
using the algorithm/public key pinned by anchor_trust_hash. WP6-0 interface contract
requires cryptographic verification; selecting algorithm/key/service is deferred
until the concrete implementation/deployment contract, never inferred from a local
receipt. No local file copy counts as independent anchoring.

Idempotency key=(run_id,anchor_seq); same content may return same stored receipt,
conflicting content refuses. For a lost acknowledgement only read-only lookup of
that exact key is permitted; no second send, rebasing or sequence reuse. A verified
matching receipt may complete the anchoring step only while the process/clock session
remains alive and all bounds hold. Unknown/absent receipt stops with ANCHOR_UNKNOWN.
Anchor failure never allows the next callback/segment to execute. Earlier stored
receipts cannot prove absence of later ones: verification requires authoritative
current service head. Offline verification reports LOCAL_ONLY, never VERIFIED.

Storage: caller-approved roots, exclusive descriptor-bound writer, O_NOFOLLOW or
refusal, create-exclusive artifact publication, file fsync then parent fsync;
no overwrite of immutable blobs. Intake, governance and session journals have
separate owners but cross-bind hashes. No atomicity claim across these stores or
remote anchoring. Unpublished or partially published files remain partial evidence.

## 5. Coordinator state transitions and failures

| From | Required durable fact/action | To |
|---|---|---|
| PREPARED | Real A, consumed R, reservation+genesis anchored before start/intake | READY |
| READY / ANCHORED | S frozen; previous K/X verified; M published | MANIFESTED |
| MANIFESTED | M verified before callbacks | EXECUTING |
| EXECUTING | Input-limited decision and any fill resolved; J/K verified and durable | CHECKPOINTED |
| CHECKPOINTED | Independent matching X and current anchor head verified | ANCHORED |
| ANCHORED | Last scheduled bar completed, no pending intent | COMPLETED |
| Any nonterminal | Refusal, interruption, unknown outcome, operator stop | STOPPED |

PREPARED is metadata only; no market intake before READY/start. READY waits until
start. STOPPED/COMPLETED are terminal. No resume/retry in same run after process
restart, clock discontinuity, missing evidence or unknown write result. If a crash
prevents STOP persistence, inspector reports INCOMPLETE; never manufactures STOP
or COMPLETED. Read-only inspect/verify may always report authentic partial facts.
end_ms closes the last data interval, not its arrival window. Accept the final
BAR_FINAL through end_ms+max_lateness_ms inclusive, with close_ms=end_ms. Never
accept bars outside the plan interval. Process queued final bars within their own
decision bound, then permit the final pending quote through intent time plus
max_quote_delay_ms inclusive. A missing final bar at end_ms+max_lateness_ms stops DATA_GAP;
an unresolved intent at its quote deadline stops QUOTE_TIMEOUT. Terminal exposure
is explicitly unliquidated. COMPLETED
means pipeline completed, not a return threshold passed.

| Failure window | Required behavior |
|---|---|
| Before R commit is proven | No intake; inspect registry/anchor, no blind reserve retry |
| After reservation, before M | Reservation consumed; retain receipts/partial segment |
| After intent, before durable result | EXECUTION_UNKNOWN; no repeated simulated execution |
| After K, before X | No next session; exact anchor lookup only while process remains valid |
| Lost process or clock continuity | INCOMPLETE/STOPPED, no same-run continuation |
| Hash/head mismatch or substituted paths | INTEGRITY_FAILURE; preserve evidence |

Control surface (future, not existing commands): pure validate(plan/artifacts),
inspect(existing root), verify(existing root, expected plan/run/head, trusted anchor),
start(exact P/A, existing empty roots), stop(run_id). No defaults or automatic schedule.
Verification performs no market-provider call, strategy evaluation, trial reservation
or evidence writes. It may query the independent anchor read-only. Locks do not
permit content mutation. A running run cannot yield a terminal VERIFIED report.

## 6. Refusal precedence and reports

Pure validation precedence: SCHEMA_INVALID → IDENTITY_MISMATCH → POLICY_UNSUPPORTED
→ CLOCK_INVALID → DATA_INVALID → SEQUENCE_INVALID → HASH_MISMATCH.
Within each group, validate fields in the order listed above, then cross-bindings.
Runtime refusals: AUTHORIZATION_INVALID, STORAGE_REFUSED, RESERVATION_UNKNOWN,
DATA_GAP, REVISION_CONFLICT, LAG_EXCEEDED, CLOCK_INVALID, STRATEGY_INVALID,
RISK_STOP, QUOTE_TIMEOUT, EXECUTION_UNKNOWN, ANCHOR_UNKNOWN, INTEGRITY_FAILURE,
OPERATOR_STOP. No arbitrary exception text becomes a stable reason code; unknown
exceptions produce INCOMPLETE plus diagnostic log outside the closed result schema.

Report fields: `schema_version="p6.report.v1", run_id, plan_hash,
lifecycle, technical_verification, scientific_acceptance, complete_family_N,
promotion_authorized, prospective_strategy_validated, last_checkpoint_hash,
last_anchor_hash, reason_code`.
lifecycle PREPARED/READY/MANIFESTED/EXECUTING/CHECKPOINTED/ANCHORED/COMPLETED/STOPPED/
INCOMPLETE. technical_verification NOT_EVALUATED/LOCAL_ONLY/VERIFIED/INVALID;
VERIFIED only independently anchored COMPLETED chain. scientific_acceptance always
UNRESOLVED, complete_family_N=null, promotion_authorized=false,
prospective_strategy_validated=false for this diagnostic profile. Hash refs may be
null only when never produced. reason_code null only for no recorded refusal;
INCOMPLETE does not imply an invented reason. No performance-dependent promotion.

## 7. Acceptance vectors and remaining implementation contracts

Companion `phase6-wp0-synthetic-vectors.json` contains synthetic canonical bar vectors and expected hashes. They
are not real prices or runtime evidence. WP6-1 must independently verify these bytes,
plus refusals for each primitive/schema/binding rule before any storage is introduced.

Required later tests: future/unclosed/late/revised/duplicate bar; clock rollback and
session change; delayed callback with historical quote; prefix leakage; pending
intent across boundary; capital/state continuity; double fee/fill; atomic reservation
conflict; every failure window above; wrong/missing/stale anchor; forged whole-tree
replacement; offline verification; no resume after crash; scientific-field tampering.

This contract is complete for proposed ownership, record envelopes and first data
profile. It is NOT a ready-to-implement numerical engine or full durable protocol.
Before WP6-2 implementation, freeze per-event payload schemas, strategy serialization,
transaction layout, max-target sizing/risk-mark details, anchor algorithm and test
fixtures in dedicated implementation contracts; review them independently. WP6-1
is limited to pure plan/bar/receipt/segment structural/content validation, not runtime
acceptance. Input is `{document, plan, prior_receipts, prior_segment, payloads,
clock_probes}` supplied in memory (unused optional context slots are null or empty
lists/maps as appropriate); no filesystem/network resolution is performed.
A validator returns exactly `{schema_version:"p6.validation.v1", document_kind,
structural_status, reason_code, canonical_hash, external_reference_verification,
runtime_accepted}`. document_kind PLAN/BAR/RECEIPT/SEGMENT; structural_status
VALID/INVALID; reason_code uses the pure precedence above (null when VALID);
canonical_hash=H(document) only when VALID, else null;
external_reference_verification always UPSTREAM_REQUIRED; runtime_accepted=false.
Required defined local context missing is IDENTITY_MISMATCH, not an external success.
Method pin acceptance, real clock provenance, provider finality, policy lineage,
authorization and anchor trust verification remain upstream until their implementation
contracts are frozen. Thus VALID means only the declared structural/content checks
passed. No test may interpret it as permission to intake or execute.
No real provider/root/strategy/anchor is selected; WP6-5 approval remains separate.
