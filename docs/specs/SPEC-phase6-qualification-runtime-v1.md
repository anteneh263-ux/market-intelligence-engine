# Synthetic receipt qualification runtime v1

Status 2026-10-18: implemented and locally integration-tested; not deployed or
accepted. This package qualifies the receipt protocol only. Its three explicit
synthetic fixture subjects are not Phase5 registration, trial admission, market
observations or evidence of a profitable strategy. No H-025 input is used.

## Actual runtime path

`app.research.phase6_qualification_runtime.main` reads one bounded, duplicate-free,
SHA256-pinned config. `run` validates the fixed account/region, explicit distinct
instance-role sessions, exact secret ARN/version, CA/leaf certificate/key matches,
Ed25519 signing public key, native library pin and pre-existing empty root paths.
STS must identify the exact instance session; root operator credentials cannot
serve as runtime credentials. Boto3 uses instance-role credentials, explicit AWS
regional HTTPS endpoints, bounded timeouts and one SDK attempt.

Linux TLS loads the exact identity from a sealed anonymous memory file. Private
keys are not written to disk or printed. The runtime constructs the real
ClockBoundReceiptHost or RecordedClock/VerifiedReceiptClient. Clock admission
precedes listening or remote requests. Receipt mode serves six exchanges; source
mode appends three synthetic subjects and verifies fresh challenged heads and
signed prefixes. Native clock observations, client artifacts, subject bytes and
result inventories use existing ObjectWriter stores. Closure, full inventory
inspection and exact final-report readback precede acknowledgement. An unknown
write or response terminates that attempt; it is not automatically retried.

The config schema is `p6.aws_receipt_qualification.v1`. Exact fields/checks are in
`validate_config`; no config with real identities/times/roots is approved yet.
The receipt-mode status is REQUESTS_SERVED, source status ROUNDTRIP_VERIFIED.
Both retain runtime_accepted=false and prospective_evidence=false. Independent
source retention, actual service object versions/retention and deployment identity
must be checked separately; a positive local result does not close those gates.

## Operator path and limit

`app.research.phase6_qualification_operator.main` requires an exact hashed
`p6.qualification_launch.v1` contract and explicit execution flag. Launching an
existing foundation additionally requires both the reviewed template bytes and
runtime archive bytes with matching hashes. This binds the supplied artifact;
CloudFormation does not install or execute that archive automatically.

The operator checks STS, exact stack ID/state, complete owned-instance inventory,
a fresh 30-second-to-one-hour remaining interval and a maximum one-hour authorized
window. It durably records ARMED before its sole update-stack request. It watches
from the operator machine using UTC and continuous elapsed time, detecting clock
regression, disagreement, missing samples and a clock that ceases to advance.
Failed/unknown launch, observation or evidence recording triggers protective
stopping. Each stop requires fresh exact stack/account/logical-ID/purpose ownership.
The operator never deletes resources and never retries an unknown launch/stop.

A successful stop API reply is insufficient. STOP_CONFIRMED requires observed
stopped/terminated states for every known instance and a settled stack. Failed
logging cannot prevent attempted protective stops and cannot produce a complete
acknowledgement. A finite observation fallback returns UNRESOLVED. Local process
termination, operator-machine loss, prolonged AWS outage or stalled requests can
still defeat timely observation/stopping. This is not an independently hosted
watchdog, a guaranteed one-hour runtime bound, or an AWS spending cap. Guest
shutdown and the external AWS scheduler remain additional unqualified controls.

## Pinned Python archive

`deployment/phase6/build-qualification-bundle.py` starts from the two entry points,
includes their explicit repository import closure, and checks eleven Linux ARM64
CPython3.11 wheels against the recorded hash lock. It produces a deterministic
archive with a per-file manifest. It does not collect ignored files, data, configs,
credentials or historical evidence. It never uploads, installs or starts a service.

The 2026-10-18 candidate archive has 42 manifest files and 21,504,298 compressed
bytes, SHA256 `370343f69e8ac0f4264b239c3ed43339cebb4cf4c9d45d2720b8833b1c060034`.
Two independently invoked builds produced identical bytes. Every manifest member
was rehashed; isolated extracted-code CLI imports passed using local macOS
libraries. Offline hash-required pip resolution passed for the target wheels.
This is not execution of Linux wheels on the target. AL2023's system python3 is
3.9; the target requires an explicit python3.11 installation, without replacing
system Python. See [AWS Python guidance](https://docs.aws.amazon.com/linux/al2023/ug/python.html).

No native ClockBound binary, private identities, approved roots/config or complete
host bootstrap is included. Native build/readiness and pin freeze, fixed SSM
tunnel startup, identity provisioning, independent artifact export and target
fault/load/retention inspection remain required before runtime activation.

## Verification and evidence

56 focused tests passed independently. Tests use actual local TCP/mTLS, full
receipt/client/store code and controlled AWS/native-clock substitutes. Failures
cover certificate/identity/config/size bounds, lost append replies, final result
publication, closure, stale launches, mismatched bundles, partial stack failure,
unknown update/stop replies, frozen/regressing/unavailable clocks, lost observations
and trace failure. No AWS resources were created by these tests.

Dedicated review found three issues, fixed with regressions: missing actual bundle
hash enforcement; frozen clocks exhausting observation without protective stop;
and an unbounded shared-mapping expansion in the new config copier. Earlier PEM
and cleanup findings were also fixed. Linux sealed-memory TLS, instance-role SDK
behavior, actual SSM/network and cloud stop/retention behavior remain unqualified.

Operational development evidence is under the owner's existing planning area:
`/Users/antenehhailu/.codex/visualizations/2026/10/01/01a0f6c9-d66b-7d51-85fa-da98aabc6ae7/mie-planning/phase6-qualification-runtime-20261018`.
This is software/build/test evidence, not a scientific pilot store.


Final local regression on2026-10-18: **3,837 collected/executed/passed**, zero
failed/skipped/blocked,226.10seconds. The first attempt's3,836 passes and one load
failure are preserved. Instrumentation confirmed the test-only2second accept
budget expired at exchange33; the load fixture now uses the existing120second
service idle budget and reports worker failures. Production10second handshake
limits are unchanged. The corrected load case passed separately,610frames and
10segments; full regression then passed. Reviewed runtime/operator hashes and
the Python archive are unchanged. No target-host or prospective claim follows.
