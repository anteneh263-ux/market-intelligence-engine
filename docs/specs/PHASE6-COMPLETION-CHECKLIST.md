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

## Remaining acceptance-critical work

1. Select the actual independent service account/region and review a concrete
   priced deployment. No account/endpoint/key or spending authority exists yet.
   User was asked whether an AWS account exists; no answer recorded in this file.
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
No free-tier eligibility or hard spending cap is assumed.

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
