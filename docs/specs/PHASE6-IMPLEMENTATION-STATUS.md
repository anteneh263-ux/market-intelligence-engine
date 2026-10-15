# Phase 6 implementation status — 2026-10-15

This status supersedes the initial planning table's implementation snapshot, not
its acceptance criteria. Phase 6 is not complete or production-verified.

| Package | Implemented and tested | Remaining |
|---|---|---|
| WP6-0 | Versioned architecture and structural contracts, independently reviewed | Runtime deployment bindings and detailed coordinator events |
| WP6-1 | Pure plan/bar/receipt/segment validators; 42 tests | Adapter finality and authentic feed/clock provenance remain upstream |
| WP6-2A | Pure continuous simulation reducer; 24 tests | Durable causal call site, callback serialization, manifest/event/checkpoint binding |
| WP6-2B | Immutable descriptor-bound local blob writer and read-only inventory inspection; 38 tests | Run authorization, reservation registry and governed coordinator integration |
| WP6-2C | Durable input/manifest/reducer/state/commit integration; 28 tests, independently reviewed | Full governed session manifest, strategy callback, authentic clocks and anchor barrier |
| WP6-2D | Pure Ed25519 receipt-chain and challenged-head validation; 47 tests, independently reviewed | Real receipt service, authenticated read-only fetch, nonce lifecycle and retention enforcement |
| WP6-2E | Durable diagnostic P/A/R reservation ledger; 38 tests, independently reviewed | Authentic authorization/time, reservation anchoring and full coordinator admission |
| WP6-2F | Pure P/A/R/genesis/signed-anchor pre-start binding integration; 33 tests, independently reviewed | Authentic external admission controls |
| WP6-2G | Durable candidate P/A/R/G, fresh local nonce and binding request/result; 40 tests, independently reviewed | Actual runtime admission and service/clock provenance |
| WP6-2H | Canonical diagnostic strategy state and all ten WP0 event payload types/local links; 99 tests, independently reviewed | Causal call site and full lifecycle validation |
| WP6-2I | Immutable typed event journal, manifest/payload/event ordering and terminal failure behavior; 26 tests, independently reviewed | Full event lifecycle, reference resolution and checkpoint binding |
| WP6-2J | Durable fixed-strategy invocation after manifest/BAR_ACCEPTED, pinned inputs and output/event commit; 40 tests, independently reviewed | Reducer/fill/checkpoint call site, authentic code/data/time and admission |
| WP6-3 | Storage fault and real process-exit cases tested under WP6-2B | All coordinator failure windows, independently anchored chain verification |
| WP6-4 | No operational CLI implemented | Controlled operator interface and traced reports |
| WP6-5 | Candidate public feed and separate receipt-service proposal saved | Exact approved service, clock, owner/roots, future interval, actual prospective run |

WP6-2B is owned by app.research and stores raw bytes by hash in an explicitly
supplied existing empty directory. Persistent ownership claims forbid reopening
for writes. Partial publications remain visible; unknown writes stop the writer.
Inspection returns LOCAL_ONLY and cannot authenticate its expected inventory or
prove a complete prospective history. No actual evidence root was created.

Independent review found and reproduced a same-writer cross-thread byte-budget
race. A failing regression preceded the fix; writers are now bound to their
creating thread. Forked-child close never explicitly unlocks the parent's shared
file description. Final dedicated review passed all 38 storage tests without
remaining blockers in that scope. Process termination tests do not establish
hardware power-loss durability.

## Next implementation contract

WP6-2C now binds internal transition records to the existing reducer and immutable
object publication. The manifest is persisted before the reducer call; committed
state advances only after the commit record is published. Failures forbid retries.
These internal records are not WP0 session manifests/checkpoints or anchor receipts.
An exclusive one-time writer claim prevents overlapping sessions and direct writes
through retained cooperative API references.

Before the full governed coordinator, specify exact WP0 event payloads, diagnostic strategy
state serialization, initial checkpoint, input-prefix commitment, manifest-before-
callback ordering, and durable intent publication time distinct from callback time.
The reducer's supplied intent timestamp is not evidence of actual publication.
Define strategy callback failure as terminal, not replayable. WP6-2D now freezes
Ed25519 trust/receipt/head fields and pure signature checking; the durable service
append/head publication protocol and authenticated transport remain unimplemented.
Only then bind validators,
reducer and object publication into an integrated call site.

