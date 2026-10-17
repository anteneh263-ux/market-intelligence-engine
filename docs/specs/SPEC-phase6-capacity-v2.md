# Explicit local capacity profile v2

Status: implemented and locally tested; no prospective admission authority.

An explicit capacity="p6.capacity.v2" keyword is carried through intake, prior
state/receipt, checkpoint/start/continuation validators and journal factories.
The default None retains v1 limits and its per-segment validation path. No ambient
context or process-global policy changes are used. The successor inherits its
journal's explicit profile. Existing evidence bytes and schemas are not migrated.

V2 permits 4096 receipt/list/map entries, depth16, one million snapshot values and
16MiB of cumulative ASCII string/key characters per snapshot. The latter is not a
serialized-byte limit; ObjectWriter independently limits published bytes. Segment
and checkpoint counts retain their64 limit. Invalid or unknown profiles refuse.

V2 validates the complete mixed receipt chain and all payload/probe mappings once,
then checks every first bar's segment schema, run, sequence, predecessor hash and
planned interval directly. It does not skip receipts or trust cached validation.
The default v1 path is unchanged. Invalid-input reason precedence may differ in
v2; acceptance conditions were independently reviewed. Numeric strategy/execution
semantics remain unchanged. A frozen future method contract must pin this code and
profile. The journal's capacity_selection object is local configuration evidence;
it is not referenced by anchor-authenticated manifests/checkpoints.

Synthetic measurement on2026-10-17:30segments,1831observations, actual local journal,
strategy, bar/quote calls, prior-state/prior-receipt binding and checkpoints.
Before optimization:70.0196s total,6.2658s maximum segment. After:12.9285s total,
0.88055s maximum segment,153100891bytes of derived blob storage. Test location is
pytest temporary storage. This is an accelerated workload, not a30minute live run.
It excludes authenticated admission/handoff, network/cloud latency and authentic
clock probes. Peak RAM and worst-supported payload sizes remain unmeasured.
No hard operational five-second guarantee follows from this measurement.

Dedicated reviewer independently passed58 optimization/differential tests and
approved equivalence; earlier75 capacity/intake/handoff regressions passed.
No historical artifact, H025, live account or pilot root was modified.
