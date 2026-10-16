# WP6-2W — Durable supplied checkpoint acknowledgement v1

Scope: continued Phase6 implementation and synthetic integration tests, base
f0fd74dfe5c345a257a37dddb88ac388250df53e. No new actual service, provider, pilot,
evidence-root or historical artifact. This does not send an anchor request.

EventJournal.bind_checkpoint_anchor(proof, clock=...) is available only after
this writer's acknowledged prepare_checkpoint and only on an explicitly admitted
genesis or checkpoint-continuation path. It accepts the WP6-2T chain proof with
one appended checkpoint and signed receipt. It requires the exact previously
admitted start package, checkpoint bundles and signed receipt prefix. Current
K, checkpoint commit, event and manifest must match the journal's retained local
hashes. Merely valid alternate supplied artifacts or signatures are insufficient.

The verifier detaches inputs before sampling clock. Clock must retain the execution
session, follow the final event in UTC/monotonic time, and follow recorded head
verification time. WP6-2T is rerun at both recorded and sampled times, including
fresh signed head/challenge, exact subjects, sequence and retention horizon.
Actual trusted clock, service head/nonce acquisition and independent retention
remain upstream. The callable clock is not automatically trusted authority.

Three ordinary object-store publications occur only after all checks:
1. Package {proof, observed}, where observed is sampled clock record.
2. Recomputed p6.checkpoint_anchor_validation.v1 result at observed UTC.
3. p6.checkpoint_anchor_binding.v1 commit with run_id, session_id, checkpoint_hash,
   checkpoint_commit_hash, admission_hash, anchor_hash, package_hash, validation_hash.
The commit also contains schema_version. Final root/lifecycle checks precede ack.

Success transitions CHECKPOINTED_LOCAL to CHECKPOINT_ANCHOR_BOUND_LOCAL, leaving
journal event head/count and checkpoint bytes unchanged. It does NOT transition to
ANCHORED/VERIFIED or authorize a successor/restart. All further event/callback or
anchor-binding calls refuse. close verifies full acknowledged inventory and closes
from either checkpoint state. A terminal timeout checkpoint may be bound, but its
stopped/pending state still forbids continuation under WP6-2U.

An admitted validation/publication/reentry/root failure becomes INCOMPLETE and
closes the writer with any partial objects retained. No resend/retry/recovery is
implemented. Wrong-thread or already sealed lifecycle calls refuse before entering
and do not poison the owning caller. Legacy ungated checkpoints refuse binding.

Result p6.checkpoint_anchor_binding_result.v1: package_hash, validation_hash,
commit_hash, checkpoint_hash, anchor_hash, technical_verification=LOCAL_ONLY,
runtime_accepted=false, independent_retention_verified=false,
external_authority_verification=UPSTREAM_REQUIRED, plus schema_version.

Tests use actual temporary admitted genesis/continuation executions, warmup,
simulated fill and terminal timeout checkpoints, followed by synthetic signatures.
They cover exact local/prefix identity (including rehashed valid alternatives),
missing/stale/wrong evidence, all3 uncertain publications, root substitution,
clock reentry, ownership, caller mutation, sealing and close. This proves bounded
component integration, not independent remote retention or full Phase1–6 validity.

Remaining: trusted anchor transport/current-head acquisition, process/nonce/clock
ownership and unified session/operator lifecycle, actual provider intake/CLI and
approved prospective pilot. Full Phase1–6 integration follows separately.
