# Phase 6 implementation status — 2026-10-08

This status supersedes the initial planning table's implementation snapshot, not
its acceptance criteria. Phase 6 is not complete or production-verified.

| Package | Implemented and tested | Remaining |
|---|---|---|
| WP6-0 | Versioned architecture and structural contracts, independently reviewed | Runtime deployment bindings and detailed coordinator events |
| WP6-1 | Pure plan/bar/receipt/segment validators; 42 tests | Adapter finality and authentic feed/clock provenance remain upstream |
| WP6-2A | Pure continuous simulation reducer; 24 tests | Durable causal call site, callback serialization, manifest/event/checkpoint binding |
| WP6-2B | Immutable descriptor-bound local blob writer and read-only inventory inspection; 38 tests | Run authorization, reservation registry and governed coordinator integration |
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

Before durable coordinator code, specify exact event payloads, diagnostic strategy
state serialization, initial checkpoint, input-prefix commitment, manifest-before-
callback ordering, and durable intent publication time distinct from callback time.
The reducer's supplied intent timestamp is not evidence of actual publication.
Define callback failure as terminal, not replayable, and freeze the cryptographic
anchor signature and authoritative-head protocol. Only then bind validators,
reducer and object publication into an integrated call site.

The local object store is not a registry and must not allocate/pretend to allocate
run reservations. A persistent diagnostic registry needs its own reviewed append
protocol; separate runs cannot reuse a single-use object root. Do not retrofit old
Phase 5 v2 identities with unknown future hashes.

The operator has no approved provider/receipt service. See
PHASE6-EXTERNAL-SETUP-PROPOSAL.md for a candidate, not activation authority.
No real feed, paid resources, trading account or external anchor has been activated.
H-025 and existing scientific evidence remain excluded. Scientific acceptance is
UNRESOLVED; passing software tests never imply strategy validity or live readiness.
