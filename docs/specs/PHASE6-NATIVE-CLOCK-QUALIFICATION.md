# Native clock qualification — 2026-10-17

Status: INCOMPLETE. No prospective pilot, market feed or receipt endpoint was
activated. These are bounded infrastructure diagnostics, not scientific trials.
The adapter code under test is commit bd30255.

Owner: Anteneh / MIE operator. Physical diagnostic packages and console snapshots:
/Users/antenehhailu/.codex/visualizations/2026/10/01/01a0f6c9-d66b-7d51-85fa-da98aabc6ae7/mie-planning/phase6-native-clock-001
through separately identified subsequent attempts. No H-025 or historical result
was read, replaced or repaired.

## Preserved incomplete attempts

001: operator incorrectly base64-decoded already-decoded AWS CLI console text.
The finalizer terminated the exact instance. Initial console output was lost;
after-termination recovery returned empty output. Log handling was corrected,
with decoded ASCII/Unicode/empty and invalid-type regression checks.

002: observed Amazon Linux, VMClock, synchronized chrony and the daemon source
checksum. Build completion and final exit were not retained. Timing suggested
a build timeout, but its exact cause is unverified. Immediate shutdown and
console publication lag are a likely explanation for the missing final build tail;
that cause was not independently established.

003: streamed logs and a final 90-second collection hold preserved exit1 at the
300-second daemon-build limit. CPU credits were changed only for this instance
under a separately reviewed, recorded amendment. It did not reach clock tests.

004: both 2.0.3 release artifacts built, and a compiled program using the actual
C header verified sizes16/40/16 and result offsets16/32. Exported SHELLOPTS then
caused the system ldd script to fail on an unbound variable. The clock adapter
was not exercised. The correction removes that export while preserving parent
shell pipefail. Recorded native artifact hashes:
- library:25613cf5d4a4de8da31c4c937b0c129d4f737e06118d319a2f4a26b2dbb00eda
- daemon:e14e4319ddb5e8f17ecebf30b6ddf63592ad70a1e8fe6d83daa59f4c0e20c84b

## Resource and interpretation boundaries

Each attempt uses one t4g.small in Stockholm, official pinned AL2023 ARM image,
no instance IAM profile, no ingress, HTTPS egress, encrypted disposable8GB disk,
shutdown-to-terminate, guest18-minute and operator20-minute deadlines. A final
confirmed `terminated` state and disk check are required. Console publication is
best-effort; raw snapshots are retained and duplicate lines are exposed during
analysis. Guest exit0 alone cannot imply qualification.

004/005 use unlimited CPU credits from launch. Maximum full20-minute component
estimate is approximately0.03438USD, including2vCPU surplus at0.04USD/vCPU-hour,
compute0.0172/hour, publicIPv4 0.005/hour and8GBgp3 at0.0836/GB-month. Each0.05USD
planning allowance is not an enforced spending cap. Actual billing is not yet
verified. No paid-plan upgrade was performed.

A completed diagnostic still cannot prove continuous event-time validity,
production permissions/dependency immutability, source transport integration or
receipt-service acceptance. The actual daemon drift policy assumes50PPM and
requires separate deployment review; it is not a universally measured hardware
bound. Linux clock support does not qualify the Mac or Lambda clock.


## Diagnostic005: actual Linux observations

Guest completed with exit0. Both artifact hashes reproduced004. Native compiled
C ABI checks matched ctypes. The adapter loaded the real library through its
sealed-memfd path as the chrony user; no root application credentials were used.
Wrong-library digest and absent-daemon cases were refused. Descriptor-count checks
passed across the exercised failed opens and repeated close paths.

Active mode: indices0 and1 were refused as unsynchronized/invalid; indices2–11
were accepted with conservative returned bounds1–2ms. Five seconds of daemon
startup wait was insufficient to guarantee readiness in this run. A future
admission must verify readiness before the single pilot attempt; it cannot assume
a fixed delay or silently restart an admitted owner after refusal.

Forced-disruption mode:12/12 attempts were refused. After SIGUSR2 and a2-second
wait, the daemon was stopped;12/12 subsequent attempts were also refused. This
latter result does NOT isolate daemon-stop detection, because resynchronization
was not established before stopping. A synchronized-then-stop test remains.

The diagnostic deliberately creates a new clock owner for each observation.
It does not establish continuous one-owner operation or authorize restart after
a failed pilot. Native success does not complete Phase6. Remaining clock work:
readiness/admission policy, isolated synchronized-stop and stale-shared-memory
behavior, production dependency/daemon permission freeze, sustained owner tests,
and actual source/service wiring. Cleanup must be confirmed in retained AWS state.


Cleanup confirmed: all five exact diagnostic instance IDs report `terminated`;
the tagged-volume query returned an empty list. The earlier Fargate diagnostic
stack is separate and remains idle; this is not a claim that all AWS resources
have been deleted. Actual charges have not yet been reconciled.

Independent review confirmed005 counts, native hashes, probe bindings and
instant-only validity against retained console SHA256
5f402a637998d6e2b941364cf43bc2d59dce390c8ada8be815fa393a02df37c1.
Cleanup API responses are retained in005/all-cleanup-instance-states.json and
005/all-cleanup-volumes.json. No source-code change followed this diagnostic;
these are documentation/evidence updates, not a new full-test-suite result.
