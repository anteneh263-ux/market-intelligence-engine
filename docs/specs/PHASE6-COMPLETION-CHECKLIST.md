# Phase 6 completion checklist — 2026-10-16

Status: INCOMPLETE. This is the current delivery checklist, superseding old
remaining-work snapshots in the chronological implementation log. It does not
change the WP0 acceptance contract or create new runtime authority.

## Implemented and tested locally

Versioned P/A/R/G/K, diagnostic strategy and continuous simulation reducer;
descriptor-bound durable store/registry; intake/state/receipt/checkpoint/signature
validators; actual bound strategy, bar and quote call sites; mandatory explicit
genesis/continuation factories; durable supplied checkpoint acknowledgement; local
close-before-open handoff. Legacy constructors are deliberately not globally gated.
Source normalization now handles a selected Binance BTCUSDT subset; it is not a
live connector and does not authenticate provider data.

Receipt append preflight validates request identity and commitments. A separate
S3 retention adapter is implemented and tested with simulated clients: signed
receipt verification, one conditional PUT attempt, exact-version readback and
compliance-retention inspection. This is not AWS integration testing or a durable
assembled service; see ADR-MIE-005. A DynamoDB conditional pending/head adapter
now implements single-attempt claim/commit with simulated-client fault tests
(ADR-MIE-006). The local ReceiptAppendAttempt now wires signing, pending claim, actual S3
adapter return and conditional commit, with failure/clock checks and simulated
client integration tests (SPEC-phase6-receipt-append-v1). There is still no
authenticated endpoint or actual AWS integration run.
Exact-version read-only S3 inspection is also implemented and tested locally,
including reading the append adapter output. It does not establish committed-head
authority or signature validity by itself. A local challenged-head assembly now
reads committed state consistently twice, inspects every supplied retained version,
validates the full signature chain and signs the nonce-bound head. Version references may be supplied to the lower-level head helper; the indexed
service path resolves them from the persisted index. A separate immutable DynamoDB index
adapter now supports conditional publication and exact-request lookup (ADR-MIE-007),
and is now wired into append before head commit and into indexed challenged-head
lookup. Index hits alone remain unconfirmed until
committed-state, signature and retention checks pass. Real
transport authentication, trusted clocks and AWS behavior remain unverified.
DynamoDB is an additional proposed cost/permission item within the same AWS account.

Original-receipt read-only lookup now verifies the indexed committed chain and
returns the original signed receipt with a fresh challenged head. A strict Function
URL event boundary and actual service binding are locally implemented and tested.
The binding checks verified predecessor identity, duplicate subjects and clock
ordering before successor writes. The event boundary relies on trusted AWS IAM
events; it cannot authenticate arbitrary JSON without a correctly deployed URL
and invocation policy. These are local integration results, not cloud acceptance.

## Remaining acceptance-critical work

1. AWS account MIE now exists; console inspection confirmed one root passkey,
   Free plan and $100 credits on 2026-10-16. Stockholm is proposed, not a frozen
   deployment configuration. Complete the region-specific priced deployment,
   exact bucket/endpoint/trust and permission review before resource creation.
   No resource/spending activation or paid-plan upgrade is authorized.
2. Implement/review service persistence transaction, signing and authenticated
   client append/lookup/challenged head. Pin public trust and exact retention.
   Unknown append cannot silently retry or reuse sequence; recover only under
   the contract's read-only lookup and still-live-process rules.
3. Select and implement actual clock-source policy, measurable offset bound and
   monotonic/process/nonce ownership. Supplied signed times and manually entered
   probes are insufficient. Connect real receive-clock capture to source data.
4. Implement bounded live collection and durable raw→normalized→receipt binding
   in the actual gate. Prove gaps/revisions/first eligible quotes and failures stop.
   Verify provider accessibility/terms only for the selected actual endpoint.
5. Complete operator orchestration/CLI and read-only report/verification, connecting
   initial reservation/start attempt, feeds, callbacks, anchor acknowledgement,
   handoff and stop under one owner. No helper/test fixture counts as this CLI.
