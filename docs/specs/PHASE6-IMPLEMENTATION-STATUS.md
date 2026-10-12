# Phase 6 implementation status — 2026-10-12

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
