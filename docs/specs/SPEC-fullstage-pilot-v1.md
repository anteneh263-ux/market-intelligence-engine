# Retrospective full-stage integration pilot 002 — preparation contract v1

## Current execution hold — prior-family lineage discovered

**Pilot002 is NOT eligible for execution approval.** H-013 and H-026 have
related real BTC/USD MA evaluation records. A zero-history owner attestation
cannot erase those records. See PHASE5-PILOT002-LINEAGE-HOLD.md for the bounded
inventory, missing history and enforced guard. The older clean-family proposal
is preserved but superseded. No new real-data evaluation/root was created.

The final runner refuses known, unavailable or missing lineage before any write,
and rebuilding the proposal prevents deletion of the history list as a bypass.
Only synthetic fixtures isolate this metadata; numerical authorities remain real.
The full base regression passed2435 tests before this additional guard. All18
final runner cases passed in the base runtime; the pinned runtime passed17 plus
one targeted anti-erasure case. Dedicated review approved the history guard.
Reconciliation remains required before the execution procedure below can apply.

Status: runner implemented; real-data execution and evidence-root creation **not
authorized or performed**. This is a separate proposed experiment from completed
pilot001. It uses existing registry v2, observed WF v3, plan/pipeline v3, MC v2,
and holdout authorities; it introduces no new engine or durable store. AGENTS,
ADR-MIE-001 and ADR-MIE-002 govern execution and evidence. Scientific changes must
remain separately reviewed and go through a separate PR before release.

## Frozen experiment

Owner: Anteneh Hailu. Proposed physical root:
`/Users/antenehhailu/Desktop/MIE_EVIDENCE/phase5-fullstage-pilot-002`.
Preparation writes only the reviewable proposal outside this root. The runner
requires the caller to supply an existing, empty, absolute nonsymlink root.
No resume, retry, repair, migration, automatic directory selection or tuning.
Unknown outcomes preserve partial evidence; do not produce a success report.

Input: repository `data_csv/BTCUSD_1d.csv`, SHA-256
`d3085c72f4d0e0b646a050756fa3f3252e1befe214910417999b70bcfc744841`.
Repository provenance is not independently certified. The entire CSV has already
been read for sealing in pilot001 and is parsed again for hashes here. The 2025
segment is therefore a **retrospective integration holdout**, never claimed to
be pristine, unseen or prospective scientific evidence.

Hypothesis family: BTC daily close above its trailing arithmetic mean gates
long-only equity exposure. This family includes ALL lookbacks, exposures,
cost/risk/window variants, implementations, earlier trials and reruns of that
hypothesis. Unconditional exposure pilot001 is a related engineering precedent
outside this signal family; new names, root, date or code version alone do not
establish a new family. Scope hashes omit those operational identifiers.

The owner must attest no prior performance evaluations in the defined family,
including other tools and unsuccessful trials. Unknown or nonzero history blocks
this fresh-registry runner pending explicit reconciliation. The attestation is
trusted operator input, not independent discovery or proof of independent trials.
After any run in this family, zero-history authorization must not be reused for a
new root. This runner is not a global cross-root trial-discovery system.

Both variants are frozen together: target 25% first, then designated target 75%.
Lookback is 20 completed closes including the current close. Equality and the first
19 bars are flat. Execution uses existing observed next-open timing. Every window
and holdout starts fresh with no cross-window warmup. The selector does no ranking;
75% is designated before any results. First invalid trial still counts. A valid
first WF run contributes its actual registered artifact to the second's statistics.

TRAIN identity context: 2018-01-01–2024-01-01; VAL: calendar 2024;
HOLDOUT: calendar 2025. All intervals are UTC and end-exclusive.
All four actual training and OOS windows are inside VAL:

| Window | Train | Test |
|---|---|---|
| 0 | 2024-01-01–2024-03-31 | 2024-03-31–2024-05-30 |
| 1 | 2024-03-01–2024-05-30 | 2024-05-30–2024-07-29 |
| 2 | 2024-04-30–2024-07-29 | 2024-07-29–2024-09-27 |
| 3 | 2024-06-29–2024-09-27 | 2024-09-27–2024-11-26 |

