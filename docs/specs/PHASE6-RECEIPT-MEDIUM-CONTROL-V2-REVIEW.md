# Receipt-medium control v2 — local candidate

21 October 2026. Owner Anteneh Hailu. Scope: new local preview/policy/execution
controls for the existing qualification stack. No AWS calls from the candidate,
policy installation, resize, start or pilot have been performed in this work.
Phase 6 remains incomplete. Original v1, deployment kit and attempts 001–004 remain
unchanged. Base commit 951c748; separate branch codex/phase6-receipt-medium-control-v2.

## Problem and resulting behavior

Attempt004 supplied the correct receipt-only template delta but AWS emitted four
resource changes, with ReceiptInstance Replacement=Conditional. Its three reference
dependents were ExternalStop, SourceRole and StopRole. V1 refused that real preview,
as required by its locked contract. Passing local v1 tests never established the
shape of an actual AWS response. Attempt004 remains closed and unexecuted.

V2 classifies that exact four-resource shape without granting execution authority.
It compares complete before/after contexts and details against the hash-pinned,
unmodified actual004 response. Only the known ExternalStop deadline changes between
attempts. Resource/detail ordering and JSON serialization whitespace are normalized;
no arbitrary resource, field, wildcard, policy expansion or placeholder is allowed.
The three exact reference substitutions are fixed data, not general acceptance of
unknown future values. Any new AWS response shape is refused for separate review.

The reference is an account-specific operational contract. It is deliberately narrow,
not a generic CloudFormation validator. Its provenance identifies the preserved raw
response and the source of physical bindings/test readbacks. Fake execution tests
explicitly synthesize a new v2 ARN and installed policy; they are not evidence that
the retained004 plan or a future AWS run was executed or approved.

## New files and policy

New deployment/phase6/receipt-medium-control-v2/ contains control.py,
stack-policy.json, preview-reference.json, resource-bindings.json and provenance.json.
Tests are test_phase6_receipt_medium_control_v2.py and its readback fixture. No
existing deployment or scientific files are modified.

Policy SHA256 d70f1bdd30506a0663c9607a6a099f9751c123e28ab6ddf16cbeed861b5dfe23:

- Allow Update:Modify only for ExternalStop, ReceiptInstance, SourceRole, StopRole.
- Deny Update:Replace and Update:Delete for every resource.
- Explicitly deny Update:* for SourceInstance. All other modifications are implicitly
  denied by the policy's allowlist.

AWS documents that ExecuteChangeSet enforces the current stack policy and does not
accept a temporary override. Therefore the proposed protection is read back just
before execution. This is reliance on the documented provider contract, not an
executed enforcement test. A failure/rollback remains a stop condition, not grounds
to override the policy. [ExecuteChangeSet API](https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_ExecuteChangeSet.html).

A stack policy persists, cannot be deleted, and can be changed with separate authority.
It controls CloudFormation updates; it is not an IAM restriction on direct EC2 calls
or a DeleteStack guard. The current stack's termination protection is separate. The
strict template/preview checks are still required to reject added resources and
unexpected content. Later legitimate changes to other resources will require a
reviewed policy revision. [AWS stack policy documentation](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/protect-stack-resources.html).

policy_install_request is a renderer only. It accepts an absent policy or the exact
reviewed policy; an unknown existing policy cannot be silently replaced. No set-policy
call is implemented or executed by this renderer. The last004 readback had no policy.
Fresh GetStackPolicy is required before any proposed installation.

## Execution path and boundaries

execute_once uses an explicitly supplied API and an already-created, approved owner
0700 operation directory. It does not create evidence roots. In order it checks:

1. Fresh ARMED supervisor with the exact stack, same stop deadline, stopped pair,
   observation age at most 25 seconds, and 40–45 minutes left in the fixed window.
2. Exact account/operator, settled UPDATE_COMPLETE, preserved LaunchCompute=yes and
   StopAtUtc, all 18 physical identities, original template, stopped small pair,
   unchanged disks/profiles/metadata/network/CPU credits via pinned v1 checks.
3. Existing external stop target, role, deadline, enabled UTC/no flexible window,
   and unchanged SourceRole/StopRole policies.