6. Verify capacity before a30-minute pilot. Existing intake/proof interfaces have
   bounded snapshots,64receipt/map limits and10000-value budgets in places. A
   continuously retained1Hz quote stream over30minutes can exceed these limits.
   Do not silently discard observed quotes, truncate proof history or change the
   pilot duration to hide this. Review scalable retention/proof composition and
   test the actual agreed event-rate/window before readiness.
7. Run assembled-path fault/uncertainty tests and independent review; freeze code,
   dependencies, source/clock/service contract and exact runtime configuration.
8. Present a complete pilot approval package: exact provider/service/costs, owner,
   separate evidence/registry roots, immutable diagnostic parameters, future
   interval and one-attempt failure handling. Then obtain explicit approval.
9. Execute that approved prospective simulation, inspect independently retained
   receipts and report technical outcome and limitations. A failed/incomplete
   attempt is preserved; no automatic replacement run or scientific promotion.

Completion requires9, not only software tests. After Phase6 acceptance, run the
separately requested entire Phase1–6 integration and trace data/identities/results/
evidence across it. Neither stage establishes profitability or live-money readiness.
H025 and all historical evidence remain excluded.

## Candidate external setup, still not activation authority

The existing separate-host AWS proposal remains a candidate. A cost-conscious
service may use an authenticated Lambda Function URL, private versioned S3 with
30-day ObjectLock compliance, and a service-only signing secret. The transaction
protocol/region/identity policy is not yet implemented or priced for deployment.
Free-plan eligibility and $100 credits were observed in the console; exact feature
availability and retention horizon still require verification. No application-level
hard spending cap is established.

Reference pricing checked2026-10-16: Lambda lists$0.20/million requests, compute
and storage requests billed separately; SecretsManager lists$0.40/secret-month.
These are reference components, not an all-in region-specific quote. S3 storage,
requests, logs and other selected services must be included after region/account
selection. No budget approval or resource creation is inferred from this checklist.

Sources:
https://aws.amazon.com/lambda/pricing/
https://docs.aws.amazon.com/lambda/latest/dg/furls-http-invoke-decision.html
https://aws.amazon.com/secrets-manager/pricing/
https://aws.amazon.com/s3/pricing/


## 2026-10-17 capacity/intake implementation update

Explicit capacity-v2 is implemented through the local call chain; the default v1
limits remain. A30segment/1831observation accelerated local run retained all supplied
observations and exercised prior-state/receipt binding and simulation checkpoints.
Optimized maximum segment time measured0.881s, derived storage153100891bytes. This
reduces the earlier capacity blocker but does not complete live/admitted capacity
acceptance, peakRAM or worst-supported-frame testing. See SPEC-phase6-capacity-v2.
SourceIntake now persists actual raw→normalized→receipt links and enforces cumulative
raw bytes in its owner (including duplicates and non-final bars), with fault tests.
It is not yet connected to live transport and the complete admitted operator path.
Clock source, deployment/pricing/permissions, cloud tests, frozen package and the
approved prospective pilot remain incomplete. No Phase6 completion claim.


## 2026-10-17 AWS diagnostic and clock adapter update

A narrowly scoped one-task Fargate clock diagnostic was authorized and executed
in Stockholm. Task exited successfully and is STOPPED. Its stack remains,
including a one-day-retention CloudWatch log group; there is no recurring compute.
This supersedes the earlier blanket statement that no resource activation was
authorized, but does not authorize an unreviewed pilot or paid-plan upgrade.
Free plan/100 USD credits were confirmed through the AWS API. Region-specific
component prices were collected; the complete service deployment remains unfrozen.
No receipt bucket, signing secret or authenticated receipt endpoint is deployed.