The new integration does not call a strategy callback or acquire clock/feed data.
Its caller-supplied timestamps do not establish actual intent publication time;
prospective quote eligibility and independently anchored continuation are unresolved.
WP6-2D adds an optional phase6-anchor extra pinned to cryptography50.0.2.
Tests used /private/tmp/mie-phase6-anchor-runtime; the base environment is unchanged.
A real missing-backend check returned DEPENDENCY_UNAVAILABLE. No service was created.
All checks and result fields derive from the same bounded detached primitive snapshot,
closing the mutation-after-signature race found during independent review.
A MATCHED signed head does not prove a live fetch or enforced retention; all outputs
keep runtime_accepted=false and external_service_verification=UPSTREAM_REQUIRED.

The local object store remains separate from the new WP6-2E diagnostic registry.
WP6-2E publishes plan, authorization record and exact WP0 reservation under an
exclusive descriptor-bound lock. Run identity is the reservation hash; distinct
plans consume distinct sequences. Read-only inspection checks explicit expected
publication bytes, not external authority or scientific completeness.

The registry is single-process-lifetime: no reopen after close, failure or restart.
Unknown commits remain preserved and cannot be retried. A future registry continuation
requires its own approved protocol; no automatic new identity or counter reset.
No actual registry was created outside temporary synthetic tests. Supplied approval
references and times are not authenticated by this module. Results remain
RESERVED_UNANCHORED, runtime_accepted=false and complete_family_N=null.
Independent review found a final-root/reentrancy race; negative-first tests then
root and lifecycle rechecks immediately before acknowledgement resolved it.

Next: exact WP0 governed event payloads and diagnostic strategy serialization,
authentic reservation/genesis anchoring before intake, and a coordinator binding
those controls to the tested execution path. Do not retrofit old Phase5 v2 identities
with unknown future hashes. No operational start/CLI or market pilot exists yet.

The operator has no approved provider/receipt service. See
PHASE6-EXTERNAL-SETUP-PROPOSAL.md for a candidate, not activation authority.
No real feed, paid resources, trading account or external anchor has been activated.
H-025 and existing scientific evidence remain excluded. Scientific acceptance is
UNRESOLVED; passing software tests never imply strategy validity or live readiness.

## Pre-start composition implemented

WP6-2F derives expected run, trust and reservation/genesis subjects from checked
P/A/R/G records; callers cannot override those bindings in the anchor request.
Synthetic integration tests allocate R through the real temporary diagnostic ledger
and verify test-key signed reservation/genesis/head records. Genesis balances and
strategy-state commitment are bound, preliminary and signed times remain pre-start,
and declared retention must cover end plus lateness/decision/quote delays.
All checks use a detached primitive snapshot. Lexical paths reject NUL and H025
components; no physical filesystem/approval authority is inferred.

This package does not acquire a nonce/clock, persist an operational genesis, call
an external service or grant READY. runtime_accepted remains false. Next is the
governed startup coordinator: freeze diagnostic strategy serialization and exact
event payloads; persist G and actual receipt/anchor observations in causal order;
require authenticated clocks/service results before intake. A pure VALID binding
result cannot be treated as an admission token.

## Durable local binding attempt implemented

WP6-2G exclusively claims a fresh object writer whose owner, run and root match
the checked candidate context. It publishes P/A/R/G before generating a fresh
nonce, then persists the challenge. The response and internally assembled request
are persisted before WP6-2F checking; the result binds the exact request/challenge.
Final close and complete read-only inventory inspection precede acknowledgement.
Foreign callers are refused; callback reentry, publication uncertainty and final
verification failures are terminal INCOMPLETE, with partial bytes preserved.

BOUND_LOCAL means only that supplied evidence passed local checks. It never grants
READY, starts intake, transfers the writer or authorizes execution. Strategy-state
serialization remains caller context, exact anchor trust validation occurs at
finish, and the required clock callable is not proof of authentic time. No actual
service fetch, signature deployment key, retention enforcement or pilot exists.
A persisted VALID result alone does not prove successful final close/inspection.
This is a bounded durable binding transaction, not the completed coordinator.

WP6-2H now freezes the diagnostic strategy-state serialization and WP0 event
payloads below. Next is durable journal and causal call-site integration, with
actual clock/service controls in the governed coordinator. CLI and a separately
approved prospective pilot still follow.


## Diagnostic state and typed event links implemented

WP6-2H adds a pure fixed 25/75 diagnostic strategy under app.paper.session.
Canonical params and state have exact versioned schemas, pinned params hash,
strict-next bar count and preceding segment commitment. Warmup produces no target;
the first post-warmup decision is25%, then75%, regardless of warmup parity. Strict
byte decoding refuses unknown fields, duplicate keys and noncanonical encodings.
No hidden mutable state or callback to a provider exists in this helper.

