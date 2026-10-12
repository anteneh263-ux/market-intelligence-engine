# WP6-2F — pre-start identity and anchor binding v1

Pure integration under ADR-MIE-004. Connect previously separate plan, authorization,
reservation, initial checkpoint and signed anchor checks. No store creation, network,
clock collection, reservation allocation or admission command. No existing schema
changes. A positive result never starts a run or claims real approval/retention.

`verify_start_bindings(request)` takes exact fields plan,authorization,reservation,
genesis,registry_identity,strategy_state_hash,anchor. registry_identity and
strategy_state_hash are expected lowercase SHA256 commitments from the enclosing
approved registry/method context; their external authenticity remains upstream.
Anchor fields exactly trust,receipts,head,challenge,now_utc_ms. Caller cannot choose
expected_subjects/run/trust hash: derive them from the checked plan/reservation/genesis.
Require a non-null signed head/challenge and exactly two receipts for R then genesis.
Reuse WP6-1 plan validation and WP6-2D signature/head validation; no crypto duplication.

Snapshot plain primitives before checks (depth8,10000values,list1000,dict64,
ASCII strings4096/keys128); no custom hooks, input mutation, floats or file reads.
Plan must pass existing validator. A exact WP0 schema/fields; nonempty ASCII approval
reference<=512, owner and plan_hash match P, endpoint WP0 token and matches pinned
trust endpoint_id. Both roots absolute normalized distinct disjoint paths, no null bytes or H025
component; this lexical check does NOT establish physical path/approval authority.
No symlink resolution or filesystem access in this package.
R exact WP0 schema, registry_identity=expected identity, plan_hash=H(P),
authorization_hash=H(A), sequence true signed64>=1, previous hash zero iff sequence1.
Registry prefix continuity beyond this local predecessor rule remains upstream.
A.approved_utc<=R.reserved_utc<=now<plan.start_ms; all true nonnegative signed64.

G exact WP0 p6.genesis.v1 fields; run_id=H(R),plan_hash=H(P),strategy_state_hash=
expected strategy hash, cash=equity=peak_equity=P.initial_cash; units,cost_basis,
cumulative_fees,cumulative_realized_pnl all canonical string0. Genesis cannot carry
a position, pending instruction, market mark or unsupported fields.

Call verify_anchors with expected_trust_hash=P.anchor_trust_hash,
expected_run_id=H(R),expected_subjects=[RESERVATION H(R),GENESIS H(G)] and the supplied
anchor fields. Require VALID/MATCHED. Then require both retained times>=R.reserved,
retained times<=now and <start, challenge.issued>=R.reserved and head.observed<start.
Thus pre-start receipts cannot be reused from a later run or invented before this
reservation. Existing signature checker still applies clock-skew/age/trust/retention
bounds. These are checks on supplied times, not proof of authentic clock acquisition.

Response exact: schema_version=p6.start_validation.v1,binding_status=VALID/INVALID,
reason_code,anchor_reason_code,run_id,genesis_hash,last_anchor_hash,
runtime_accepted=false,external_authority_verification=UPSTREAM_REQUIRED.
Failure clears all hashes. Staged refusals: SCHEMA_INVALID, PLAN_INVALID, IDENTITY_MISMATCH, preliminary
CLOCK_INVALID, then ANCHOR_INVALID, then post-signature CLOCK_INVALID. Anchor
failure wins over post-signature timing failures; preliminary timing wins over
anchor failure. anchor_reason_code set only when
WP6-2D returns invalid; offline/missing head is ANCHOR_INVALID with null detail.
Post-signature chronology failure is CLOCK_INVALID. Also require the signed
retention_until_ms strictly beyond plan.end_ms + max_lateness_ms +
max_decision_delay_ms + max_quote_delay_ms; insufficient horizon is ANCHOR_INVALID
with anchor_reason_code=RETENTION_HORIZON_INSUFFICIENT. This is a declared minimum
run window, not proof of archival retention or physical storage enforcement. No technical VERIFIED/READY.
Unknown system/programming exceptions propagate; never convert to acceptance.

Required integration test produces R using the actual diagnostic ledger on a new
temporary root, supplies synthetic test-key signed reservation/genesis/head, then
checks the composed pre-start binding. Adversarial tests change reservation,
authorization, genesis balances/state/identity, receipt kinds/head, trust pins,
start boundaries and source input after validation. This is a coordinator prerequisite,
not an implemented operational start barrier. Actual verified registry/approval,
physical root ownership, code/state resolution, fresh nonce, trusted clocks and
externally retained service access must be enforced by the future coordinator.

The local envelope/P/A/R/G checks use the outer refusal vocabulary. Internal trust,
receipt, challenge and head schema failures are delegated to WP6-2D and appear
as ANCHOR_INVALID with its SCHEMA_INVALID detail after preliminary checks.
