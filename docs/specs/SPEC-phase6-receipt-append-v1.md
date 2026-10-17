# Service-side receipt append assembly v1

Status: local assembly with simulated external clients; no AWS deployment, HTTP
handler, pilot, real signing secret or market-data connection. ADR-MIE-005/006
remain the storage authorities. No historical migration or H-025 changes.

ReceiptAppendAttempt owns actual calls to the existing state and retention
adapters. Request preflight detaches input; service identity must match configured
identity. The injected service clock supplies preparation time within the supplied
key validity interval. The service Ed25519 key signs the canonical receipt; its
public key configures the S3 verifier. A fresh 32-byte random attempt ID and exact
signed receipt hash bind the DynamoDB pending claim. No S3 operation occurs before
a verified claim response. An expired prior prefix stops while leaving PENDING.
Only the actual S3 adapter return feeds commit; HTTP caller dictionaries cannot
supply receipt timestamps, signatures or retention witnesses through append().
The clock is checked again before and after commit; backwards movement, validity
expiry or expired prefix retention prevents acknowledgement. No signed receipt
is returned until commit returns successfully and completion checks pass. Any uncertainty
consumes this process-bound attempt; no retry or substitute receipt is returned.

Trust boundary: injected SDK clients, signing key, clock and configuration are
service-owned. This is not a sandbox against code with access to Python internals.
No real key is generated or written by this module. Explicit deployment key/trust
commitment and secure clock policy remain required. retained_utc_ms is sampled
before storage as receipt preparation time, not independently observed S3 time.
The timestamp semantics and cross-receipt clock monotonicity need full-service
review before prospective use. Runtime acceptance is always false.

Still missing: authenticated API, full-prefix signature/subject/time checks,
read-only exact lookup, historical idempotency return, challenged current head,
trusted live clocks, SDK deployment configuration and real cloud fault tests.
The diagnostic test fakes establish Python call order and refusal behavior only;
they cannot establish AWS durability or concurrency. Recreating an attempt after
a failure is not permission to retry a pilot. No Phase6 completion claim.


2026-10-17 integration update: SPEC-phase6-index-integration-v1 supersedes the
above earlier missing-index-wiring snapshot. The index is now mandatory in append
before commit, and indexed_challenged_head resolves stored versions. These changes
remain locally tested with simulated SDK clients, not deployed AWS authority.
