# Phase 6 — Paper Trading / Shadow Execution: planning specification v1

Status: proposed work breakdown and acceptance contract; no runtime implementation
or prospective activation. Date: 2026-10-07. Baseline: main merge
b683163c884e661843138ca8094d7585319f9186 (tree b52eae945989b14e09af6ecb1288d630b508a73b).

## Origin and purpose

The ChatGPT conversation [MiE gjenstående arbeid](https://chatgpt.com/c/6ac4cfa3-66e0-83eb-a216-2e6bdc75cdee)
proposed Phase 6 as Paper Trading / Shadow Execution, followed by execution/risk,
a controlled live pilot, then production. That conversation is planning context,
not evidence that any strategy passed validation. The user's instruction to proceed
authorizes this specification. AGENTS and ADR-MIE-001 remain controlling contracts.

Goal: observe newly arriving market data and execute a reproducible, governed
simulation without real capital. Distinguish actual observations from modeled
orders/fills. Existing Phase 5 and session modules remain authorities within their
versioned scope; do not create another independent backtester or live broker.

## Entry criteria and scientific boundaries

Engineering design and synthetic development can proceed on the merged, tested
Phase 5 implementation. The original scientific acceptance is still UNRESOLVED.
Pilot003 is a technical integration fixture, not an eligible validated strategy:
its retrospective 2025 return failed its threshold. No H-025 source/artifact is
approved for use. Incomplete H-013/H-026 family history remains disclosed; do not
create a supposedly clean family or zero-count history by renaming a strategy.

Two explicitly different future modes must not share an approval status:

| Mode | Entry evidence | Permitted claim |
|---|---|---|
| Engineering diagnostic | Separate frozen non-H-025 fixture contract; declared non-inferential scope | Technical behavior only; no strategic validation or promotion |
| Validated-strategy forward study | Independently verified scientific acceptance under its applicable protocol, complete required trial provenance and explicit prospective contract | Prospective simulated observations within that protocol; no live-capital approval |

No validated strategy is designated by this specification. Diagnostic eligibility
must never imply scientific eligibility. A failed strategy is not tuned or replaced
inside the same experiment to pass a software gate. No real-data pilot is started.

## What exists and what must be established

| Existing component | Reuse boundary | Missing prospective requirement |
|---|---|---|
| app.paper.session engine/observed_engine_v1 | Deterministic execution reference; preserved schemas/results | Actual arrival clock, continuous state and closed-segment integration |
| app.research.observed_session | Bound callbacks and virtual timing receipts | Real arrival/decision chronology; virtual timestamps are not wall-clock proof |
| Phase 5 identity/registry/session trial | Registration, reservations, outcomes and read-only verification | Versioned prospective segment identities and explicit reservation/count policy |
| Session manifest/journal/report | Hash-bound immutable evidence primitives | Pre-execution persistence, cross-segment checkpoints and independent receipts |
| Shadow admission/forward gates | Existing versioned eligibility and reporting | Reviewed mapping to new verified prospective evidence; no automatic status transfer |

A finite replay is not a continuous service. Concatenating independent windows
with fresh capital is not a continuous portfolio. Legacy terminal liquidation and
pending-order cancellation must not silently occur at every segment boundary.

## Ordered work packages

### WP6-0 — freeze architecture and interface contracts (first package)

Documentation only. Before implementation, specify a new ADR/version for any new
stateful coordinator, durable store or execution semantics. Define exact schemas,
closed reason-code/status vocabularies and test vectors for:

- Immutable segment canonicalization and provider/asset/calendar/timeframe binding.
- Actual receipt time, provider event time and virtual replay time as separate fields.
- Session-manifest persistence before evaluation and identity linkage to Phase 5.
- Strategy-state, position/cash/cost and pending-order checkpoint boundaries.
- Trial reservation timing, segment sequencing and counting; a segment is not
  automatically a new strategy trial, and an outcome cannot be erased by retry.
- Exclusive storage ownership, transaction boundaries, partial failures and recovery.
- Independent checkpoint anchoring and controlled command-line entry point.

Acceptance: every authority and state transition has an owner and refusal case;
no unresolved identity circularity or implicit filesystem creation; independent
review of contracts. Output is reviewable specifications, not executable evidence.
No specific new schema becomes frozen merely by appearing in this planning document.

### WP6-1 — closed-segment and receipt validation

Implement pure validators and synthetic fixtures against approved WP6-0 contracts.
Define UTC half-open boundaries, calendar, expected coverage, data finality,
canonical numeric representation and source identity. Specify duplicate, revision,
late-arrival, missing-bar and provider-outage behavior before seeing run results.
Arrival receipts must be recorded when observed, never reconstructed from OHLCV.
Reject future/unclosed bars, invalid prices, wrong provider/asset, sequence conflicts
and invalid receipt bindings. Freeze clock-skew tolerances; never clamp timestamps
into apparent causal order. Corrected data receives new identity/linkage under an
approved revision policy; original evidence is preserved.

Acceptance: independent canonicalization/hash vectors, adversarial boundary tests,
no content reads or output writes in pure validators, and deterministic replay of
identical inputs. Provider adapters/network access are not required in this package.

### WP6-2 — governed segment coordinator and state continuity

Implement only after the stateful ADR and contracts are approved. Reuse versioned
session/Phase 5 primitives; specify equivalence and intentional differences from
legacy replay. Freeze plan before intake/evaluation, close/hash segment, bind and
persist manifest, execute, verify, anchor receipt, then allow the next segment.
No future-data callback or post-result parameter change. Reject duplicate, missing,
out-of-order or substituted segments. Next segment must bind the exact verified
checkpoint, including positions, cash, accrued costs, pending decisions and strategy
warmup state. Warmup and scoring coverage must be separately identified.

Acceptance: synthetic multi-segment runs reconcile to the declared continuous
reference where equivalent; no capital reset, double cost, lost pending order,
reused fill, look-ahead, or fabricated observation. A behavior change needs a new
method/version, not an in-place change to frozen engines.

### WP6-3 — interruption, recovery and evidence verification

Define and test faults before/after every durable transition. Unknown commit or
execution outcomes must stop for reconciliation, never execute the same action
again speculatively. Recovery verifies persisted evidence against independently
bound receipts before any new action; it does not repair or invent missing events.
Non-resumable stages remain non-resumable. A hash chain alone does not prove that
the last events or the whole local tree were not replaced.

Acceptance: synthetic fault injection, isolated process-crash tests only under a
reviewed test contract, idempotent verification with no source-data re-evaluation,
and explicit power-loss limitations. No claim of power-loss durability from mocks
or ordinary process termination. No fault injection against old evidence roots.

### WP6-4 — controlled CLI and operational reporting

Commands must separate inspect/validate/verify from intake/start/stop. No default
source-tree state directory, implicit pilot, secret logging, account-order endpoint
or unattended schedule. Read-only market adapters must not acquire order authority.
Publish health, staleness, missing/late data, clock skew, processing delay, exceptions
and checkpoint state. Reports identify the exact source, method, plan and evidence.

Acceptance: an operator can trace each reported fact to a persisted verified
artifact; failures are visible; stop behavior and any pending simulation state are
explicit. Observe signal times/quotes where actually collected. Fees/slippage/fills
remain modeled unless independently observed at the required resolution. Daily
OHLCV cannot establish intrabar queue position, spread or actual execution quality.
No brokerage fill or profitability claim from simulated fills.

### WP6-5 — separately approved prospective simulation pilot

Only after WP6-0..4 pass dedicated review and applicable tests. Prepare a concrete
contract before approval: mode/fixture or scientifically eligible strategy,
provider, assets, timeframe, interval, receipt/clock policy, execution/cost/risk
rules, warmup, segment/state behavior, frozen thresholds, failure/recovery policy,
owner, exact evidence root, independent anchor destination and resource limits.
Start and end dates must be future at authorization; no backdating/replay substitute.
Technical diagnostics and scientific experiments have distinct outcome authority.

Real provider access, exact root creation and pilot activation require the concrete
approval described in AGENTS/ADR. No root or runtime store is created by this plan.
Provider/account access, paid services and scheduling are not implicitly authorized.

Acceptance: an approved non-H-025 prospective run completes the declared path and
independent read-only verification; all negative/partial results remain reported.
Software success is not contingent on positive P&L. Scientific success is decided
only by its own frozen protocol. A short technical pilot does not establish
statistical power, strategy validity or eligibility for live trading.

## Phase completion criteria

| Criterion | Current status |
|---|---|
| WP6-0 architecture/contracts independently reviewed and approved | PLANNED |
| WP6-1..4 implemented and tested, with declared runtime support | NOT IMPLEMENTED |
| Full regression plus dedicated timing/storage/identity/recovery review | NOT EXECUTED for Phase 6 |
| Explicit owner/root, authentic receipt chain and independent anchor | NOT SELECTED |
| Authorized prospective non-H-025 simulation and independent verification | NOT EXECUTED |
| Reproducible environment, operator instructions and recorded limitations | PLANNED |

Only when the applicable rows have actual supporting artifacts may the technical
prospective path be called verified. Scientific eligibility remains a separate
status. Phase 7 development or live activation is not automatically authorized by
Phase 6 technical completion. Phase 8 capital use requires its own explicit decision.

## Immediate next deliverable and deferred choices

Next is WP6-0: a concrete architecture/contract package with ownership, transition
and fault tables and acceptance tests. Choose the data provider/timeframe and physical
storage only when preparing that concrete run; no user preference is needed to
write the documentation or synthetic fixtures. Do not request pilot approval until
the complete reviewable package exists. Review this planning specification first.
