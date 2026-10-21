# Phase 6 — receipt host preparation attempt 002 review

Status 2026-10-21: corrected kit built and verified locally; recovery controls
implemented, locally tested and independently reviewed. No attempt002 cloud mutation has occurred. The user
resumed the next receipt-server step and renewed the expired AWS login. Current
read-only AWS observations confirm the intended account, existing UPDATE_COMPLETE
stack, both hosts stopped, exact profiles, and expired 18 October stop schedule.
Phase 6 and receipt runtime qualification remain incomplete.

## Concrete scope

Owner: Anteneh Hailu. Account 989879505232, Stockholm/eu-north-1. Existing stack:
`arn:aws:cloudformation:eu-north-1:989879505232:stack/mie-p6-qualification-001/c0769b00-caf6-11f1-8e71-0affdc797e93`.

Start only existing receipt instance `i-0c5a2a75ca70db228` once. Its profile must
remain `mie-p6-qualification-001-ReceiptRoleProfile-9F2A3mVypDyp`, pinned Amazon AMI
`ami-04e0c7320ad3a3a48`, t4g.small, standard CPU credits, IMDSv2 and encrypted
8 GB gp3 volume `vol-0d777f69ef62e0bbd`. Revalidate these actual facts and all
CloudFormation/account/logical-ID/Purpose ownership before any mutation.
Source `i-08e89ad2e3feedb8a` remains stopped and its installation is preserved.
No instance replacement, new volumes, IAM/secret changes or new foundation stack.

Freeze a new absolute 45-minute UTC/continuous window immediately before execution.
Run the existing qualification operator in watch-only mode, WITHOUT --template or
--runtime-bundle. Record/verify ARMED and actual observation before any restart.
This operator observes both exact-stack hosts and may protectively stop either
if unexpectedly active; it does not start hosts or update the stack in this mode.
Do not describe its protection scope as receipt-only. One new local test exercises
the existing pair stopped → receipt running → receipt stopped lifecycle.

Refresh only StopAtUtc through one existing-stack UpdateStack, using the previous
unchanged template and LaunchCompute value, with a frozen request token. Confirm
UPDATE_COMPLETE, unchanged instance/resource identities and the actual enabled
Scheduler target, role, UTC expression and no flexible window before starting.
The scheduled backup retains both existing instance IDs. It cannot start either.
Only ScheduleExpression should change; any unexpected resource change stops work.
Do not reuse an old schedule or repeat an unknown update/start.

The whole window includes schedule update, restart, boot, SSM admission, guest
protection, package delivery, build/readiness and collection. Check fresh operator
observations and sufficient time before StartInstances, each SSM command and
preparation. Any failure/unknown outcome triggers observation/protective shutdown;
do not wait for a successful build or silently extend the deadline.

## Kit and delivery

One corrected public-code-only kit: `receipt-host-kit-v2.tar.gz`, 21,585,589 bytes,
SHA256 `ea831b59505cfdd04e4c6f78bff4de63fc3fa71045e5279d5d7fe6b7d02c5c69`.
Two independent local builds were byte-identical and all eight tar members were
read back; seven are manifest-listed payload files. The receipt profile pin and
plan now match the actual foundation. Source/runtime/native inputs are unchanged.
The native binaries are still built on the receipt host; no source-host output
hash is assumed equivalent to a new build.

Perform one conditional PutObject IfNoneMatch=* into existing private bucket
`mie-p6-qualification-001-989879505232-artifacts`, key
`code/ea831b59505cfdd04e4c6f78bff4de63fc3fa71045e5279d5d7fe6b7d02c5c69.tar.gz`.
Verify exact returned VersionId, byte count and SHA on a separate GET. Preserve
attempt001's object/version. No blind re-upload or substitution of latest version.
The eventual preparation request binds that exact version and is sent once.

Use new root-only delivery path:
`/var/lib/mie/qualification-001/host-preparation-delivery-v2`.
The existing v1 delivery directory remains unchanged. Installation target remains
`/opt/mie/qualification-001-host-v1` on receipt, which was never created by failed
attempt001. Recheck that target and runtime user `mie-qualification` are absent;
the installer refuses partial installations or an existing user. No resume/repair,
chmod of unexpected existing parents or overwrite of the failed attempt is allowed.
The source host's same-named installation is not targeted.

