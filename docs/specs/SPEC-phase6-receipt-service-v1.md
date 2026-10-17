# Bound receipt service v1

build_receipt_service binds the strict Function URL dispatcher to actual local
append, original-receipt and indexed-head implementations. No injectable HTTP
callbacks or SDK/config/key fields come from request bodies. Configuration and
pinned key/trust are service-owned. Append uses a new attempt object per invocation
but persistent pending/head conditions still prevent duplicate or uncertain writes.
Repeated append gets refusal; explicit /lookup can return the verified original
without writing. /head resolves the persisted index automatically.

Local integration tests use trusted synthetic IAM events and simulated SDK clients.
This does not demonstrate deployed AWS authentication, clock accuracy or durability.
Still required: immutable deploy artifact/dependencies, secret loading and key setup,
AWS_IAM URL and invocation-only caller policy, least-privilege runtime role, accurate
clock policy, service identity/endpoint pinning and actual cloud fault tests.
No AWS resources, credentials or pilot are created by this implementation.

Before every successor append, the bound service verifies an indexed challenged
head with a fresh service-owned nonce, checks exact predecessor sequence/hash,
checks all immutable index entries for duplicate subjects, and floors all append
clock samples at the verified head observation. The persistent predecessor CAS
rejects concurrent advancement. These controls belong to the bound service; the
lower-level ReceiptAppendAttempt alone is not a complete admission path.

2026-10-17 validation: 27 focused repeat/HTTP/service tests passed, independently
repeated by the dedicated reviewer. Service source SHA256:
7a3975dfab0302e222d3dcc2089c1bd1930a0ad320a4151ab3259d4fdbe23eac.
The full suite before these last three successor tests passed 3610 tests.
Final full regression with the reviewed successor checks: 3613 passed in194.54s,
using the pinned cryptography runtime. No cloud tests or prospective runs included.
