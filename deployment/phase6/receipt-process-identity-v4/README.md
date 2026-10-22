# Receipt process identity v4

Local correction for the admission failure in closed attempt006. The virtualenv
launcher resolves to `.../3.14/bin/python3.14`, while the macOS process runs
`.../3.14/Resources/Python.app/Contents/MacOS/Python`. An exact comparison of the
original argv[0] therefore rejected the genuine observer. The failure was
reproduced with a harmless child before this implementation.

This package does not create a root, freeze a window or approve another attempt.
**It is a local control candidate, not a complete attempt007 execution package.**
No CloudFormation, instance, credential, receipt-service or pilot change is made
by installing these files. Closed001–006, all v1/v2/v3 sources, research history
and H-025 remain unchanged.

## Identity and call sites

`identity.py` reads the exact process using the installed Apple SDK ABI:
`proc_pidinfo(PROC_PIDTBSDINFO)`, `proc_pidpath`, `kern.bootsessionuuid` and
`KERN_PROCARGS2`. It binds PID, real/effective/saved UID, start seconds and
microseconds, boot UUID, kernel executable path/hash, and every argument. Birth
identity, executable and arguments are rechecked around the read. The argc parser
is bounded and NUL-aware, preserves empty/space-containing arguments, and never
decodes or persists the returned environment tail. Errors and missing permissions
remain explicit refusals; they are not interpreted as an absent process.

The launcher realpath/hash and kernel executable path/hash are separately pinned
in `macos-python-pin.json`. Only that exact mapping is allowed. The process must
also match the exact script path/hash, root, contract hash and full argument tail.
The profile is specific to this Mac's current Python installation; an interpreter
update requires a new reviewed pin. No basename matching, stripping argv[0] or
learning an arbitrary executable from a live runtime process is used.

| Entry point | Actual verification and effect |
| --- | --- |
| `local-preflight.py` | Two harmless real processes, direct and through the same `caffeinate -i -t 2880` topology; checks reported PID against the launched PID, binds/verifies identity, then closes the child. JSON diagnostic only, no AWS or runtime store. |
| `watch-operator.py` | Requires an existing separately approved v4 root and recorded launch. Publishes and reads back `process-identity.json` before writer creation or AWS reads; publication failure refuses. Delegates observations and protective cloud stops to the hash-pinned v3 observer. |
| `execute-live.py:Supervisor` | Runs the shared binding verifier before and after the preserved complete v3 chain/clock/source/state checks. The v4 live entry retains the unchanged v2 exact-once execution guard and API adapter. |
| `process-control.py lookup/health` | Reads the previously recorded initial PID and verifies the same binding. No process search by name or command substring. |
| `process-control.py interrupt` | Same birth/contract/executable binding, fresh idle observation, exact caffeinate-child exception, persisted exclusive intent, final identity recheck, at most one SIGINT. This does not confirm cloud hosts stopped. |

The operational process-control version is `receipt-process-identity.v4` and the
identity schema is `p6.process_identity.v4`. The observation stream deliberately
retains `p6.qualification_operator_event.v3` and `receipt-update-states.v3`: its
state/clock semantics are unchanged, not relabelled. V4 health and ARMED output
add `process_control_version`; the execute response has a separate v4 filename.
Historical v3 records cannot substitute for the new process-identity binding.

## Runtime prerequisites (future integration)

Every forward entry requires an existing owner-only 0700 root with explicit v4
authorization, review and source-manifest hashes, an archived interpreter pin,
clean matching Git commit, all required package file hashes, and the exact launch
contract. `process-launch.json` must be exclusively persisted by the future
launcher **before exec**. It contains only `initial_pid`, `uid`, `argv`,
`contract_sha256` and `control_version`. Runtime watch binds its own PID to that
record; no automatic adoption of a different PID is allowed.

The future preparation procedure must run the local preflight before freezing
any AWS window and use these v4 entry points throughout lookup, health and
protective interruption. It must provide the reviewed root, source pins,
preflight/closure configuration, approval review and the original bounded AWS
steps. Those attempt-specific changes are **not implemented by this package**.
The old006 procedure remains unchanged and cannot be resumed. No root007 or new
runtime-window authorization exists here.

Protective-only verification skips current preparation source/script checks, so
an already loaded observer can still be asked to stop after a source-pin failure.
It retains the existing approval, root/contract, interpreter and immutable birth
binding. Missing or conflicting identity still refuses; it does not fall back to
an unverified PID. The existing external/guest stop boundaries must remain in the
future attempt; this module is not their replacement.

## Validation and limits

The new suite exercises real Mac direct/caffeinate admission, native child
inspection and SIGINT to owned harmless children, plus negative PID reuse,
boot/UID/interpreter/argv/contract changes, missing permissions, parser truncation,
changed source/approval files, disk publication failure and final pre-signal
identity changes. Synthetic integration covers watcher publication before AWS,
the full Supervisor chain, and the unchanged v2 exact-once guard. AWS subprocess
execution is forbidden in the new tests. Existing v3 and earlier tests remain
separate regression coverage.

These tests do not prove an AWS deployment, successful host preparation, receipt
clock/authentication, independent retention or completed Phase6. File hashing
checks current filesystem bytes, not an OS attestation of the process's mapped
executable. Process inspection and signaling are separate system calls with a
remaining race; this is not an atomic process handle. Root access or malicious
same-user replacement of all local approval/evidence files is outside this
accidental-mismatch/PID-reuse control's threat model.

Run with local macOS process-inspection permission (a sandbox that denies these
APIs must cause refusal, not a passing substitute):

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/private/tmp/mie-phase6-qualification-test-20261018 \
/Users/antenehhailu/Desktop/market-intelligence-engine/.venv/bin/python \
-m pytest -q -p no:cacheprovider -o addopts='' tests/test_phase6_process_identity_v4.py
```

For the explicit harmless check before a future window:

```sh
PYTHONDONTWRITEBYTECODE=1 \
/Users/antenehhailu/Desktop/market-intelligence-engine/.venv/bin/python \
deployment/phase6/receipt-process-identity-v4/local-preflight.py
```

No new AWS attempt is authorized by either command.
