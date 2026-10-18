# AWS qualification — foundation deployed, runtime pending

Original proposal status2026-10-17: DRAFT. This is a synthetic receipt-service qualification proposal,
not the prospective market pilot or approval to create resources. Phase6 is open.
Owner: Anteneh Hailu. Account: 989879505232. Region: eu-north-1.

## Proposed resources

`deployment/phase6/qualification-v1.json` describes separate source and receipt
roles, two private encrypted versioned buckets, two DynamoDB on-demand tables,
two Secrets Manager identity containers, a fixed-port SSM document and a security
group without ingress rules. Storage authority and proposed paths are recorded
in ADR-MIE-010. Secret containers have no provisioned values in this template.

Default `LaunchCompute=no` creates only the foundation; it still creates durable,
potentially chargeable resources. Compute is conditional: two t4g.small ARM64
instances with standard CPU credits, encrypted 8GB gp3 root disks retained on
termination, IMDSv2 and public IPv4 for outbound access. The pinned AL2023 AMI is
`ami-04e0c7320ad3a3a48`. It does not install a complete runnable MIE operator.

For compute, `StopAtUtc` must be set. CloudFormation rules reject the empty default
when compute is requested. The template cannot validate whether a timestamp is
future or within an approved interval. A launch guard and independently active
operator watchdog must check that before the first instance starts, including
partial stack failures. Guest shutdown and an external scheduler are additional
controls; scheduler dependencies can prevent its creation on partial failure.
Sixty minutes is a target, not a guaranteed hard runtime/cost cap.

## Preliminary cost inputs

Recorded AWS price-list responses dated 2026-10-17 provide these list-price inputs,
without assuming credits/free-tier entitlement, excluding tax and currency conversion:

| Item | Input / illustrative allowance |
| --- | --- |
| Two t4g.small for one hour | 2 × USD 0.0172 = USD 0.0344 |
| Two public IPv4 for one hour | 2 × USD 0.005 = USD 0.01 |
| Retained 16GB gp3 for a full month | 16 × USD 0.0836 = USD 1.3376 |
| Two secrets for a full month | 2 × USD 0.40 = USD 0.80 |
| 1GB S3 Standard for a month | USD 0.023 |
| 10,000 S3 Tier1 requests | USD 0.05 |
| 100,000 S3 Tier2 requests | USD 0.04 |
| 100,000 DynamoDB write units | USD 0.067 |
| 100,000 DynamoDB read units | USD 0.01345 |
| 1,000 Secrets Manager calls | USD 0.005 |

These allowances are planning inputs, not measured consumption or a complete bill.
DynamoDB storage, data transfer, scheduler calls and any logging must be included
in the final cost freeze. S3 pricing pages were paginated; only identified exact
products were used. Retained disks, secrets and stored data continue charging
beyond a stopped compute run. No budget is approved by this document. A proposed
USD 5 allowance needs final pricing, enforceable operating limits and owner approval;
it is not an AWS hard spending cap. Do not upgrade the account plan automatically.

## Validation and remaining work

Local strict JSON validation and independent structural review covered the initial
18-resource candidate. Offline cfn-lint 1.57.2 checked the current conditional
candidate: no errors, one W1030 warning because `StopAtUtc` has an empty default.
The compute-only resource and compute rule prevent that default from being used
for a requested compute launch; this is not a suppressed or clean-lint result.

Automatic approval review rejected `cloudformation validate-template` because it
would transmit account/network/IAM/storage configuration without explicit approval
for that payload. The template was not sent. Offline validation does not establish
AWS deployability, permissions, service availability or account entitlement.

Before any launch: finalize the CLI/bootstrap/dependency bundle and immutable code
versions/hashes; provision separate CA/client/server/signing identities without
logging secrets; freeze trust/run IDs/roots, actual native readiness checks and
resource limits; implement the fresh-deadline launch guard and operator watchdog;
review the complete package and obtain approval for concrete costs and creation.
Then qualify actual native-clock + SSM + mTLS + S3/DynamoDB roundtrips, unknown
writes, faults, latency and capacity. Preserve all outcomes and verify retention.

