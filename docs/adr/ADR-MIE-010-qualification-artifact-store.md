# ADR-MIE-010: Separate qualification archives

Status: PROPOSED, 2026-10-17. No resources created and no runtime/store approval.

## Scope and ownership

The proposed synthetic qualification uses two distinct Linux hosts and service
identities in Anteneh Hailu's AWS account 989879505232, eu-north-1. Anteneh is the
storage owner. Source and receipt identities are separate; both remain under the
same account administrator. This is not independent third-party custody.

The proposed receipt authority consists of the existing signed receipt protocol,
S3 bucket `mie-p6-qualification-001-989879505232-receipts`, DynamoDB state table
`mie-p6-qualification-001-state` and index table
`mie-p6-qualification-001-index`. Bucket versioning and default 30-day COMPLIANCE
Object Lock are requested. Explicit retention requests must still be checked by
the application; the proposed IAM role does not enforce that minimum by itself.

A separate proposed artifact bucket,
`mie-p6-qualification-001-989879505232-artifacts`, contains `code/` deployment
objects and `source/` / `receipt/` archives. It is a private, encrypted, versioned
archive, not an immutable independent receipt authority. The source role writes
only source archives; the receipt role writes only receipt archives. Both read
code objects. Code object version IDs and hashes remain to be frozen.

Each host has its own preserved encrypted root disk. Proposed local base path is
`/var/lib/mie/qualification-001`; exact child paths and headers must be frozen in
the runtime package before any evidence-root creation or execution. Source and
receipt host paths refer to different machines and runtime identities.

## Authority, equivalence and failure handling

Existing ObjectWriter stores and validators retain their formats and authority.
Archives are verified copies, not replacement stores or evidence of successful
execution. No migration, equivalence to prior pilots, H-025 artifact, restart,
repair, retroactive start or scientific acceptance is authorized by this proposal.

Archive publication must preserve source bytes, metadata and hashes, then verify
the exact uploaded version. Unknown or partial uploads remain unknown; they may
not be relabelled complete. Failed runtime stores and retained service objects
remain preserved. Export and independent inspection precede any separately
approved cleanup. The template retains buckets/tables and root disks; stopping
compute does not stop all charges and deleting the stack does not erase them.

## Consequences and outstanding decisions

Role isolation allows the source to verify service receipts without its signing
key or storage-write rights. The account administrator can still affect both
sides. Independent retention must be checked against actual deployed object
versions, retention attributes, identities and policy, not inferred from local
mocks or the template. The cloud template is infrastructure only and is not a
runnable or approved qualification package. See
`../specs/PHASE6-AWS-QUALIFICATION-PROPOSAL.md`.
