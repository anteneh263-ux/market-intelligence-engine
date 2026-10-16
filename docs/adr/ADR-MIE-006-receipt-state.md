# ADR-MIE-006 — separate receipt pending/head state

Status: local implementation, subject to dedicated review. No AWS table or
resources created, no pilot authorization. Parent ADR-MIE-004/005.

The selected architecture adds one DynamoDB table in the same AWS account and
region. This is an additional AWS service and must be included in the deployment
cost estimate and IAM review. The service owns the table; the Mac gets no table
access. Table ARN is explicit. Partition key is run_id (String); no TTL, no global
table or automatic pending expiry. S3 remains immutable receipt-version storage;
DynamoDB is mutable sequencing metadata, not scientific evidence or independent
retention. No historical migration/equivalence or H-025 involvement.

ReceiptStateAttempt performs one claim and one commit, bound to its creator PID.
SDK retries disabled. Initial claim requires attribute_not_exists(run_id).
Successor claim atomically requires COMMITTED, exact previous sequence/hash and
service identity. It records exact canonical request, unique caller-generated
attempt ID and expected signed receipt hash while preserving the preceding head
and prefix retention horizon. ALL_NEW response is checked before storage may run.
No timeout/unknown/conflict allows storage or retry from that attempt object.
A run left PENDING cannot be taken over, expired, reset or continued by this API.

Only after the actual S3 adapter returns its verified version may the trusted
service call commit. The supplied result is bound to bucket/key/hash/version and
retention. Commit condition binds the full pending request, attempt and expected
receipt hash. Prefix horizon is the minimum of prior and new retention. A lost
commit response remains uncertain even if the database committed. No automatic
retry, deletion, repair or recovery mutation is offered. Read-only reconciliation
and historical idempotent lookup remain required future components.

This adapter trusts the service caller to supply the actual S3 result: accepting
a dictionary cannot prove it came from S3. Signed receipt creation, trusted clocks,
unique attempt generation, authenticated API, full history duplicate-subject
checks, trust expiry, challenged head and coordinator invocation of both adapters
remain unimplemented. The latest table row alone cannot prove full history.
A recreated service attempt is not permission to repeat a failed pilot.

Tests use an explicit conditional-state fake, not AWS or an SDK emulator.
Production implementation to actual call site to externally inspected persisted
result is still incomplete. Cloud concurrency/fault tests require approved setup.

Sources checked 2026-10-16:
https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_UpdateItem.html
https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_GetItem.html
