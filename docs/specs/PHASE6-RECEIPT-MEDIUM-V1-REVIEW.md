# Phase 6 — receipt-medium-v1 local candidate

Status: implemented and locally tested; NOT deployed. This is a separate deployment
variant based on d542f51, following two capacity-rejected receipt starts (002/003).
Neither capacity-rejected attempt sent SSM or installed software. No evidence is
reclassified. Phase 6 remains incomplete. Owner: Anteneh Hailu.

## Scope and preservation

Account 989879505232, eu-north-1, existing `mie-p6-qualification-001` stack only.
Receipt i-0c5a2a75ca70db228 may change from t4g.small to t4g.medium; source
i-08e89ad2e3feedb8a remains t4g.small and stopped. Same AMI, profiles, instance IDs,
subnet/zone, encrypted 8 GB gp3 volumes and standard CPU credits. No replacement,
new disk/server, identity values, listener, trading, market connection or pilot.

The original deployment files remain byte-identical. A versioned directory preserves
the new installer/timer/builder/renderer and template as a self-contained candidate.
These deliberately frozen copies retain reproducibility of the original artifacts;
changes are reviewed against their originals, not treated as a generic refactor.
The runtime archive, crates, readiness script, source installation and all previous
kits and attempt folders remain unchanged. H-025 is excluded.

Candidate installer validates exact per-role ID/type/profile. Its actual preparation
entry point and CLI accept receipt only. The source-small identity check is preserved
for checking boundaries; this is NOT authority to prepare source again.

## Local artifacts and controls

`deployment/phase6/receipt-medium-v1/` contains:

- `prepare-qualification-host.py`: exact receipt-medium admission before installation;
  source preparation is refused; `p6.host_preparation.receipt_medium.v1` result schema.
- `receipt-recovery-timer.py`: only the description and required receipt size differ
  from the original. Root, exact IMDS identity, one creation, numeric D-Bus/action
  readback, minimum remaining time and no-rearm rules are unchanged.
- `build-host-kit.py`: deterministic archive and versioned plan/manifest; source-small
  and receipt-medium mapping, exact IDs and receipt-only preparation are explicit.
- `host-preparation-command.py`: receipt only, exact kit hash/S3 version/timer hash,
  fresh `host-preparation-delivery-medium-v1`, two deadline checks and bounded command.
- `qualification-v1.json`: entire template equals the original except
  `Resources.ReceiptInstance.Properties.InstanceType = t4g.medium`.
- `host-control.py`: offline hardware/ownership/volume checks at before-resize,
  after-resize and delivery stages; strict template and change-set checks. It never
  invokes AWS. Its CLI validates saved observations, not their age or provenance.

Kit SHA256 `c3a024b3e09483f3c2341ec87cb10e6843dbd39dc174fa7b2aac660666bf216e`,
21,585,861 bytes. Existing runtime SHA256
`370343f69e8ac0f4264b239c3ed43339cebb4cf4c9d45d2720b8833b1c060034`.
Timer SHA256 `6a9a190de2a230a7f2b93a7d8646c783c2298933473dc42daa3810ad4b4f6ca4`.
Kit is local only. Readiness/native binaries still need actual host qualification.

Local candidate output is in the existing administrative preparation folder:
`/Users/antenehhailu/.codex/visualizations/2026/10/01/01a0f6c9-d66b-7d51-85fa-da98aabc6ae7/mie-planning/phase6-receipt-recovery-20261021/receipt-host-kit-medium-v1.tar.gz`.
This is code preparation, not a new scientific/runtime evidence store.

## Required bindings before any later AWS execution

Changing a compatible EBS instance type requires a stopped instance. CloudFormation
marks this property as involving interruption. Do not assume that executing the
resize leaves receipt stopped; it may transition to running as part of the update.
Observer and an independently verified fresh external stop must already be armed.
Sources: [EC2 resize](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/change-instance-type-of-ebs-backed-instance.html),
[CloudFormation instance](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ec2-instance.html).

The prospective attempt must bind fresh evidence to:

1. Exact stack/account/operator, all 18 physical resources, stopped pair, template,
   source-small/receipt-small ownership, volumes, credits and security group.
2. One conditional kit upload and exact returned S3 version byte/hash readback.
3. A fresh absolute 45-minute UTC/continuous-clock window and watch-only operator
   (NO `--template`/`--runtime-bundle`), with ARMED and current observation verified.
4. One schedule-only update using the current template and previous LaunchCompute;
   verify actual enabled UTC stop, exact pair/role, no flexible window and settled stack.
