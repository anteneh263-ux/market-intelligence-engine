# ADR-MIE-003: separate technical diagnostic integration

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

Status: implemented, independently reviewed and synthetically verified.
No real-data execution authorized yet. Date: 2026-10-06.
User selected the recommended technical integration scope with “forsett” after the
explicit technical-versus-scientific scope question. This does not authorize a new
real-data root or run; those require approval of the concrete frozen proposal.

## Decision and authority

`app.research.diagnostic_pilot` owns a new `wp5.technical_diagnostic.v1` orchestration
and envelope. It tests actual native components while family-history inference
remains UNRESOLVED. Existing `fullstage_pilot`, `observed_pipeline`, the known-history
guard, all numerical engines and registry/holdout implementations remain unchanged.
This is not a relaxed version of the inferential acceptance pipeline.

No new store or execution engine is introduced. The owner supplies an approved
empty absolute root and existing exclusive JSON/SQLite/anchor components operate
only within it. Proposed owner is Anteneh Hailu (`antenehhailu`); proposed root is
`/Users/antenehhailu/Desktop/MIE_EVIDENCE/phase5-diagnostic-pilot-003`.
No migration, repair, backdating, legacy cutoff or historical status transfer occurs.

The hypothesis-family and strategy-family definitions are retained. A different
evaluation objective, `TECHNICAL_INTEGRATION_FIXTURE_ONLY_V1`, explicitly identifies
a local diagnostic fixture registry. It does not claim a new clean research family.
Known related lineage remains attached. Local reservations are reported separately
from `complete_family_N=null`. The observed statistical component may return a local
DSR and PASS/FAIL; neither controls technical acceptance nor grants family inference.
Mathematically undefined statistics remain visible and are not replaced with invented values. Statistics ledger/vector binding must be VERIFIED; integrity-invalid statistics stop.

## Version and stage separation

The diagnostic uses distinct contract, authorization, context, structural proof,
execution-receipt and final-report schemas. The existing observed plan v3 is a
**component configuration**, not a claim that its full inferential pipeline ran.
The diagnostic invokes WF, CPCV, MC, statistics and single-use primitives directly.
It never calls the inferential pipeline with a skipped gate or changes its threshold.
The diagnostic selection-rule string is method-bound and the objective is identity-bound.

The access step is explicitly a retrospective single-use **protocol probe**, not a
scientifically validated holdout. Existing primitive `HOLDOUT_PASSED/FAILED` outputs
remain unmodified as raw component verdicts. The envelope always denies scientific
holdout validation, complete-family inference, prospective validity and promotion.
Its verifier reconstructs the expected report and rejects resealed contrary claims.
The inferential pipeline verifier must not accept this diagnostic envelope.

## Structural prerequisites and failure handling

Before access, independently verify both actual WF trials and timing/native evidence,
the fixed designated 75% parameters, pinned CPCV result, current local ledger and
statistics artifact, full MC artifact set, RAG provenance and frozen contract/source.
DSR failure or MC downgrade does not substitute for a scientific gate result: the
scientific gate remains unresolved outside this diagnostic. Structural/timing/hash
failure or incomplete/invalid designated WF execution stops the diagnostic.
MC reports must be authentic and structurally VALID, including all incomplete or
failed scenarios in the report; their downgrade remains visible.

Access consumption is durable before the data callback. Exceptions retain consumed
or partial evidence. There is no resume/retry or fallback to a better-performing
exposure. A new root is not an automatic retry authorization. Unknown commit outcomes
propagate; no invented completed report is produced. Component access evidence and
the current structural proof are checked before a VERIFIED report is published.

Read-only verification reconstructs component evidence and current local statistics
without reopening market data or adding reservations. This verifies the declared
technical chain, never profitability or original Phase5 scientific acceptance.

## Publication and approval boundary

Prepare the executable package and synthetic evidence before requesting approval
of pilot003's exact hash, owner, root, retrospective access and unresolved history.
The current user selection authorizes this preparation only. Separate reviewed PR
and release remain required; no commit/push/merge/deployment is performed here.
