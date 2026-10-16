# WP6-2T — Supplied checkpoint anchor chain validation v1

Authorized scope: continued Phase6 implementation and synthetic tests. Base
c765dffc48bd2b2e67dfdc2f48b33dc2d923c386. No service deployment, real pilot,
new scientific evidence root or historical artifact modification.

`verify_checkpoint_anchors({start, checkpoints, anchor})` is a pure bounded
validator. `start` is the WP6-2F start-validation request, `checkpoints` is an
ordered list of 1–64 WP6-2N full checkpoint bundles, and `anchor` has exactly
trust, receipts, head, challenge, now_utc_ms. Existing schemas are unchanged.
The shared bounded snapshot allows at most10000 values and depth8;64 checkpoints
is an upper bound, not guaranteed capacity for every bundle shape.

The verifier recomputes start/checkpoint validation and derives expected signed
subjects from actual supplied R/G/K hashes. Caller-selected subjects are forbidden.
It requires the exact original reservation/genesis receipt prefix, fresh signed
head/challenge, pinned trust and signature/sequence verification. Each checkpoint
manifest names the previous checkpoint and complete signed prior receipt. Segment
sequence starts1 with no gaps, run/plan/params/strategy/config match the plan,
engine identity stays constant, receipt counts increase, and a stopped or pending
checkpoint cannot have a successor. Clock session stays constant with increasing
checkpoint monotonic time. Manifest creation follows the previous anchor retention;
checkpoint retention follows its local event and is not in the supplied future.
Current signed retention must cover the full plan horizon. Terminal checkpoints
may be checked but never confer continuation authority.

Result `p6.checkpoint_anchor_validation.v1` reports binding_status, reason_code,
last checkpoint/anchor hashes and checkpoint_count. runtime_accepted,
independent_retention_verified and full_execution_verified remain false;
external_authority_verification remains UPSTREAM_REQUIRED, including on success.

This proves supplied content/signature links only. It performs no filesystem,
network, clock sampling, signature creation, durability or service-retention check.
The trust authorization, nonce freshness/ownership, actual current service head,
authentic clock and retained evidence service remain upstream. The supplied start
head is checked at its recorded time, not treated as a current head. Checkpoint
validation does not resolve full intake, callbacks, execution inputs or historical
journal ancestry. This helper does not activate mandatory runtime admission,
restart, ANCHORED/VERIFIED lifecycle transitions or a full Phase1–6 test.

Tests build temporary reservations and actual persisted diagnostic/bar/checkpoint
calls, then sign with an existing deterministic test-only key. Negative cases
include validly rehashed and resigned but incompatible predecessor references,
identity changes, chronology and receipt counts, as well as signature failures,
stale/offline heads, retention boundaries, missing/reordered bundles and malformed
inputs. No test signing key is a deployment key.
