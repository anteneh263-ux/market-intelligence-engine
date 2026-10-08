# Phase 6 external setup proposal — 2026-10-08

Status: proposed, not authorized, not deployed. The operator confirmed no approved
market-data provider or independent receipt service exists. This is a concrete
candidate configuration, not a frozen pilot contract or activation request.
No provider connection, external resource, evidence root or paid account was created.

## Candidate market input

Use Binance public spot market data for BTCUSDT, UTC one-minute closed candles,
and the individual-symbol 24-hour ticker as a sampled bid/ask observation source.
The documented kline payload includes a closed flag; accept only `k.x == true`.
Normalize its inclusive close time T to half-open close_ms=T+1, requiring exactly
60,000ms from k.t. Normalize decimal strings without binary floating-point parsing.
The ticker supplies event time E and bid/ask b/a; retain raw payloads and actual
local receipt clocks. Do not substitute its rolling statistics for completed bars.
This quote stream samples prices; simulated fills do not establish executable
liquidity, queue position or real spread capture. BTCUSDT uses USDT as the quote
unit, not USD; amounts and reports must say USDT throughout.

This source choice is motivated by the explicit closed-bar flag and timestamped
bid/ask payload. It is not a recommendation to open an exchange/trading account.
Use public data only; do not supply trading credentials. Deployment must verify
that the selected public endpoint is accessible under applicable provider terms.
If unavailable, stop and review another adapter; do not silently switch source.

Primary source checked 2026-10-08:
[Binance spot WebSocket documentation](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md).
Provider schema and adapter version must be pinned in the actual pilot package.

## Candidate independent retention service

Deploy a small receipt service in an operator-controlled AWS account, separately
from the Mac: authenticated HTTPS API, a single serialized writer per run, a
signing key held only by the service, and a private S3 versioned bucket with Object
Lock in compliance mode for 30 days. The Mac receives append/read access through
the service only; it receives no bucket deletion, retention administration or
signing-key permissions. Pin the service public key and algorithm in the plan.

S3 retention protects retained object versions; it does not itself supply the
ordered, signed receipts or authoritative head required by ADR-MIE-004. Implement
and test those separately. The service must refuse sequence conflicts, bind the
preceding signed receipt, return the original result for matching idempotency keys,
and expose a read-only current head. It must not acknowledge a receipt before its
retained version is verified. A crash between retention and head publication must
block new appends until read-only reconciliation proves the state; no sequence reuse.
The deployment contract must pin an algorithm, exact transaction protocol and
service identity before implementation is represented as operationally supported.

Primary source checked 2026-10-08:
[AWS S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html).
Compliance retention is deliberately irreversible during its retention period.
Use hashes/receipt metadata only remotely; do not include credentials or raw payloads.
This is separate-host retention, not independence from the account administrator
or protection against a fully compromised signing service. Disclose that trust limit.

Account, region, bucket name, endpoint, key and pricing remain unset. No spend is
authorized. Provide a priced deployment plan and explicit approval before creating
resources; budget alerts are not a guaranteed spending cap. A local second folder,
GitHub commit or unsigned object upload cannot replace this service.

## Proposed diagnostic pilot parameters

- Operator: Anteneh; engineering diagnostic only, not a strategy acceptance study.
- One 30-minute future UTC-aligned interval, chosen only after readiness and approval.
- Two warmup bars, then an immutable alternating 25%/75% target schedule by bar index.
- Simulated starting balance: 1,000 USDT; maximum target 75%; quantity step 0.00001
  BTC; modeled price tick 0.01 USDT. These are simulation discretizations, not
  asserted current exchange order filters.
- Modeled fee 10 basis points each side and slippage 5 basis points; these are
  diagnostic assumptions, not claims about the user's fee tier or observed costs.
- Stop at 10% drawdown; do not liquidate terminal positions automatically.
- Proposed lateness 10s, decision delay 5s, quote timeout 5s and clock offset bound
  100ms. Failure to meet any bound stops the run and preserves partial evidence.
- Maximum local intake 100 MiB. One attempt; failure does not authorize a retry.
- Proposed new local evidence root:
  `/Users/antenehhailu/Desktop/MIE_EVIDENCE/phase6-shadow-diagnostic-001`.
- Proposed separate new registry root:
  `/Users/antenehhailu/Desktop/MIE_EVIDENCE/phase6-diagnostic-registry-001`.
  Neither root has been created. Their exact paths require explicit authorization.
- H-025 and all earlier pilot evidence are excluded and remain unchanged.

A real clock probe implementation must measure and retain a defensible offset bound
against the frozen time-source policy. A successful local time call or a manually
supplied offset is not sufficient. Clock policy remains an implementation prerequisite.

## Readiness and approval boundary

Implemented and independently reviewed: pure structural validators and the continuous
simulation reducer. Full regression: 2,517 tests passed; all Phase 6 inputs synthetic.
These components do not constitute an operational prospective path.

Still required: durable governance/coordinator and exact event payload contracts,
source adapter and authentic clock collection, anchor protocol/service, interruption
and read-only verification, controlled CLI, integrated adversarial tests, and an
independent review of the assembled path. Freeze dependency/code hashes and the
actual future interval only after those checks pass.

After the complete package exists, ask the operator to approve the exact provider,
external service/costs, owner, roots, time window and frozen diagnostic contract.
Approval of this candidate design alone must not start a pilot or create resources.
Technical completion will require the actual authorized run and independently
verified retained evidence. Scientific acceptance remains UNRESOLVED.