5. One UPDATE change set for the new template, preserving both actual parameter
   values (LaunchCompute=yes, the same new StopAtUtc). Bind the returned exact ARN.
   Read DescribeChangeSet with IncludePropertyValues=true and GetTemplate using
   that same ARN. Preserve the exact request/response association and hashes.
6. `validate_change_set` requires the exact ARN and template/request binding, exact
   two parameter values, one receipt modification, Replacement=False, static
   InstanceType-only change, RequiresRecreation=Never and explicit
   t4g.small → t4g.medium before/after values. Missing/conditional/extra changes
   refuse execution. A local synthetic preview passing is not an actual AWS preview.
7. Execute only that frozen ARN once, under the already armed stop protections.
   If update fails, rolls back, changes identity, becomes unknown, or protection
   fails, stop preparation. No automatic repair, repeated execution or rollback
   override. Observe any provider-managed rollback and stop state honestly.
8. After settled success, verify exact medium receipt/small source and unchanged
   other resources. If receipt is already running, do NOT issue StartInstances.
   If still stopped, at most one separately recorded start is allowed within the
   same window. A capacity/unknown error terminates the attempt.
9. Fresh read-only guest admission must confirm installation leaf/runtime user and
   new delivery path absent. Arm one guest timer with >=35 minutes remaining;
   verify exact action/root/deadline. Delivery requires >=2,000 seconds remaining
   before download and after extraction; installer timeout 1,900 seconds.
10. Collect exact diagnostics/native hashes, stop early on completion/failure, and
    verify both actually stopped with a settled stack. Dedicated outcome review.

The offline guard checks a supplied response; it is not a network witness or an
execution gateway. A future operator must use these functions on freshly captured
responses and send only the same verified ARN/request. The existing supervisory
operator is unchanged. No live-call-path or cloud-readiness claim is made here.

## Evidence/cost proposal for a later approved attempt

Proposed new operation root (NOT created):
`/Users/antenehhailu/.codex/visualizations/2026/10/01/01a0f6c9-d66b-7d51-85fa-da98aabc6ae7/mie-planning/phase6-host-preparation-run-004/`,
with empty operator/ child. Earlier operation roots remain closed.

Keep the existing combined USD 3 first-30-day planning allowance from 18 October,
not a hard cap. AWS Pricing readback on 21 October at 13:05:56 UTC: Stockholm Linux
shared On-Demand t4g.medium is USD 0.0344/hour, or USD 0.0258 for 45 minutes of
compute only; excludes IPv4/storage/requests/tax and is not an account bill. Retained resources can keep
costing. No account upgrade, reservation or guaranteed availability is proposed.
The stopped receipt may remain medium afterward; no automatic downgrade/cleanup.

A successful later attempt establishes host preparation for review only. Runtime
authentication, independent retention, integrated fault/capacity tests and a
separately approved live-data/simulated-money pilot remain outstanding.


## Verification completed on 21 October 2026

- Initial boundary tests exposed the old small-only behavior: 6 failed, 2 passed.
  The review regression then exposed the missing change-set target binding:
  1 failed, 66 deselected. Both failures were corrected in this candidate.
- Final relevant suite: 245 executed and passed, 0 failed or skipped, in 2.85 s.
  Includes candidate, redirected original fault tests, original recovery and host
  preparation tests, and qualification operator tests.
- Dedicated independent reviewer separately ran 138 candidate/regression tests:
  all passed in 1.43 s. This overlaps the 245; the counts are not additive.
  Review approved the local candidate after the change-set binding correction,
  with no remaining local blocker. It is not approval to deploy.
- Two builds are byte-identical: 8 regular archive members, all 7 manifest payload
  hashes verified. Candidate Python sources parse with Python 3.9 grammar. The
  original six deployment files remain identical to base d542f51.
- Read-only AWS observations at 13:23:26 UTC: ValidateTemplate accepted the
  candidate; AMI metadata reports arm64, EBS/HVM and ENA; stack UPDATE_COMPLETE;
  both exact instances remain stopped and t4g.small. No capacity is proven.
  No upload, change set, resize, start or SSM command was performed this turn.

Reviewed guard SHA256:
`2ac21909c36e4b0a2a417f5272ec84f8135b93f21e45135af65f033b57950c90`.
Candidate template SHA256:
`6e23770f5b50aefd4f5491a09d8803c7cc2baea59527e85b8528f634407103a9`.

Logs and artifact verification are saved in the administrative preparation folder
above as `medium-v1-{initial-red,review-red,final-245-tests}.txt`,
`medium-v1-artifact-verification.json`, and
`medium-readonly-20261021T132324796676Z-*`.
