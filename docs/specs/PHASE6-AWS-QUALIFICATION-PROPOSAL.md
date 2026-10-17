# AWS qualification proposal — not deployed

Status 2026-10-17: DRAFT. This is a synthetic receipt-service qualification proposal,
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
