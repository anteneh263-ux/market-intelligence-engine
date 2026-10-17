# Read-only challenged head assembly v1

Local assembly only, not an authenticated/deployed endpoint. Service-owned version
index, SDK clients, signing key and pinned trust are explicit inputs. No new store.

Read current DynamoDB item strongly consistently; require COMMITTED and exact run,
service and prefix length. Inspect every supplied S3 version through the existing
read-only adapter. Require canonical bytes, exact run/sequence keys, last committed
version/key/hash, and sufficient retention for the claimed prefix horizon. Re-read
the table strongly consistently and reject any item change. The second read is the
snapshot linearization point; an append after it can make a response stale, as with
any snapshot. No claim of atomicity across DynamoDB and S3 is made.

Verify the complete signed chain against pinned trust, reservation identity and
monotonic receipt times. Service observation time must be valid, nondecreasing
within the call, within max head age and earlier than the retained horizon. Sign
p6.anchor_head.v1 with the supplied nonce only after all checks. A client must still
verify its unpredictable one-use nonce, issuance time, authenticated transport and
pinned endpoint. The service does not establish client nonce freshness on its own.

Output runtime_accepted=false. No state/retention mutation, retry of append, repair
or automatic recovery. Version-index persistence, historical idempotency lookup,
clock authenticity, authorization and actual AWS testing remain outstanding. Tests
use simulated SDK clients around actual local append/lookup/verification modules.
Full-prefix reads are bounded by existing validator limits and require measured
capacity/latency before any pilot. H-025 and historical evidence remain excluded.
