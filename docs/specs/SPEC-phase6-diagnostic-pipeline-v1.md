# Sequential diagnostic pipeline candidate

Status 2026-10-17: implemented and locally integration-tested; no prospective
activation or profitability authority. Entry point: `DiagnosticPipeline.run`.

The constructor takes an actual owned source/transport, VerifiedReceiptClient,
bound StartBindingAttempt, immutable diagnostic parameters and engine hash,
explicit segment writers and a separate result writer. It allocates no directories.
The caller must establish approved physical roots, start bindings, actual code
identity, native readiness, trust and an appropriate start/end interval upstream.
A supplied engine hash is not proof of which code was loaded.

The start request is exported from the closed start store only after complete
inventory verification under a descriptor-bound shared lock. The source and client
must share the same owned event-clock capability. Constructor rejection occurs
before claiming the result writer. There is no automatic reservation or retry.

`SourceInventory` verifies the just-acknowledged raw-source commit chain, exact
schemas/scalars, all-packet clock continuity, byte accounting and receipt/payload/
probe bindings. Ignored frames count. Identical bars retain receipts without
creating duplicate segments; revisions or gaps refuse. The inventory uses the
existing acknowledged source store; it is not another durability authority.
Limits are 10,000 packets, 4,096 receipts and the plan's intake-byte allowance.

For each first closed bar, the controller fetches a fresh verified head, opens the
genesis journal or uses the preceding journal's close-before-open successor handoff,
binds intake/prior state/prior receipt ancestry, then calls the fixed diagnostic
strategy. Decision time is taken from its acknowledged return event, not a later
sample. The reducer records an intent and causal barrier. A subsequent eligible
application-observed quote is bound before simulated execution. An unexpected bar
receipt while awaiting that quote remains a terminal refusal under the existing
quote-binding contract. No quote is fabricated to resolve a dead feed.

Checkpoints and predecessor receipt proofs are exported through acknowledged-object
read APIs, verifying descriptor/root/header/inventory, expected length and SHA256.
Exact reducer/strategy state carries to the next segment. A checkpoint advances
only after the external client verifies its signed receipt/head and the journal
binds that exact acknowledgement. No arbitrary path reads assemble production proof.

The transport stops after the callback returns exactly True and acknowledges the
current commit/count. All other callback returns retain the existing behavior.
A bounded transport return is insufficient for completion: all planned segments
must be anchored, there must be no unresolved failure, and source/client closure
and complete local inventory inspections must succeed. The result writer records
`p6.pipeline_result.v1`, closes, and verifies its own result before returning it.
Raw unacknowledged footer bytes alone are not a successful returned result.

Results distinguish COMPLETE_LOCAL, STOPPED_LOCAL and INCOMPLETE. They always
retain runtime_accepted=false and independent_retention_verified=false. Unknown
publication errors survive cleanup errors. Closure or exhausted message/duration
budgets cannot silently convert an incomplete horizon to completion. One instance
is consumed once; no restart, recovery, live-money execution or capital allocation.

## Validation and practical limits

Actual local integration covers registration/start binding, the production
transport loop with supplied synthetic frames, native-clock adapter with substituted
FFI/time, two sequential admitted journals, simulated fills, real local TCP/mTLS,
simulated AWS retention/state/index, four verified anchor subjects and final local
store inspection. Raw duplicate/nonfinal/interstitial quotes are retained.

Integrated failures cover feed disconnect/no quote, first-bar gap, native failure,
late quote, slow decision, storage capacity, prior-store tamper, successor publication,
lost post-commit reply, invalid signature, pending-bar duplicate, message exhaustion,
constructor failure and primary-publication plus secondary-close failure.

An accelerated 10-segment/610-frame integration measured 4.233306 seconds in
callbacks, 0.160753 seconds maximum callback and 23,875,808 derived blob bytes.
This uses a synthetic clock and local network/storage substitutes. It is not a
10-minute live run, target-host capacity qualification, worst-case/RAM bound or
proof that the real five-second decision budget will hold.

Full repository regression after these production changes: 3,781 passed in
120.18 seconds. No failures/skips were reported by that run. Native/network/AWS
qualification, full frozen deployment/CLI packaging, exact pilot approval and
actual prospective evidence remain open. H-025 remains excluded.
