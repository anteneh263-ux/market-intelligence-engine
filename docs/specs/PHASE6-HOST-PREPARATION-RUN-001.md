# Phase 6 — AWS host preparation attempt 001

Execution date: 2026-10-18. Outcome: source host preparation/readiness succeeded;
receipt host preparation failed its identity preflight. Both instances are stopped.
The pair is NOT qualified and Phase 6 remains INCOMPLETE.

## Authorization and physical evidence

The user replied “neste” to the complete host-preparation proposal. The operator
explicitly stated that this approved the proposed bounded server/clock test and
combined USD 3 first-30-day planning allowance. This consumed one existing-stack
update, one exact code-kit upload and one preparation command per host. It did not
authorize automatic preparation retry/repair, secret values, runtime listeners,
port forwarding, trading, a pilot, account upgrade or resource deletion.

Owner: Anteneh Hailu. Account 989879505232, region eu-north-1 (Stockholm).
Stack:
`arn:aws:cloudformation:eu-north-1:989879505232:stack/mie-p6-qualification-001/c0769b00-caf6-11f1-8e71-0affdc797e93`.
Operation evidence, including raw AWS requests/results, is physically retained at:
`/Users/antenehhailu/.codex/visualizations/2026/10/01/01a0f6c9-d66b-7d51-85fa-da98aabc6ae7/mie-planning/phase6-host-preparation-run-001/`.
These are deployment diagnostics, not prospective scientific evidence. Original
attempt artifacts, failures and native outputs have not been relabelled or replaced.

The proposal is preserved in PHASE6-HOST-PREPARATION-REVIEW. The frozen template
remained `ef9f30a8af6bfb817f2ddcebeedbbe5abf88f6b89a17a2a969fb9e36c4591754`.
One conditional kit upload, 21,585,579 bytes, was read back by exact version and SHA:

- Bucket: `mie-p6-qualification-001-989879505232-artifacts`.
- Key: `code/66d595ca5fd3ca5aa689969b60eeba57c217949bd4f24615206e48978ba08592.tar.gz`.
- Version: `a1inGXa8VLuxOOMm1uKpnBPJlOMRC0k5`.
- Kit SHA256: `66d595ca5fd3ca5aa689969b60eeba57c217949bd4f24615206e48978ba08592`.

The operator recorded ARMED before the only UpdateStack. The fresh 45-minute
contract started at 14:13:05 UTC and fixed a stop at 14:58:05 UTC. CloudFormation
reached UPDATE_COMPLETE with 18 resources. Both actual instances were checked for
stack/account/logical-ID ownership, profile, pinned AMI, standard CPU credits,
IMDSv2 and SSM availability before delivery. AWS-RunShellScript document version 1
was pinned. Actual SSM Agent version was 3.3.5390.0.

## Results and exact deployed identities

| Role | Instance | Retained encrypted 8 GB gp3 root volume | Preparation |
| --- | --- | --- | --- |
| Source | i-08e89ad2e3feedb8a | vol-0c92009fa2fb849af | Success, exit 0, 6m36.708s |
| Receipt | i-0c5a2a75ca70db228 | vol-0d777f69ef62e0bbd | Failed identity preflight, exit 2, 1.458s |

Source command: `378089bf-b17a-4afb-ae8f-50922e122552`.
Receipt command: `284ce7d8-ce69-48ad-b832-c0ba3f44d1b0`.
Each preparation command was sent once. Later commands only read diagnostic files
or identity metadata; none repeated installation, retrieved secret values or started
a receipt listener.

Source completed all 28 preparation stages, including actual Python 3.11/offline
wheel installation, locked ARM64 native builds, ABI/link checks, nonroot access and
clock readiness. Its result is HOST_PREPARED_PENDING_REVIEW, never runtime acceptance.
The complete 32 operational files (77,539 bytes) were exported read-only and each
file matched the previously collected size/SHA inventory. Exact originals are in
`source-operations-exact/`; original host logs remain under
`/opt/mie/qualification-001-host-v1/operations` on the retained disk.

Native output pins from this build:

- clockbound: `6b8e4c739f62bfe334626bfd90acb5c04f5de8c9a4c9ab0942583ec32e25791d`.
- libclockbound.so: `319129e07f877fb2f5d6c20ba80c0128d2c2db56eb61c21de0ba3a69cf2a4748`.
- Readiness log 028.log: `4123cdf6c6aea48408278a56d58fd2cbefac198fb56c80a77037c9548ef43938`.