Each train has 90 daily bars, each test 60; step 60, no overlap between tests.
Returns retain FIRST_CLOSE baseline and fresh capital 100000 per window; no
compounding independent windows into one continuous portfolio. Regime comes from
training close return with frozen ±1% thresholds. Fixed BTC spot execution profile,
absolute simulation risk limits 90%; the separate holdout drawdown criterion is 30%.

Required CPCV uses the checked-in macOS Python 3.14 runtime pin: 3 folds, 2 test
folds, purge 1, embargo 0. Geometry/diagnostics only, as R3 WP5-4 requires. Actual
runtime pin must match before any evidence write or counted performance evaluation.

DSR threshold remains 0.95. Failure ends this authorized attempt, with downstream
stages NOT_EVALUATED. It is not completion of full-stage traversal. No replacement
strategy, relaxed threshold, revised window or new exposure after results.

MC seed 20261006; 8 scenarios: 2 cost profiles × 2 patches × 2 repetitions,
each across all 4 windows. Fee/slippage/half-spread in bps: (10,5,1), (20,10,2).
Patches: unchanged and lookback 30. Delay 1 bar, missing-fill probability 0.1,
analytical SHUFFLE_EXPOSURE_BLOCKS. Survival: return ≥0, drawdown ≤0.3,
at least 75% surviving. These are full-grid sensitivity checks, not new parameter
selection or promotion. SINGLE_PENDING semantics and analytical reorder limitations
from ADR-MIE-002 apply. Holdout retains lookback 20 and designated 75% exposure.
Valid MC downgrade may proceed and remains in the final readiness result.

Holdout: last WF-selected parameters; net total return ≥0 and drawdown ≤0.3.
Access is consumed durably before its evaluation callback. Failed holdout is a
completed failed evaluation, not permission to retry. No promotion is authorized.

RAG is pinned lexical retrieval from one explicit method note, not an external
research review or LLM gate. Corpus text and hashes are in the proposal.

## Authorization and reproduction

`python -m app.research.fullstage_pilot prepare --source ... --sha256 ...
--evidence-root ... --owner ... --output ...` only seals the proposal; output must
not already exist. Source and runner/parser pins plus pipeline implementation pins
are recorded. No evaluator is called during preparation.

After explicit approval of the exact proposal hash, owner, new root, retrospective
holdout and family-history attestation, record the actual user response reference
in an authorization JSON. Required schema is
`wp5.fullstage_pilot_authorization.v1`; exact keys are documented in `_authorize`.
Do not invent or backdate approval. This is an operator-recorded approval, not a
cryptographic signature. The runner verifies its bindings before runtime/data
preflight and refuses a changed fixed design even if a new hash is supplied.

Run with the pinned runtime using `python -m app.research.fullstage_pilot run
--contract ... --authorization ...`. The PLAN and authorization are persisted
before registry initialization and before the first reservation/evaluation.
Completed pipelines are independently reread through `verify_observed_pipeline`;
STOP summaries report the persisted stop and are **not** labeled fully verified.

## Acceptance trace

| Requirement | Production call | Persisted result |
|---|---|---|
| Registered/countable actual attempts | `run_observed_walkforward_trial` → registry v2 | registry/anchor, RESERVATION, OUTCOME, TRIAL_RESULT |
| Fixed WF windows, causal selection/timing, gross/net/regimes | observed WF v3 → native observed sessions | predecessor/ and pipeline/wf/ window reports, native journal, timing receipts |
| Current-ledger N and DSR | observed pipeline → `_statistics` | pipeline/STATISTICS.json; gate STOP or continuation |
| Required pinned CPCV | observed pipeline → `_cpcv` | pipeline/CPCV.json |
| Native delay/missing/cost sensitivity and complete MC grid | observed pipeline → observed MC v2 | pipeline/mc/SCHEDULE.json and reports/sessions |
| Single-use holdout after verified prerequisites | observed holdout + holdout access store | PREHOLDOUT_VERIFICATION, access ledger, holdout execution receipt |
| Retrieval provenance, advisory only | `retrieve_advisory` | pipeline/ADVISORY.json |
| Bounded final status | pilot runner + full pipeline verifier when completed | REPORT.json, child REPORT.json; no automatic promotion |

New runner tests exercise preparation, causal signal, changed approval/source/runtime,
nonempty-root refusal, stable family identity, actual synthetic full chain and actual
statistics STOP paths. Real-data acceptance is still pending approval and execution.
Pilot001 and H-025 are not modified. Separate PR/release remains pending.