## Guest timer admission

`receipt-recovery-timer.py` is embedded verbatim into frozen SSM request bytes;
it is not a new persistent service installation. AWS-RunShellScript version1 is
pinned. One arm command first verifies root and exact receipt IMDSv2 identity
without credential endpoints, then creates the transient shutdown timer once.
It never resets/replaces an existing timer; systemd-run failure is terminal.

The timer targets /sbin/shutdown -h now as root about 30 seconds before the external
UTC stop. The candidate reads numeric D-Bus deadline, action/user, active/waiting
state, unit name, 1-second accuracy and zero randomized delay. It requires at least
35 minutes at arm time. Save the actual monotonic deadline in the arm result and
pin it into the one delivery request. That request rechecks the same identity,
timer, deadline and minimum 2,000 seconds remaining both before download and after
extraction, immediately before the 1,900-second preparation command. A shifted
UTC/monotonic relationship, replaced timer, wrong action or short interval refuses
preparation. Preserve all timer/refusal outputs; do not automatically rearm.

The shell/SSM/local observer also have bounded request timeouts. These controls are
not guaranteed shutdown or billing caps during outages or machine loss. The guest
timer's actual AL2023 systemd behavior still needs this attempt's target readback;
local mocked tests do not establish cloud behavior. The first local parser test
caught a method-message/property-value JSON shape mismatch; its failure and fix
are preserved before any AWS timer command.

References: [systemd D-Bus timer interface](https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.systemd1.html),
[busctl JSON encoding implementation, v252](https://github.com/systemd/systemd/blob/v252/src/busctl/busctl.c),
[AWS ScheduleExpression update behavior](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-scheduler-schedule.html).

## Evidence, result and cost boundary

New actual operation evidence is proposed at:
`/Users/antenehhailu/.codex/visualizations/2026/10/01/01a0f6c9-d66b-7d51-85fa-da98aabc6ae7/mie-planning/phase6-host-preparation-run-002/`,
with an empty `operator/` child owned by Anteneh. Refuse reuse. Host operational
logs live under the fresh installation's `operations/` directory. These are
administrative deployment diagnostics, not scientific runtime/pilot evidence.
Local preparation/read-only evidence is in sibling `phase6-receipt-recovery-20261021`.

After the one preparation attempt, collect exact outputs/log hashes and native
pins, obtain dedicated review and observe actual receipt stopped/source stopped
states with settled stack. Preserve failures and retained disks. Do not count an
accepted stop request as confirmation. Early shutdown may use the already tested
operator interrupt path; verify its actual resulting stop evidence and states.

Keep the existing combined USD3 first30-day planning allowance from18October;
no allowance increase, free-credit assumption or plan upgrade. This adds one short
receipt restart, one approximately22MB code version and control requests, with no
new disks or secret containers. Existing retained-resource charges continue;
the previously documented disk+secret planning baseline was aboutUSD2.14/month
before other storage/requests/tax, not a measured bill. No cleanup is included.

Success means receipt HOST_PREPARED_PENDING_REVIEW only. It does not establish
source/server identities, authentication, receipt runtime, independent retention,
tunnel, fault/capacity qualification or prospective acceptance. No secret values,
receipt listener, live-market connection, pilot, trading or H-025 work is included.
The first attempt's failure stays failed and its source success remains separately
identified. Further failures do not authorize automatic repair/repetition.


## Local verification completed

107 targeted tests passed (30 recovery, 52 host preparation, 25 existing operator)
in1.70seconds. Independent review passed82 recovery/host tests with the pinned
qualification dependencies. The reviewer's initial base-only environment lacked
cryptography (81passed/1importfailure), so no target/runtime failure follows.
The first local recovery pass preserved2parserfailures/28passes; method-vs-property
JSON handling was corrected before the passing107-test runs. No full-suite rerun
or target-host qualification is claimed.

Reviewed timerSHA256 `8cbd4273ea287f0fb4d99b0b3c9dff791e767b49e16ce6cd90f368e1b3ad54fa`;
generatorSHA256 `6b19b2ddad040cfb449827cfe2428ab8628d02049a8d23bb06bb9d17d8841eb9`.
The corrected host kit remains the separately pinned archive above. The timer is
carried by the frozen SSM request, not silently added to that archive.
