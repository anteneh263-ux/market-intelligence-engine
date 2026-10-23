# Small008 — receipt host preparation on the existing free-plan instance

**PAUSED BY OWNER, 23 October 2026. This is an unexecuted draft.** The CLI entry
point refuses both root creation and execution, including --approved. No root008
exists. Do not remove the pause gate or run internal functions to bypass it.
The scope below is retained as design documentation only. Future resumption
requires the user's strategy acceptance criteria and fresh explicit authorization.

Local candidate. Execution requires separate approval of this version, physical
root, exact cloud scope and window. Recovery001 approval does not authorize it.
No runtime root exists while preparing/reviewing this package.

Owner Anteneh Hailu. Account989879505232, eu-north-1. Existing stack and source/
receipt IDs are hard-pinned in guard.py. Physical administrative evidence root:
`/Users/antenehhailu/.codex/visualizations/2026/10/01/01a0f6c9-d66b-7d51-85fa-da98aabc6ae7/mie-planning/phase6-host-preparation-run-008`.
This is deployment evidence, not scientific/pilot evidence. H-025 is excluded.

## Scope

One schedule-only UpdateStack using the original unchanged template, LaunchCompute
previous value and a fresh StopAtUtc; one explicit start of existing receipt
`i-0c5a2a75ca70db228`, t4g.small; source `i-08e89ad2e3feedb8a` stays stopped.
No resizing, replacement, new instance, disk, IAM, network setting, key material,
plan upgrade or pilot. Existing exact small host-kit object version is read and
verified, never uploaded or replaced. Old medium kits cannot satisfy this path.

One read-only SSM admission, one transient guest stop timer, one install command,
then bounded read-only log exports. AWS-RunShellScript version1. Original small
generator/timer bytes are reused; their historical "attempt002" comments identify
implementation provenance, while requests/results belong only to small008.
Installation `/opt/mie/qualification-001-host-v1`, delivery-v2, and runtime user
must be absent on receipt. Existing directories/users cause refusal, not repair.
Source installation, failed receipt delivery-v1 and historical evidence remain.

## New admission contract, separate from recovery001

Recovery001 remains RECOVERY_UNRESOLVED, closed as
STOPPED_STACK_RECOVERED_DRIFT_UNRESOLVED. Its IN_SYNC requirement is unchanged.
For small008, require UPDATE_ROLLBACK_COMPLETE initially, same18resources, original
small template, policies/disks/credits/identities, stopped pair, and fresh exact
EC2 primary-ENI/attachment bindings with AssociatePublicIpAddress=true on BOTH.
The cached individual CFN result must be either exact IN_SYNC or ONLY the original
recorded /NetworkInterfaces/0/AssociatePublicIpAddress true/false difference with
the original expected/actual property sets and exact resource identity. Additional,
unknown, changed, deleted or paginated drift is refused. Cached does not mean fresh
drift detection. The contradiction is reported, never marked repaired or explained
as a proven AWS bug. No setting is modified merely to clear that report.

After schedule update, require UPDATE_COMPLETE, only stack/ExternalStop events
under this operation token, unchanged identities/template/permissions, and the
actual enabled exact UTC schedule and pair-only StopRole policy before starting.
No arbitrary drift, hardware change or account change is accepted.

## Protection and bounds

Use pinned Python under `/usr/bin/caffeinate -i`. Single local supervising process;
no separate operator PID discovery/signaling. AWS child process groups are bounded
to30seconds and1MiB per captured stream; killed on timeout/overflow. Only one AWS
attempt; unknown mutations are not resent. An eventually invisible SSM invocation
may retry that same read within its bounded poll loop, never resend SendCommand.

Fresh absolute45minute window covers preflight, kit GET, schedule update, start,
boot, guest protection, build/readiness and log export. No deadline extension.
Local wall/monotonic disagreement>2seconds or observation gap>90seconds refuses.
Observe exact pair and stack repeatedly, including during SSM polls. This is
sampled observation, not proof of continuous state. Source activity aborts.
The future AWS schedule must be verified before start. Guest timer is about30s
before the external deadline, with exact D-Bus/action/monotonic readbacks enforced
by unchanged small generator. At least2080s must remain before preparation, its
actual execution is bounded to1900s, and the guest independently rechecks2000s.

Completion or any failure invokes at most one prearmed protective StopInstances
for the exact pair, even if audit storage fails; never terminate/force. Then at
most four state reads and one stack read. Worst-case final reconciliation is about
195seconds PLUS bounded child cleanup (up to5s per failed call), local I/O/scheduling
after the forward window. There is no new forward work in that period. Observed
stopped pair and settled stack are mandatory for strongest result. Cloud/guest/
local stops are protection layers, not shutdown guarantees or billing caps.

## Execution after explicit approval only

Verify clean source commit and source-pins.json, exact review hash and interpreter.
Use pinned `/Users/antenehhailu/Desktop/market-intelligence-engine/.venv/bin/python`.
First `run.py --create-root --approved --approval-text <verbatim approval>` once.
Inspect the resulting authorization/pins. Then `run.py --run --approved` once under
caffeinate. Never chain blind retries, reuse a consumed root, or edit source during
the run. Package itself has no AWS/start/root side effects on import or help.
No automatic later host/service/pilot step exists.

Strongest outcome HOST_PREPARED_PENDING_INDEPENDENT_REVIEW requires install-command
success, exact hash-verified operation-log export and observed stopped pair with
UPDATE_COMPLETE. Independently inspect logs/native binaries/clock readiness before
accepting point1. Host installation does not qualify the receipt service, sustained
clock/authentication, independent retention, integrated faults/capacity or pilot.
On failure preserve the cause/raw output and original files; no automatic repair.

Keep the existing combined USD3 before-tax first30day planning allowance from
18October, not a technical spending cap. No allowance increase or assumed credit
coverage. Current measured bill/credit expiry remains unverified. Existing retained
resources can continue costing money after compute stops. Stay on FREE plan.

## Verification

New negative tests cover exact network/identity/policy/state changes, narrow drift,
clock/deadline failures, unknown update/start, write failure, unsuccessful install,
corrupt logs, exact S3 custom argv, and fake end-to-end success. Existing versions
and original recovery acceptance are regression-tested without modification.
Local success does not establish AWS capacity or actual AL2023 timer behavior.