Only after qualification should a separate future market-data pilot contract fix
its interval, exact configuration, evidence paths and cost limits. That pilot uses
simulated money. It has not started. No H-025 artifacts are involved.


## 2026-10-18 — AWS template validation completed

After the user resumed the explicitly proposed validation and renewed the
expired CLI session, AWS CloudFormation ValidateTemplate returned exit0 in
account989879505232, eu-north-1. The exact template SHA256 was
`ef9f30a8af6bfb817f2ddcebeedbbe5abf88f6b89a17a2a969fb9e36c4591754`.
AWS reported both parameters and the required `CAPABILITY_IAM` capability.
The earlier transmission rejection is resolved for this validation action only.
The original dated rejection above remains as history.

No stack was created or updated, no resources were launched, and no IAM capability
was acknowledged for creation. ValidateTemplate does not prove deployment success,
runtime safety, native-clock/receipt operation or prospective acceptance. The
exact response and attributed validation record are retained under
`/Users/antenehhailu/.codex/visualizations/2026/10/01/01a0f6c9-d66b-7d51-85fa-da98aabc6ae7/mie-planning/phase6-aws-template-validation-20261018`.

Next execution boundary remains the complete server/launch package: actual runtime
bundle and dependency pins; provisioned separate identities/trust; approved physical
roots and run identity; guard rejecting stale or excessive stop intervals; independent
operator watchdog active before the first launch and covering partial stack failure;
final cost freeze and explicit creation approval. Guest shutdown and the conditional
scheduler do not, by themselves, close the operator-watchdog gap. These controls
are still outstanding, not activated by successful template validation.

## 2026-10-18 — runtime candidate and staged creation boundary

The synthetic receipt CLI and operator watch/stop candidate are now implemented
and independently tested (56 focused tests). A reproducible Python/ARM64 wheel
archive exists; see SPEC-phase6-qualification-runtime-v1. Actual Linux bootstrap,
native binary freeze, identities/roots/trust, SSM startup, archive verification and
cloud qualification remain open. The operator is external to the EC2 hosts but
runs on the operator machine; it is not an independently hosted guaranteed stop.

To obtain concrete storage/role identities before any compute launch, the first
reviewable deployment step is foundation-only. PHASE6-FOUNDATION-CREATION-REVIEW
fixes the unchanged template hash,14 resources, exact create parameters and USD1
first30-day planning allowance. Approval remains pending. No code/data/secrets
are uploaded and no EC2 instances start in that step. The prior USD5 illustrative
combined allowance is not an approved budget. Creation remains separate from the
already-authorized successful template validation. Read-only AWS confirmed the
proposed stack absent on2026-10-18.


## 2026-10-18 — approved AWS foundation created and verified

The user approved the exact14-resource foundation-only scope and USD1 first30-day
planning allowance. One CreateStack request completed in account989879505232,
eu-north-1, with LaunchCompute=no.73 actual setting checks and two consistent
empty-table checks pass; bucket and secret version inventories are empty. See
PHASE6-FOUNDATION-DEPLOYMENT-20261018 for exact stack/resource identities, raw
readbacks, CloudTrail failure-policy confirmation and preserved verifier corrections.
The original template is unchanged. No compute, code/secret-value upload, account
upgrade, pilot or cleanup occurred. This closes foundation creation/settings only.

Point1 still needs actual receipt-clock/auth/retention qualification; points2–3
still need target-host/source integration and fault/load tests; point4 now has an
approved deployed foundation but needs exact runtime/bootstrap/identities/roots/
interval/compute freeze; point5 remains an unstarted prospective simulated-money
pilot. Phase6 remains INCOMPLETE. No production code changed; prior3837 local
regression passes remain the code baseline, not cloud runtime acceptance.