The diagnostic retained 12 raw metadata samples and their hashes. All advertised
SYNCHRONIZED status and sub-100-ms bounds; these are sampled AWS metadata claims,
not prospective event-time acceptance. A ClockBound 2.0.3 Linux adapter candidate
now has synthetic fault tests. See SPEC-phase6-clockbound-v1. Native deployment,
actual source/service wiring, live integration and pilot acceptance remain open.


## 2026-10-17 native Linux diagnostic update

ClockBound now has actual ARM64Linux build/load/ABI evidence and bounded fault
observations:10 accepted readings with1–2ms uncertainty after2 startup refusals,
and12/12 forced-disruption refusals. See PHASE6-NATIVE-CLOCK-QUALIFICATION for
all preserved attempts and limitations. The stopped-daemon sequence is confounded
by unverified recovery from forced disruption; continuous admitted-owner operation
and production source/service wiring remain unverified. Phase6 is INCOMPLETE.

## 2026-10-17 persistent-owner and isolated-stop update

Separate diagnostic008 passed61 one-owner observations over60.00108348seconds,
with2–3ms bounds, after explicit Chrony stabilization and ClockBound readiness.
It then confirmed daemon termination and same-owner refusal after5.021837131seconds,
followed by two sticky refusals and balanced descriptors. The20 accepted readings
between stop acknowledgement and refusal reflect the native cached-state grace
period; this is not immediate stop detection. Attempts006/007 remain failed and
preserved. See PHASE6-NATIVE-CLOCK-QUALIFICATION for raw evidence and limitations.
The bounded isolated-stop gap is closed; sustained production reliability,
actual source/service integration, deployment freeze and prospective acceptance
remain open. Phase6 is INCOMPLETE.

## 2026-10-17 fixed clock-to-intake binding

ClockBoundSourceIntake now constructs one native clock adapter and one durable
SourceIntake, taking explicit library pins and the plan's offset limit. Its frame
API cannot accept a replacement supplied clock. It persists the native probe
through actual intake calls and permanently stops both owners on admitted failure.
39 focused tests passed and were independently reproduced, including persisted
probe/receipt bindings, same-session continuity, constructor cleanup, reentry and
terminal errors. See SPEC-phase6-clockbound-intake-v1. Tests substitute native FFI
and time; no new native/network integration or AWS activation occurred. Transport,
service-time binding and the complete admitted operator remain unfinished.

## 2026-10-17 bounded transport candidate and service-clock gap

BinanceClockBoundTransport now connects the pinned synchronous WebSocket client
through the actual fixed-clock intake and a synchronous downstream callback.
54 focused tests passed independently, including a real localhost WebSocket path
with synthetic messages/native timing. Original application-message bytes are
persisted; no reconnect follows setup, timeout, intake or callback failure. The
reviewed callback-mutation issue was fixed by capturing the durable commit hash
before invoking downstream code. See SPEC-phase6-binance-transport-v1.

No Binance connection, external receipt acknowledgement, cloud resource or pilot
was activated. The callback is not yet a remote authenticated receipt client.
Existing Function URL/Lambda service code has no qualified ClockBound host;
PHASE6-RECEIPT-CLOCK-DEPLOYMENT-GAP records the specific deployment decision and
required authentication/clock review. Transport buffering/freshness, service
clock/deployment and assembled acceptance remain open. Phase6 is INCOMPLETE.

## 2026-10-17 separate mTLS receipt ingress candidate

Receipt operations are now transport-neutral while the existing IAM Function URL
wrapper remains intact. A separate mTLS socket boundary requires verified peer
certificates and pins each leaf digest to allowed run IDs before invoking append,
lookup or head. 38 tests passed independently, including real local TLS exchanges
through all three existing operations with simulated AWS storage. No fabricated
IAM events, schema migration, cloud deployment or pilot activation occurred.
See ADR-MIE-009 and SPEC-phase6-receipt-mtls-v1. The proposed Linux/SSM/mTLS
configuration still requires the HTTP listener/client, qualified service-clock
binding, exact policies/keys/prices and end-to-end cloud verification.