4. Fresh DescribeChangeSet and matching GetTemplate, creation time inside this
   operation's 45-minute window, exact v2 ARN/name and all reviewed preview content.
5. Fresh GetStackPolicy must equal the exact pinned policy. Check supervisor again.
6. Persist and fsync an exclusive execution-intent file and directory BEFORE a single
   ExecuteChangeSet. Send the same validated ARN, no policy override, and explicitly
   DisableRollback=False. Return only that a response was received, never completion.

The old v1/004 ARN is refused by execution_request even though content classification
can explain its shape. A repeated call in the same operation directory is refused.
Unknown API outcomes preserve the intent and must never be retried automatically.

The API adapter must itself bound calls, disable SDK retries and persist raw requests
and responses. The supervisory callback must be backed by the authentic reviewed
operator, with its UTC/continuous-clock checks and evidence chain. These are injected
caller responsibilities and must be verified in the actual live wrapper. The intent
provides cooperative once-per-directory behavior, not account-wide exclusion against
another administrator. No atomic AWS policy/preview snapshot is claimed.

A future attempt must arm the watch-only observer and verified external stop BEFORE
any resize, since CloudFormation may start the receipt host during update. Source
remains stopped. After settled success, freshly verify medium receipt and unchanged
identities; explicitly start it at most once only if it stayed stopped. Guest admission,
one timer, same monotonic deadline, >=35 minutes for arming / >=2000 seconds for
delivery, bounded preparation, log collection and confirmed stop still apply.

AWS deletes other change sets for the stack when one is executed. The archived004
record remains local; do not manually execute or repair its retained AWS change set.
A future approval must disclose this provider side effect. [ExecuteChangeSet API](https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_ExecuteChangeSet.html).

## Preservation and candidate reuse

The existing medium template and kit remain exact:

- Template SHA256 6e23770f5b50aefd4f5491a09d8803c7cc2baea59527e85b8528f634407103a9.
- Kit SHA256 c3a024b3e09483f3c2341ec87cb10e6843dbd39dc174fa7b2aac660666bf216e,
  21,585,861 bytes, existing S3 version E.Z2EIEwdq7Q7JkjviL1SQSfCBmUQA6E.
- V1 guard SHA256 2ac21909c36e4b0a2a417f5272ec84f8135b93f21e45135af65f033b57950c90.
- Actual preview reference SHA256 a5f47d625f09d2def971e484cb5d8eb8ebb16f4f912d0d968c6d7a23c4b0b6df.

No kit rebuild/upload is needed for these operator-only changes. Reuse still requires
fresh exact-version GET/hash validation before any later delivery. The deployed
runtime code, native crate sources and readiness script are unchanged.

## Verification status

The first actual-preview regression failed against v1 as expected. The expanded
new suite passes 115 tests, including actual reference classification, modified
identity/template/IAM/schedule/policy refusal, all 18-resource policy action cases,
recording fake-API execution, late guard failure, unknown outcome, durable-intent
failure and repeated-call refusal. One JSON-whitespace case caught a local bug and
was fixed before review.

Independent reviewer /root/phase5_contract_review separately ran the 115 tests and
approved the LOCAL candidate with no blocking finding. Reviewed control SHA256
225c2a7529e08425fdaeceaadda1d9a59298b81ba934e93ecc2f9d13b83ef90b. Counts overlap and
are not additive. AWS policy enforcement, capacity, rollback consequences and host
preparation remain untested. This is not cloud-readiness or deployment approval.


Final combined relevant verification: **360 executed and passed**, 0 failed or
skipped, in 2.93 seconds (115 new plus 245 unchanged relevant tests). Independent
review approved this technical report's local-only claims and disclosed limitations.
No AWS API mutation, policy installation or running cloud workload occurred this turn.

A later proposed attempt005 would install this exact persistent policy only if the
fresh existing policy is absent (or verify an already identical policy), read it
back, then reuse the existing medium kit in one fresh 45-minute window. It needs a
new approved result root, authentic watch-only supervision, fresh recorded bounded
API readbacks and use of execute_once at the actual execution call site. The earlier
004 wrapper must not be used unmodified: its v1 validator cannot accept the real
preview. No production claim follows until that live path and outcome are reviewed.
