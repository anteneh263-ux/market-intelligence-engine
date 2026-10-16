# WP6-2X — Local close-before-open successor handoff v1

Scope: continued Phase6 implementation/test/local save, base
2b62722261efc67aa5ce1aa4e5c39ad282bd7ff7. No real provider/service/pilot or new
scientific evidence root. The caller creates both explicit temporary/test writers;
this method does not create directories, stores or remote anchors.

EventJournal.open_successor(writer, manifest, proof, clock=...) consumes only the
same owning thread/process's CHECKPOINT_ANCHOR_BOUND_LOCAL journal. It requires
a separate unclaimed empty live ObjectWriter with the same owner/run and checks
its bound descriptor. It detaches manifest/proof before sampling clock. Clock must
follow the acknowledged anchor sample in the same clock session. It validates
WP6-2U and requires the exact acknowledged start/checkpoint/receipt history. A
fresh signed head/challenge may replace the prior head while the receipt sequence
stays identical. Stopped/pending checkpoints cannot continue. Manifest time cannot
precede acknowledgement. Authentic clock/head/nonce/service authority stays upstream.

Two publications to the previous journal precede closing:
- Package {manifest, proof, observed, writer_root, writer_header}.
- p6.successor_intent.v1 {schema_version, run_id, session_id, next_session_id,
  package_hash, checkpoint_anchor_binding_hash}.
The intent records a requested attempt, not proof that a successor opened.
The previous writer then closes and inspect_objects verifies its full inventory.
Only after that does the existing mandatory checkpoint-continuation constructor
open the successor and publish its six admission/manifest objects. No strategy
callback occurs during handoff. The successor still requires intake/prior-state/
receipt bindings and fresh callback-time admission before execution.

In-memory lifecycle: CHECKPOINT_ANCHOR_BOUND_LOCAL → CLOSED_SUCCESSOR_PENDING →
CLOSED_SUCCESSOR_OPENED_LOCAL. The previous event head/count stay unchanged.
A successful return is a new EventJournal, not a durable cross-store transaction.
There is no claim that two stores and external service commit atomically. No final
success record is added to the closed predecessor. Persisted intent plus successor
admission/manifest must be inspected separately after uncertainty. This implementation
provides no restart/recovery/retry permission or reconstruction of live ownership.

Admitted failure becomes INCOMPLETE, closes the previous writer, retains partial
objects and forbids retry. If a successor has opened before a later failure it is
also stopped. Constructor-owned publication failures close the successor. A next
writer still unclaimed when validation/closing fails remains caller-owned; the
caller may close it but cannot retry this consumed predecessor. Foreign-thread or
already sealed lifecycle calls refuse before admission and do not poison the owner.
Legacy direct factories remain available and are not globally forced through handoff.

Tests include actual bound genesis→checkpoint→ack→closed handoff→next bound callback
and checkpoint; both old intent publications, all six next publications, inventory
failure, physical root substitution, caller mutation, ownership/reentry, terminal
checkpoint refusal and ordering checks. This is local component integration only.
Runtime/independent retention flags remain false in the returned journal.

Remaining: trusted service transport and current-head acquisition, authenticated
process/nonce/clock/intake ownership, full operator orchestration/CLI and approved
prospective pilot. Full Phase1–6 integration remains a separate later validation.
