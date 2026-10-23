# Receipt stack recovery001 — local candidate

Version `receipt-stack-recovery.001.v1`. Owner: Anteneh Hailu. Operational recovery
records only; no scientific evidence, pilot, market data or H-025 activity.
Historical packages and closed001–007 roots remain unchanged.

## Exact operation and prerequisites

Account `989879505232`, `eu-north-1`, existing stack
`arn:aws:cloudformation:eu-north-1:989879505232:stack/mie-p6-qualification-001/c0769b00-caf6-11f1-8e71-0affdc797e93`.
Both existing hosts must be stopped `t4g.small` with their original identities,
encrypted retained disks, standard CPU credits, profiles and no-ingress group.
The current original template, 18 physical bindings, stack authority, two host
inline IAM policies, security group, stack policy and schedule must match closed007.
The failed stack and exact archived rollback events must still match.

One request only: `ContinueUpdateRollback`, exact stack ARN, unique durable
ClientRequestToken, `ResourcesToSkip=["ReceiptInstance"]`. No RoleARN,
ForceRollback, broad skip, start, resize, SSM, install, policy change, deletion,
new resources, account upgrade or automatic retry. Unknown response means observe,
never resubmit. The skip is eligible because ReceiptInstance failed *during*
rollback, not merely during the earlier free-plan rejection.

The existing stop expression `at(2026-10-22T20:44:06)` is expired and stays unchanged.
It is NOT a safety backstop. A single local supervising process takes fresh EC2 and
stack observations before submission and during recovery. It runs under caffeinate,
rejects clock disagreement above 2 seconds, observation gaps above 90 seconds,
and ends forward work within 15 minutes (also at most 180 polling cycles).
The 15-minute bound cannot cancel a CloudFormation operation already accepted.

Approval must include at most one protective `StopInstances` request for the exact
pair `i-08e89ad2e3feedb8a`, `i-0c5a2a75ca70db228`, if a host is unexpectedly active
or the procedure fails/loses observations. This may be issued even when state
cannot be read, but only after the exact account identity was verified. No force,
hibernate or termination. After an error, up to three bounded calls (stop and two
readbacks) may extend local reconciliation by approximately 90 seconds plus cleanup.
No protective stop retry is made after uncertainty. Unconfirmed stopping remains
unresolved and requires operator reconciliation.

Before recovery submission, the exact-pair protective authorization is persisted.
If later local evidence writes fail, an in-memory one-shot gate still permits the
same bounded stop request and two final readbacks, with best-effort logging.
Primary failure and missing audit records remain explicit; the result is unresolved
and printed for the operator. A process crash/restart does not replay that fallback.

This is LOCAL protection, not an independent cloud stop guarantee. Crash, lost
credentials, laptop sleep/termination or network loss can prevent protection. There
is no intended compute start, but AWS controls its internal rollback behavior.
Source-off claims are sampled observations, not continuous-state proof. No runtime
capacity or free-plan support guarantee follows from catalogue eligibility.

## Approval, source binding and execution

The human proposal is `mie-planning/PHASE6-STACK-RECOVERY001-REVIEW.md`.
The proposed, not-yet-created root is:

`/Users/antenehhailu/.codex/visualizations/2026/10/01/01a0f6c9-d66b-7d51-85fa-da98aabc6ae7/mie-planning/phase6-stack-recovery-run-001`

The source manifest in the existing `phase6-receipt-recovery-20261021` prep folder
pins the review, clean commit, preserved dependency hashes, new package and tests.
The pinned interpreter files are verified. Root creation is a separate explicit
entry point, requires the actual approval text, and is exclusive/0700. Runtime
records use exclusive nofollow 0600 files with fsync; roots with closure markers or
an execution-start record cannot be reused. Root creation is not submission.

After explicit approval, first refresh STS outside the proposed root. Then use
the pinned interpreter to invoke `recover.py --create-root --approved
--approval-text '<actual user approval>'`. Inspect this result before proceeding.
Run the same script once under `/usr/bin/caffeinate -i`, with `--run --approved`.
Keep the foreground exec session and inspect its completion. Do not run the CLI
indirectly through an outer timeout that could orphan the supervising process.
Do not re-run after a nonzero/unknown result or create a second root implicitly.

Imports and `--help` do not call AWS or create a store. Tests use fixed historical
fixtures, fake AWS and harmless local subprocesses only. `transport.py` carries the
unchanged two bounded CLI helpers from v2 to avoid importing the receipt service
and cryptography for an infrastructure-only procedure; AST equivalence is tested.
The package is versioned separately and does not widen v3/v4 acceptance states.

## Acceptance and closure

Submission acceptance is not success. Require fresh `UPDATE_ROLLBACK_COMPLETE`,
the new exact token's receipt skip and terminal stack events, unchanged identities,
template/policies and stopped hosts, plus individual
`DetectStackResourceDrift(ReceiptInstance)` returning a fresh `IN_SYNC` result with
matching expected/actual small-instance properties and no differences. Preserve
the full response. Reject `MODIFIED`, `DELETED`, `NOT_CHECKED`, missing/stale or
wrong-identity results. Drift covers explicit template properties only; direct
hardware/state checks remain mandatory. This API updates drift metadata but does
not repair a resource. No automatic correction follows a drift failure.

The strongest machine outcome is `RECOVERY_VERIFIED_PENDING_INDEPENDENT_REVIEW`.
An independent reviewer must inspect raw requests, resource/event bindings,
observations and final readbacks before archiving a closure and file inventory.
Otherwise archive as unresolved only with explicit known/unknown resource states.
Root archival is part of this operation; it is not permission to repair evidence.
Successful recovery unblocks later reviewed host work; it does not qualify the
receipt service or finish Phase 6. Later starts/pilots need their own concrete scope.

Official contracts (consulted 2026-10-23):
- https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_ContinueUpdateRollback.html
- https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-cfn-updating-stacks-continueupdaterollback.html
- https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_DetectStackResourceDrift.html
