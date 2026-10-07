# Technical diagnostic pilot003

## 2026-10-06 — approved diagnostic003 completed

The user explicitly approved the concrete pilot003 run and evidence root with
“yes fikse det som trenger å fikses og fullføre fase 5”. Authorization is captured
in the immutable run AUTHORIZATION.json, with capture time identified as such.
The single real-data simulated run completed and its built-in read-only verifier
passed. Technical status: VERIFIED. No production source was changed for this run.

Root: `/Users/antenehhailu/Desktop/MIE_EVIDENCE/phase5-diagnostic-pilot-003`.
Contract: `3b2df2c2a7165b7b0682ff147b53bd88cf4c62f9cda27a2b7ef1da09a9560c48`.
Report: `a6d12aa8dfba24e57146923d0f4a9fa25de968dccb133393a852107682c4c950`.

Both local WF reservations completed; actual pinned CPCV and native MC executed.
MC component readiness is PRESERVED. The retrospective2025 access probe completed
once and returned HOLDOUT_FAILED: total return -0.14295266079033742,
maximum drawdown0.19367181994030536. The nonnegative-return criterion failed;
the30% drawdown limit passed. This is an authentic adverse component result,
not an implementation defect. No retry, tuning, repair or replacement was done.

The agreed bounded technical diagnostic is complete. Original scientific Phase5
acceptance remains UNRESOLVED: complete_family_N=null, scientific_holdout_validated=false,
prospective_verified=false, promotion_authorized=false. Missing authoritative
historical attempt counts/return artifacts cannot be manufactured. Pilot002 remains
HOLD. No release/merge/deployment is claimed; separate PR/release remains outstanding.
The2451-pass regression and14-pass actual-CPCV suite apply to the unchanged source.
See PHASE5-TECHNICAL-CLOSURE.md for the final evidence trace.

Earlier preparation/approval statements below are historical and superseded by
this dated update. Do not request approval again or rerun the consumed pilot.

Authority: ADR-MIE-003; schema `wp5.technical_diagnostic.v1`.
Status: preparation and synthetic verification; no real-data run or new root yet.

## Frozen proposed run

Owner: Anteneh Hailu, ID `antenehhailu`.
Root: `/Users/antenehhailu/Desktop/MIE_EVIDENCE/phase5-diagnostic-pilot-003`.
Source: repository BTC/USD daily CSV, raw SHA256
`d3085c72f4d0e0b646a050756fa3f3252e1befe214910417999b70bcfc744841`.
Source provenance is not independently certified; the entire CSV was previously
read for sealing. No unseen-data or prospective claim is made.

This keeps the previously prepared MA20 signal, current completed close included,
first19 bars/equality flat; fixed 25% then predesignated75% exposure. Each window
starts with fresh100000 capital/state, no warmup. No parameter ranking or tuning.
Known H-013/H-026 lineage remains in the contract; it is not counted as zero.

UTC end-exclusive windows:

| Window | Train | Test |
|---|---|---|
| 0 | 2024-01-01–2024-03-31 | 2024-03-31–2024-05-30 |
| 1 | 2024-03-01–2024-05-30 | 2024-05-30–2024-07-29 |
| 2 | 2024-04-30–2024-07-29 | 2024-07-29–2024-09-27 |
| 3 | 2024-06-29–2024-09-27 | 2024-09-27–2024-11-26 |

Access-protocol probe covers calendar2025, with the final frozen75%/lookback20
parameters. Native BTC spot costs10/5/1 bps fee/slippage/half-spread, absolute
simulation risk limits90%. These are simulator inputs, not capital-allocation advice.

Required actual pinned CPCV:3 folds,2 test folds, purge1, embargo0. Geometry and
diagnostics only. MC: seed20261006,2 cost profiles(10/5/1 and20/10/2),2 patches
(unchanged,lookback30),2 repetitions,8 scenarios across all4 windows;1-bar delay,
10% deterministic missing fills, analytical exposure-block reorder. All scenarios
remain reported. Survival diagnostics use return≥0, drawdown≤30%, surviving≥75%.
The access component records return≥0/drawdown≤30% verdicts, solely as component
outputs. No favorable verdict can grant scientific acceptance.

## Technical completion

The sequence is frozen contract/authorization → local stores → CPCV and advisory
→ actual25% WF → actual75% WF → current local-statistics diagnostic → full native
MC → structural proof → consumed-before-read access probe → independent verification.
Statistics are not an inferential access gate in this explicitly non-inferential
orchestration. The original inferential gate remains unchanged and unresolved.

Technical VERIFIED requires the declared chain to execute and verify. Structural
failures stop. Flat/invalid WF stops before MC/probe. Mathematically undefined statistics remain
explicitly invalid; ledger/vector binding failures stop before MC. Valid MC downgrade and component access failure remain visible
and do not become scientific approval. No retries, repair, alternative exposure,
post-results edits or promotional transition are supplied.

## Artifact trace

| Component call | Artifact |
|---|---|
| Pure `prepare_contract` | Reviewable external proposal; no evaluator/root creation |
| Exact-hash `preflight` | Contract/source/code/runtime checks before writes |
| Existing trial registry + observed WFv3 | Local registry/anchor, predecessor/ and wf/ receipts/journals |
| Actual pinned CPCV and lexical advisory | CPCV.json, ADVISORY.json |
| Existing statistics component, current local ledger | LOCAL_STATISTICS.json, no complete-family claim |
| Existing observed MCv2 | mc/SCHEDULE.json and complete reports/native evidence |
| `_proof` | Diagnostic PREHOLDOUT_VERIFICATION.json with current structural bindings |
| Existing single-use/observed execution primitives | holdout-access/ ledger, diagnostic receipt, ACCESS_COMPONENT.json |
| `verify_diagnostic` | Derived diagnostic REPORT.json; contrary resealed claims rejected |

Primitive artifacts preserve their original schemas and meanings. The diagnostic
envelope, objective, bound selection rule and authorization define their limited
use; they cannot be presented as a full-family inferential run. Each full component
report remains available beneath the envelope for review.

## Commands and approval

`python -m app.research.diagnostic_pilot prepare --source ... --sha256 ...
--evidence-root ... --owner ... --output ...` seals inputs only.

After explicit concrete run approval, record the actual user-message reference
and timestamp in `wp5.technical_diagnostic_authorization.v1`. Fields bind the exact
proposal hash, root, owner, technical-only scope, unresolved-history acknowledgement
and retrospective-access-probe acknowledgement. No zero-history attestation exists.
The caller explicitly creates the approved empty root, then uses `run --contract ...
--authorization ...` under the pinned runtime. Never invent/backdate authorization.

`verify --root ... --contract-hash ... --report-hash ...` performs read-only replay
of a completed diagnostic, without market-data reads or count increments. STOP or
partial evidence is not reported as a completed verified chain.

H-025, completed pilot001 and blocked pilot002 remain unchanged. This diagnostic
does not complete the original scientific acceptance criterion or authorize release.
