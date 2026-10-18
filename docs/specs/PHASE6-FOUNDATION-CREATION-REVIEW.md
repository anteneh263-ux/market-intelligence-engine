# Foundation creation review — approved and deployed

Prepared 2026-10-18 for Anteneh Hailu. This is the concrete first deployment step,
not permission to start compute or a market-data pilot. The pre-creation read-only AWS check
on this date returned ValidationError: the exact proposed stack did not exist.

## Exact request

- Account 989879505232, region eu-north-1 (Stockholm), CLI profile mie-phase6.
- Stack `mie-p6-qualification-001`.
- Existing file `deployment/phase6/qualification-v1.json`, SHA256
  `ef9f30a8af6bfb817f2ddcebeedbbe5abf88f6b89a17a2a969fb9e36c4591754`.
- CloudFormation CreateStack, LaunchCompute=no and StopAtUtc empty,
  CAPABILITY_IAM, termination protection enabled, OnFailure=DO_NOTHING,
  TimeoutInMinutes=10, ClientRequestToken `mie-p6-foundation-001-ef9f30a8af6bfb81`.
- Purpose tag `phase6-qualification-only`.
- Existing VPC vpc-08598a29ae0886e9d and subnet subnet-0063a05d947cface6
  remain referenced by the template. No existing network setting is changed.

The exact template has already passed AWS ValidateTemplate. That earlier consent
covered validation only. Creation explicitly acknowledges the template's new IAM
roles/profiles/policies and the persistent storage settings below. An unknown
CreateStack result must be inspected by stack ID/name/events and request token;
never blindly retry, delete, or create another stack name.

## Fourteen foundation resources

Two private encrypted versioned S3 buckets with HTTPS-only policies, two on-demand
DynamoDB tables, two empty Secrets Manager identity containers, a fixed-port SSM
document, a security group with no ingress rules, two instance roles and their
two profiles. Roles include SSM access and the reviewed bucket/prefix/table/secret
permissions; no role is attached to an existing instance. Containers are created
without secret values. Public network ingress is not enabled.

The receipt bucket is `mie-p6-qualification-001-989879505232-receipts`; default
Object Lock is 30-day COMPLIANCE. When later authorized writes occur, locked
versions cannot be deleted before retention expires. Artifact bucket is
`mie-p6-qualification-001-989879505232-artifacts`. Tables are
`mie-p6-qualification-001-state` and `mie-p6-qualification-001-index`.
Secret names are `mie-p6-qualification-001/source-identity` and
`mie-p6-qualification-001/receipt-identity`. Ownership and authority are defined
by ADR-MIE-010; approval of this step accepts only its foundation storage scope.

LaunchCompute=no excludes both EC2 instances, their disks/public IPs, the stop
role and stop scheduler. This step does not upload code, secret values, credentials,
market data or local research evidence. It does not upgrade the AWS account plan,
start a pilot, authorize trading, migrate anything or touch H-025.

## Price and persistence

Allow up to USD1 of planned foundation usage over the first30 days, before tax
and currency conversion, without assuming credits. Two secrets at USD0.40 each
per month are the principal recurring allowance (USD0.80/month); empty S3/DynamoDB
stores have no planned workload. Small control/read-request charges remain usage
based. IAM and native AWS-resource CloudFormation management add no service fee.

The user approved this USD1 planning allowance on2026-10-18. It is not an
enforced spending cap.
It does not include future compute, data uploads or runtime tests. These resources
persist and can keep accruing charges after30 days until separately removed.
Failure preserves partially created resources for inspection; no automatic cleanup
or repair is authorized. Do not treat this document as an automated cost monitor.

Prices checked against AWS on2026-10-18 and the eu-north-1 pricing snapshots saved
on2026-10-17: [Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/),
[CloudFormation](https://aws.amazon.com/cloudformation/pricing/),
[IAM](https://aws.amazon.com/iam/faqs/).

## Verification after approval

Read exact caller identity and recheck absence/name collisions. Submit only the
hashed template/request above; preserve the actual API response and request token.
Observe stack events to completion, without recreating failed resources. Read back
bucket encryption/versioning/lock/public-access settings, table identity/status,
secret metadata without retrieving values, role policies/trust, SSM document and
security group. Check no compute resources exist. Save actual outputs and hashes
in the existing owner's planning area under a new foundation deployment record.
A failed or incomplete readback leaves foundation status INCOMPLETE.

The next separately reviewable step is host/native bootstrap, identities and
exact runtime configuration, time interval, roots and compute costs; then actual
receipt qualification. Market-data simulated-money pilot approval follows that.
Phase6 remains INCOMPLETE throughout this foundation-only step.


## 2026-10-18 — approved request executed and verified

The user answered “Ja, opprett dette grunnoppsettet” to the exact foundation-only
approval. The stored approval/request and original reviewed document hash are
preserved in the deployment evidence. One creation request completed; all14
resources and73 setting checks passed, with empty bucket/secret version inventories
and two additional consistent empty-table checks. See
[the actual deployment record](PHASE6-FOUNDATION-DEPLOYMENT-20261018.md) for identity,
service audit, preserved verification corrections and boundaries. No compute,
secret values, code/data upload, pilot, account upgrade or cleanup was performed.
The future compute/runtime/pilot scopes above remain open.