The pure event validator checks exact payloads for all ten WP0 journal kinds,
manifest/run/session bindings, callback time bounds, local sequence, hashes and
predecessor genesis invariants. A matching event hash proves neither complete
journal ancestry nor referenced execution. runtime_accepted remains false.
Tests compose actual strategy output with the reducer and event hashing; they
are synthetic pure integration tests, not an active durable runtime call site.

Independent review corrected the modeled fill-price limit to3e12 to cover valid
slippage and upward tick rounding. A test derives a1.5e12 fill from actual reducer
cash/units and validates its event payload. Three negative-first regressions
closed predecessor genesis kind/hash/sequence contradictions.99new tests and24
existing reducer tests passed independently; no remaining scoped review blockers.

Next implementation: persist typed events and strategy input/output states in a
single governed causal call site, with manifest-before-callback ordering and
checkpoint binding. Preserve a separate actual intent-publication barrier before
selecting a quote; neither an event envelope timestamp nor a pure valid result
proves that barrier. Full lifecycle, pinned method/params/plan checks, authentic
clock/service integration, operator CLI and approved prospective pilot remain.
Phase6 is incomplete; no scientific acceptance or live readiness is asserted.


## Durable typed event journal implemented

WP6-2I publishes the exact manifest before MANIFEST_VERIFIED, and each exact typed
payload before its WP0 event. Run/session/sequence/previous hash derive from pinned
context and the last acknowledged head. Constructor/append acknowledgement follows
readback and a final physical descriptor/root plus lifecycle check. Uncertain
publications, reentry and invalid appends are terminal INCOMPLETE; no retry/reopen.
Final close inspects the complete expected inventory before reporting CLOSED.

26 synthetic tests cover all ten payload types, real process exit, write-then-raise,
root substitution after publication, foreign callers, callback reentry, immutable
inspection and actual pure strategy/reducer-derived event payload storage. This
last test integrates payload production and storage; it is not the full causal
execution path. Referenced strategy states/receipts remain unresolved by the journal.
Caller timestamps are not authenticated time or actual publication barriers.

STOP is a typed record in this primitive: later locally valid events remain allowed.
CLOSED denotes the local writer/inspection outcome, never COMPLETED or VERIFIED
prospective execution. Full event lifecycle belongs to the future coordinator.
Next: persist and resolve strategy input/output and execution artifacts in the
same governed call site, invoke the strategy only after a durable manifest, bind
checkpoints, and preserve actual intent-publication time before quote eligibility.
Authentic clock/service integration, CLI and separately approved pilot still remain.
No provider/service/real evidence root was activated. Phase6 remains incomplete.


## Durable fixed-strategy invocation implemented

WP6-2J adds EventJournal.run_diagnostic: after durable manifest and locally checked
BAR_ACCEPTED, bind supplied plan/params/state, persist P/params/input state and
invocation descriptor, then invoke the fixed diagnostic strategy once. Output state,
typed decision/warmup event and a linking commit precede acknowledgement. Every
publication failure or callback uncertainty stops the journal without retry; the
last acknowledged head remains unchanged. The commit is not a WP0 checkpoint.

40new tests cover all eight publication windows, zero callback after pre-invocation
failure, exact postconditions, warmup/parity, ownership, reentry and clock failures.
Independent review and66targeted tests (40new+26journal) passed. Negative-first
regressions fixed bool/int mutation equality and a clock-callback race that could
repair returned output or arguments before checking. Output and canonical argument
snapshots are now frozen immediately on return, before further callbacks. The
finished-clock sample follows that bounded copy and is an observed upper bound.

This is an actual local strategy call site with durable inputs/output, not complete
prospective execution. P/M code-pin equality does not authenticate loaded source;
bar/receipt/prefix references, prior state, clock trust and receipt-time decision
bounds remain upstream. runtime_accepted=false and external references remain
UPSTREAM_REQUIRED. No reducer/fill or checkpoint is invoked in this new method.

Next: bind simulated execution and checkpoints to this call site, resolve data and
prior-state artifacts, preserve actual intent-publication barrier before eligible
quote selection, and enforce full lifecycle/admission controls. Authentic external
clock/anchor service, CLI and an approved prospective pilot remain outstanding.
No actual evidence root, provider/service, trading account or pilot was activated.
