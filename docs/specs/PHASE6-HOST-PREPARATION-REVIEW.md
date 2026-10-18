> Execution update, 2026-10-18: the user approved this bounded preparation step.
> Attempt 001 is now complete: source preparation succeeded, receipt preparation
> failed its profile check, both hosts are stopped. See
> [the execution report](PHASE6-HOST-PREPARATION-RUN-001.md).
> The original pre-execution review below is preserved as the approved snapshot;
> its pending-approval/unexecuted wording describes the state before this attempt.

# Phase6 — host preparation execution review

Status2026-10-18: locally implemented/tested/reviewed candidate; cloud preparation
NOT approved or executed. This is a bounded build/readiness step before receipt
qualification. Phase6 remains incomplete.

## Exact scope for the next approval

Owner/operator: Anteneh Hailu. Account989879505232, Stockholm/eu-north-1.
Existing stack:
`arn:aws:cloudformation:eu-north-1:989879505232:stack/mie-p6-qualification-001/c0769b00-caf6-11f1-8e71-0affdc797e93`.
Use the unchanged reviewed `deployment/phase6/qualification-v1.json`, SHA256
`ef9f30a8af6bfb817f2ddcebeedbbe5abf88f6b89a17a2a969fb9e36c4591754`.
No duplicate foundation stack.

Request a single update with LaunchCompute=yes and a freshly frozen UTC stop time,
no later than60minutes after the approved attempt begins. Freeze actual timestamps
immediately before the sole update; reject stale or overlong intervals. The
existing operator CLI must record ARMED before the update and continue observing
through stop confirmation. Root operator credentials are for deployment only.
The operator launch contract's runtime_bundle_sha256 binds the complete host kit
`66d595ca5fd3ca5aa689969b60eeba57c217949bd4f24615206e48978ba08592`;
pass that kit to --runtime-bundle. The preparation script separately checks its
embedded Python archive's original SHA256. The local operation record and
precreated operator store are proposed at:
`/Users/antenehhailu/.codex/visualizations/2026/10/01/01a0f6c9-d66b-7d51-85fa-da98aabc6ae7/mie-planning/phase6-host-preparation-run-001/`
with an empty `operator/` child, both owned by Anteneh. These are deployment
operation evidence; a preexisting attempt must not be reused or overwritten.
CloudFormation may roll back a failed update; root disks are retained by the
existing template. Do not retry an unknown update, launch extra hosts or repair a
failed preparation automatically.

This adds the template's two t4g.small ARM64 hosts, one scoped stop role and one
scheduled stop, and extends the existing source role's SSM target permission.
AMI `ami-04e0c7320ad3a3a48`, standard CPU credits, IMDSv2, separate instance
profiles, no inbound security-group rules, public IPv4 for outbound connectivity.
Each encrypted8GB gp3 root disk persists on termination. Existing14 foundation
resources remain. No account-plan upgrade is included.

Upload exactly one public-code-only kit to the existing private artifact bucket:
`mie-p6-qualification-001-989879505232-artifacts`, key
`code/66d595ca5fd3ca5aa689969b60eeba57c217949bd4f24615206e48978ba08592.tar.gz`.
The kit is21,585,579bytes; SHA256 is the key stem. It contains the prior runtime
archive, two ClockBound source crates, preparation/readiness/builder scripts and
an explicit preparation-only plan. Seven manifest files, eight tar members.
No keys, certificates, account credentials, historical datasets or native binaries.
Use one conditional PutObject (IfNoneMatch=*), one-attempt SDK/CLI behavior, then
read and hash the exact returned VersionId. An unknown upload or missing version
stops delivery; do not substitute latest or upload again.

Before delivery, read actual stack resources and both instances: require this
stack ID, source/receipt logical ID, Purpose tag, account, AMI, profile, standard
credits and running state. Freeze the actual instance IDs and kit VersionId in
local request artifacts. The local generator `host-preparation-command.py` renders
one exact AWS-RunShellScript request per host. Inspect/hash these derived requests,
then send each once through the operator's SSM permission. The host roles receive
no new RunCommand/shell permission. Do not start an SSM port-forwarding session.

Host delivery is root-only at
`/var/lib/mie/qualification-001/host-preparation-delivery-v1`.
The fresh software installation is `/opt/mie/qualification-001-host-v1`;
operational build/readiness evidence is its `operations/` directory, owned by
root under Anteneh's account. These are deployment diagnostics, not scientific
stores or prospective evidence. Existing/partial directories are refused and
preserved. No ObjectWriter runtime roots are created.

