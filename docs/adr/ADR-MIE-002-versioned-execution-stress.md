# ADR-MIE-002: versioned execution stress for historical Phase 5 fragility

Status: design accepted in dedicated review; implemented for synthetic integration.
Date: 2026-10-06. This does not activate production or authorize any real-data run.

## Decision and ownership

Existing historical authority and Phase5 governance under ADR-MIE-001 remain.
app.paper.session.stress_engine_v1 is a separate simulated numeric execution
version, derived from preserved observed_engine_v1. app.research.stress_session
owns the versioned observation adapter; app.research.observed_mc_v2 owns the
preregistered stress schedule and scenario reporting. No live capability is added.

No new storage system: the existing caller-owned explicit empty evidence root,
exclusive fsynced JSON artifacts, hash-chained session journals and trial/holdout
ledgers remain authoritative. Stress roots are predetermined children of that
root, not new implicit defaults. Phase5 trial outcomes and later holdout completion
anchor the existing evidence chain. MC never changes N, DSR, base parameters,
strategy ranking or holdout policy. Post-results strategy variants are new trials.

## Version/equivalence boundary

paper-session-stress-v1 binds paper.stress_policy.v1 in the native manifest.
It is used only by observed plan v3 / MC report v2 / pipeline v3. Existing observed
engine/session v1 and all earlier plans/results remain preserved. No artifact
migration, schema reinterpretation, repair, backdating or legacy cutoff occurs.
Old source pins retain their preserved checkpoint environment. New source hashes
produce new method identities. A separate reviewed PR is required for publication.

Every MC run first requires byte-identical baseline observed-v1 replay, then a
zero-delay/zero-missing stress run with identical order records, cash, holdings,
cumulative costs, equity endpoints and net returns. Cross-version event hashes
are expected to differ. Failure of economic parity stops the run.

## Execution and failure semantics

SINGLE_PENDING permits one outstanding target. Its eligibility is decision bar
open + timeframe*(1+delay_bars). New strategy decisions are suppressed while it
waits, while close marks and risk checks continue. This changes the decision
sequence and is not a transparent switch on an unchanged strategy-decision stream.
A due order is attempted at the first ingested eligible bar. A deterministic
missing draw suppresses the simulator call, records STRESS_MISSED_FILL as the
reason on a zero-quantity/zero-cost rejected order, and leaves cash/holdings unchanged.
It is not described as a real broker rejection. After an attempt a new decision
may be made at that bar's close. Outstanding final decisions remain unexecuted.

A risk breach cancels the waiting strategy decision permanently, with an explicit
SIGNAL_CANCELLED observation tied to the native risk event. It can replace it
with one flatten decision using the same delay and missing rule. No retry of a
safe exit; remaining holdings and RISK_STOPPED are preserved if it is missed,
partial, or not yet due by the end. Such scenarios are not complete evaluations.

BrokerError with unchanged financial state fails closed as DATA_STOPPED, without
retry. Any broker error after financial mutation raises unknown-outcome and
preserves partial files without a manufactured final report. Structural failures
stop the MC package; they never silently reduce its denominator. Ordinary completed
session stops remain failed scenarios in the full schedule. No automatic recovery,
overwrite or re-execution of a consumed holdout is introduced.

## Verification and authorization

Schedule verification rejects early/late or omitted due attempts, strategy
observations while pending, missing risk checks, unsupported cancellation, incorrect
missing draws and altered balances. Native order costs/snapshots are reconciled.
Every grid/patch/repetition/window and seed is fixed before MC performance reads.
Independent review and negative/integration tests are required before release.
This package runs only in explicit temporary synthetic test roots. Existing pilot001
and H025 are untouched. Any new real-data experiment/root needs separate explicit
authorization under AGENTS; no such authorization is supplied by this ADR.