Readiness waited for two advancing regular Chrony updates from Amazon time, then
collected ten samples from one nonroot native clock owner. Session:
`26a534a333984183778831934254f606703fda62651cc173649771fcf782c2e4`.
This short diagnostic does not qualify sustained timing, failure handling, receipt
issuance, independent receipt retention or the live market-data chain. The recorded
native binary hashes apply only to this actual build, not to older diagnostic
binaries or a future rebuild. RPM and Cargo downloads used the network; binary
reproducibility has not been established.

Receipt failed because the frozen installer expected the source profile suffix:
`ReceiptRoleProfile-zUFLg78C0FsN`. The actual approved receipt profile is
`mie-p6-qualification-001-ReceiptRoleProfile-9F2A3mVypDyp`. Read-only IMDS diagnostics
confirmed the other identity checks matched and the installation directory did
not exist. This was an installer defect, not an incorrectly attached AWS role.
The check stopped before installation or secret access.

The local installer now uses the correct receipt profile. Regression tests bind
independently recorded actual source/receipt profile facts, accept the expected
identity and refuse a foreign profile. The negative run reproduced the receipt
failure (1 failed, 1 passed, 50 deselected); after correction all 52 host-preparation
tests passed. Dedicated review independently ran the same 52 tests. The correction
is not in the frozen uploaded kit and has not been executed on the receipt host.
The failed attempt remains failed. No automatic retry or in-place remote repair ran.

Before either preparation was delivered, review also found that restrictive umask
would make the missing /opt/mie parent inaccessible to the nonroot runtime user.
The delivery wrapper now creates only an absent parent with explicit root:755,
verifies its canonical path/mode and refuses an unexpected existing parent. It
never chmods an existing parent. This fix was reviewed and tested before delivery,
committed as 1ea4eb7, and bound into the two v2 requests. Old v1 requests remain
saved and were not sent. The host kit itself was unchanged.

## Shutdown and retained evidence

After both commands ended and logs were collected, one SIGINT to the exact idle
operator entered its existing protective-stop path. Its 200-event hash chain is
contiguous and contains exactly one update request and one StopInstances request
per instance. Final event: STOP_CONFIRMED, reason OPERATOR_INTERRUPTED. The operator
exited 0. No fallback stop was used and no uncertain stop was repeated.

Final event SHA256:
`d639aeab79bf2537fa88a1ab7f4a20a1cc9c8400eb810343f5f5a8f99817007d`.
A separate AWS readback at 14:33:35 UTC confirmed both instances stopped and the
stack UPDATE_COMPLETE. Both encrypted disks are retained; no resources were
removed. The scheduled stop remained configured at 14:58:05 UTC and was not the
mechanism used to establish observed shutdown. Guest/scheduler backup execution
was not tested by this early operator stop.

Final independent cloud readbacks also found zero versions in both secret
containers, zero receipt object versions, both state/index tables empty by
consistent COUNT scans, and exactly the one uploaded code object version. Raw
results and checks are in `final-*.json` and `final-verification.json`.

Preserved auxiliary failures: initial exact-version GET command syntax refusal;
first stop-policy lookup used the wrong policy name; combined read-only log export
exceeded its output-size guard. Corrected read-only calls completed without
repeating a PUT, stack update or host preparation. Every original failure remains
in the operation directory. The full logs were exported in two bounded parts.

The combined USD 3 planning allowance is not a spending cap. Stopped instances
retain their disks and the two empty secret containers. The prior proposal's disk
and secret planning baseline is about USD 2.14/month before other storage/requests
and tax; this is not a measured bill. No automatic cleanup was created.

## Next boundary

Prepare a separate receipt-only recovery proposal using the corrected installer,
a newly hashed kit and a fresh delivery/evidence identity. Preserve this failed
attempt and the successful source installation. Before any restart, freeze the
exact owned host, new interval, stop controls, request bytes and remaining cost
scope; the original one-attempt approval does not authorize retry/repair. In
particular, do not reuse the old scheduled stop for a later run or assume guest
timers will remain armed after a restart.

After successful receipt preparation, the separate runtime qualification still
requires concrete source/server identities, secret versions, trust/configuration,
physical runtime roots, authenticated receipt roundtrips, independent retention
readback and fault/capacity tests. Then comes an approved future live-market pilot
with simulated money, followed by the requested whole Phase 1–6 integration.
No pilot has started. No real-money trading or H-025 work occurred.


## Dedicated final review

An independent read-only reviewer verified all 32 exported files, all 28 stage
log hashes, the two qualifying advancing Chrony references, and all ten native
samples over 9.002613567 seconds with 1–2 ms reported bounds. The reviewer also
traversed all 200 operator events and checked the separate stopped-state/storage
readbacks. No new blocker in this outcome report was found. This accepts the
accuracy of the bounded diagnostic report, not runtime or pair qualification.
The review is retained as `independent-final-review.json` in the operation root.
