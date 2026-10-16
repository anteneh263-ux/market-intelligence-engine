# ADR-MIE-005 — receipt retention adapter boundary

Status: local implementation decision, subject to dedicated review. No deployment
or new evidence root is authorized. Parent ADR-MIE-004. No historical migration.

The external receipt service owns its separately approved private S3 bucket and
signing key. S3ReceiptRetention is its version-retention adapter, not the owner of
run sequencing or committed heads. The Mac must never call it with bucket rights.
Physical account/bucket/version remain explicit inputs; no bucket or client is
created by import or construction. Production integration is not implemented.

Each instance is bound to its creator PID and permits one conditional PUT attempt.
An inherited adapter refuses before acquiring a lock or touching storage. The coordinator must persist a
run's pending operation before invoking it and must block the run on uncertainty;
constructing a new adapter is NOT recovery authorization. SDK total_max_attempts=1
is required. A conditional conflict is not retried or treated as success. Exact
lookup/reconciliation and durable pending/head transactions remain unimplemented.

The adapter validates the receipt structure, reservation identity, pinned public
key signature and service identity. It writes canonical signed receipt bytes with
checksum, expected bucket owner, IfNoneMatch=* and explicit COMPLIANCE retention.
It then reads the returned exact version and its retention; no acknowledgement
is returned for missing version, altered bytes, weaker mode or insufficient date.
Returned retention is a conservative requested horizon, not a full-prefix claim.
The supplied receipt timestamp is not verified against a real clock; trust expiry,
authenticated caller and observed-time policies remain coordinator responsibilities.

There is no equivalence claim with historical evidence or an operational service.
No old store is migrated or repaired. Simulated clients exercise failure paths,
not AWS durability. Live testing requires approved bucket/permissions/costs and
reviewed complete service sequencing. H-025 and earlier evidence are excluded.

References checked 2026-10-16:
https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html
https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObjectRetention.html