The host preparation checks IMDSv2 account, region, AMI, instance type, instance ID
and exact instance profile before host mutations. It requires VMClock and the
existing guest stop timer, records package/toolchain versions, installs an isolated
Python3.11 environment, checks pinned wheel hashes/offline installation/version
inventory and CLI imports, then builds pinned ClockBound2.0.3 crates with their
original Cargo.lock files. ABI and dynamic-library checks precede installation;
new native hashes are recorded, never equated to the prior diagnostic008 binary.

RPM installation and Cargo's locked dependency downloads use the network. This
is not an offline native build or proof of reproducible binary output. Actual
RPM/toolchain/SSM Agent versions and native output hashes need inspection after
execution. The Python wheel installation is offline. No system Python symlink is
changed. [AL2023 Python](https://docs.aws.amazon.com/linux/al2023/ug/python.html),
[Cargo locking/offline distinction](https://doc.rust-lang.org/cargo/commands/cargo.html).

Root owns installation bytes; the nonlogin `mie-qualification` user gets group
chrony read access. Prior chrony configuration and VMClock permissions are recorded
before changing this fresh host to the previously qualified Amazon-time/max50ppm
configuration. A non-restarting ClockBound process runs as chrony. Readiness uses
two advancing regular Chrony updates and ten observations from one nonroot native
owner; any native refusal is terminal. Success is only
HOST_PREPARED_PENDING_REVIEW. No receipt listener, runtime secret retrieval, signing,
market-data connection, trading or pilot starts.

Each host command has bounded logs and stage timeouts, with a30minute preparation
budget, a1900second outer command timeout and a2100second SSM execution timeout.
The operator, guest timer and AWS scheduler are additional stop controls. Machine
loss, network/AWS outage or unknown requests can defeat timely stopping. These
are not guaranteed runtime or spending caps. Watch actual stopped/terminated
states and settled stack status; a StopInstances response alone is insufficient.
Preserve failures and all disk/build artifacts; no cleanup or resource deletion
is included. Full operational logs remain on retained disks if export is incomplete.

## Proposed spending allowance and residual resources

Request an expanded combined allowance of **USD3 for the first30days**, replacing
the foundation-only USD1 planning allowance for this combined foundation/preparation
scope. Excludes tax and currency conversion; no free credits assumed. The detailed
AWS price-list inputs were recorded2026-10-17 in the existing qualification proposal:
2t4g.small ×1hour USD0.0344;2IPv4 ×1hour USD0.01;16GB retained gp3 ×1month
USD1.3376;2secrets ×1month USD0.80; approximately22MB code storage at USD0.023/GB-month
plus small upload/read/control request allowance. The listed principal items total
aboutUSD2.18; the proposed USD3 provides room for small additional requests/storage.
This is a planning allowance, not a fresh bill quote or technical AWS cost limit.
Do not enable unlimited CPU credits, extend the interval or upgrade the plan.

Stopped compute does not stop disk, secret or storage charges. The two disks and
two secrets alone have a planning baseline of aboutUSD2.14/month while retained,
before other storage/requests and taxes. No automatic cleanup or cost monitor is
created. Later deletion/retention decisions need their own concrete scope.

## Local verification and next boundary

132 relevant tests collected/executed/passed, zero failed/skipped/blocked:
49 new preparation/readiness/delivery tests plus83 existing runtime/operator/
native-clock tests. These are targeted regressions, not a new full-suite run.
The results are recorded in
`mie-planning/phase6-host-preparation-20261018` under the owner's existing local
planning root. The first exact archive check rejected a standard PAX path header
for a long locked wheel filename; that failure is retained. The validator now
allows only empty headers or the exact canonical path header, with all existing
path/type/size/manifest checks. Review also corrected primary-error masking during
cleanup/logging and added regressions. No AWS operations occurred in this step.

Two independently invoked builds produced identical kit bytes and every member
was rehashed. Syntax checks cover Python3.9 parsing; actual AL2023 execution,
RPM/venv behavior, nonroot native/SHM permissions, AWS SDK/SSM behavior and all stop
controls remain target qualification work. Local test success cannot accept them.

After approved preparation and observed shutdown, inspect actual outputs and
freeze native pins. Separate runtime approval must cover independent source/server
identities and secret versions, trust/config/window, real runtime roots, tunnel
and receipt roundtrips, archive readback/retention verification, faults and capacity.
The subsequent future live-market pilot uses simulated money; it has not started.
H-025 is excluded. The requested complete1–6 integration remains after Phase6.
