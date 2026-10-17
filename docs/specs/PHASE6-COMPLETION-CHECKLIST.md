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
