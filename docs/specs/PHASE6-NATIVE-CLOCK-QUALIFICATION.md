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


## Diagnostic006: persistent-owner failure

After five consecutive successful readiness observations, the one persistent
clock owner returned11 accepted readings (indices0–10), then refused index11
with the generic unsynchronized/invalid-interval error. The harness exited1
without reopening the owner. No stop request was issued: the isolated stop test
was not reached. The exact failing native tuple/brackets were not retained, so
this result alone cannot establish whether the cause was a native status or
an adapter timing invariant. Instance termination and empty tagged-volume results
were confirmed. Earlier short005 successes do not override this failure.

Commit28e9ed4 adds exact scalar failure diagnostics to ClockRefused. The existing
acceptance predicate, uncertainty limit and permanent-stop behavior are unchanged.
28 focused tests passed; independent review confirmed the change. The next
separate diagnostic007 retains native interval/status and both clock brackets
from the actual failing call, plus daemon/chrony diagnostics. It is not a retry
of a scientific pilot and cannot erase006.

The pinned clock-bound-shm2.0.3 source defines a five-second restart grace period
before old synchronized state becomes FreeRunning. FFI2.0.3 locks
clock-bound-vmclock2.0.2; these component versions must not be conflated. The
adapter rejects FreeRunning; an immediate refusal upon daemon stop is therefore
not the expected native contract. A later native read is only contextual evidence.

The diagnostic verifier now checks readiness, exact lifecycle ordering, one owner,
probe bounds and binding, recomputed refusal latency, sticky refusal and completion.
Eleven synthetic positive/negative verifier checks passed. Sampling span is
reported from observations, without inventing a post-hoc tolerance.

## Diagnostic007 and separate008 stabilization check

Diagnostic007 refused persistent sample index12 with native status2
(FreeRunning). The exact failing call's MONOTONIC bracket was40,730ns and
BOOTTIME bracket42,946ns, so the recorded brackets satisfy the adapter ordering
invariant. The owner was not reopened and the controlled stop was not reached.
The guest exited1; instance i-021bff61f7c324538 terminated and its tagged-volume
query returned no volumes.

Daemon logs show startup reset/burst of Chrony. Tracking immediately after the
failure reported a2-second update interval, normal leap status, Amazon NTP,
and a reference approximately17seconds old. The pinned daemon source marks a
synchronized tracking observation FreeRunning when its reference age exceeds
eight update intervals. This supports a startup-transition explanation; it does
not independently prove the exact branch taken at the failing call.

Separate diagnostic008 adds a pre-admission stabilization check: two strictly
increasing Chrony reference timestamps observed after its baseline, normal leap
status, the configured Amazon NTP source, finite update interval>=16seconds,
and a90-second deadline. Invalid observations reset the count; duplicates do
not increment it. Five consecutive ClockBound readiness checks follow, then
one persistent owner. The100ms bound and terminal refusal predicate are unchanged.
The probe alarm is explicitly240seconds; guest18-minute and operator20-minute
termination bounds remain unchanged. This is diagnostic admission only, not yet
a deployed market-source admission policy.

Eight synthetic warmup checks and17 synthetic semantic-verifier checks passed.
Independent prelaunch review checked the embedded payload and deadline. Its
operator.py standard-library shadowing finding was fixed by renaming the008
launcher to aws_clock_operator.py before execution. Reviewed UserData SHA256:
0ac834d08de9af59c97a68bd0b43cf6c6f27e23763d7c959ce7323b43cd78fc2.
Outputs are retained in the previously identified operator-owned planning root,
under phase6-native-clock-008. Earlier failed diagnostics are preserved.

Diagnostic008 completed with guest exit0. Retained99 linked events show61 accepted
samples from one owner across60.00108348seconds, with2–3ms returned bounds. The
supervisor confirmed daemon PID27255 stopped only after this series. The same
owner then accepted20 bounded readings before refusing native FreeRunning at
5.021837131seconds after stop acknowledgement. Two further calls refused without
reopening. File descriptors returned from4 to4. This behavior is consistent with
the native five-second cached-state grace period, not immediate stop detection.
The semantic verifier passed; console parsing found no errors. Event chain head:
95e9e1599ea09c4f44039637e1b08056add01ab3bfcacc69f77f6160cbedd0bc.

This closes the bounded synchronized-then-stop diagnostic gap. It does not prove
long-running reliability, event-time feed binding, deployed service behavior or
prospective pilot acceptance. Production admission policy and dependency/permission
freeze, source/service wiring, assembled fault/capacity verification and a separately
approved prospective pilot remain. Phase6 is INCOMPLETE.

Independent review reconstructed all99 events from raw snapshots and confirmed the
reported values. Instance i-0a3704fd616a42a27 reached terminated; the008 tagged
volume query returned an empty list. This diagnostic has no remaining compute.
The separate idle Fargate stack remains outside this cleanup claim. Component
estimates are not reconciled billing. No GitHub push or prospective activation
was performed as part of this diagnostic.
