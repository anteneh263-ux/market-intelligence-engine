# ADR-MIE-007 — immutable receipt version index

Local adapter decision; no resources or evidence roots created. Parent ADR005/006.
A separately approved DynamoDB table has entry_id(String) partition key run:seq.
Service owns writes; Mac has no table access. Include the table in pricing and IAM
review. No TTL, delete, migration or overwrite APIs. No historical/H-025 changes.

Record contains exact canonical append request and retained version reference,
committed by a canonical SHA256. Conditional PutItem attribute_not_exists prevents
replacement. Each process-bound writer instance permits one publication attempt;
SDK retries disabled. Unknown outcome blocks head commit and repeated publication.
Strongly consistent exact-request lookup returns NOT_FOUND or FOUND_UNCONFIRMED.
Same sequence with different request content refuses. Canonical encoding and hash
are checked; hash does not authenticate an administrator-controlled table.

The future append coordinator must publish after actual S3 verification and before
head commit. Index failure leaves the run pending; successful publication followed
by failed head commit can leave an orphan index entry. This is intentional partial
state, not an append acknowledgement. Lookup alone never proves committed state,
signature, current retention or permission to resume a failed pilot. Those controls
must gate any future idempotent response. Conditional Put conflict does not return
an original receipt automatically. This package does not yet wire the index into
append or challenged-head callers, and does not implement pagination/full history.

No equivalence with historical evidence. Simulated SDK tests establish conditional
requests and refusal paths, not live AWS durability or access policy enforcement.
Table ARN, bucket and service identity are explicit trusted configuration.
