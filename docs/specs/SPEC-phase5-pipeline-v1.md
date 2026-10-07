# Governed Phase5 historical pipeline v1

New callable `app.research.phase5_pipeline.run_phase5_pipeline`, code/synthetic
integration only. Historical continuous-bar profile, Form A statistics. No CLI
rerouting, production activation, automatic promotion or prospective execution.
No H025 data/artifacts. Session-calendar/sparse coverage refuses explicitly;
there is no fabricated coverage verification for unsupported profiles.

## Method and data identity

Existing walkforward policy remains byte-identical for existing callers. New
profile additionally requires validation_plan_hash; walkforward_method_hash
includes it, so the WP0 experiment ID binds every downstream policy before data.
The new policy profile also persists actual decision_positions and the full
frozen parameter artifact hash per OOS window.
Legacy evaluation matrices and engine source are unchanged.

Dataset format `wp5.dataset_manifest.v1`: timeframe_ms, complete ordered rows of
sample_id/open_time/close_time/feature_time_ms/label_start_ms/label_end_ms/row_hash,
and whole-holdout-array hash. WP0 dataset_hash is explicitly the hash of this
manifest, not a relabeled legacy file hash. Each row_hash commits the exact
canonical source dictionary. Check full metadata grid/count/bounds against WP0;
verify every accessed row before exposing it to selection/execution. Manifest
contains no prices, so verifying it does not open unseen holdout. Unaccessed row
contents remain commitments until access; report wording reflects that boundary.
The root commits holdout-array hash as well, checked at its single authorized read.

## Owned evidence and ordering

Caller supplies existing empty run root and separately initialized holdout root;
registry is existing v2. Phase5 owns named plan/trial/walkforward/mc-N children
inside the explicit run root. No implicit top-level root creation, migrations or
resume. RUN claims the root exclusively; fsync directory after child creation.

Persist full manifest and frozen plan first. Advisory retrieval is POST_FREEZE,
read-only and never input to a numerical decision. CPCV applicability/config comes
from that plan. REQUIRED/OPTIONAL_USED sample index must exactly match eligible
non-holdout manifest metadata, including labels available by development end;
NOT_APPLICABLE requires no samples and a bound justification. Actual adapter must
pass before historical trial reservation/data. No silent downgrade of applicability.

Governed WF reserves before read, performs real historical adapter execution,
persists outcome/result, and computes ledger-bound Form A statistics. Binding,
computability, DSR>=.95 and explicit plan dsr_min are mandatory before MC/holdout.
N_total=1 stops here. Invalid historical trials remain counted. Completed historical
trial is distinct from a completed full validation pipeline.

For each WF window, reconstruct engine seed+OOS frame from already verified cache,
retain past training prices as warmup, use the persisted decisions and frozen
parameters, and require exact net-return equality with the committed WF report.
Recompute the entire WF report seal before consuming windows; verify the full
frozen artifact hash, policy/window/range bindings and OPEN_OOS receipt. Re-read
all these artifacts again before holdout, not just their embedded hash fields.
MC uses new `wp5_5.fragility.v2`: explicit optional warmup_rows, persisted scenario
net returns, seed binding to base_run_ref to separate window streams. Earlier v1
source/checkpoint evidence is retained, no historical report rewritten. Existing
engine numerical method is unchanged. Combine matching scenario ordinals across
ALL windows, recompute aggregate metrics and frozen survival predicate; no window
or scenario selection. Whole-pipeline MC readiness is preserve/downgrade only.

Final params are selected ONLY by preregistered LAST_WF_TRAIN rule, before its OOS
and all MC results. Holdout warmup NONE is explicitly preregistered (no implicit
choice). Before reservation, trusted outer verifier rechecks frozen plan, CPCV and
MC artifacts, all MC window reports and FRESH complete ledger-bound statistics.
Persist STATISTICS.json using the actual ordered twelve-check statistics-report
verifier and reject any later change before holdout. PREREQ_NOT_MET cannot reserve
or open holdout.
Persist PREHOLDOUT_VERIFICATION snapshot. Holdout strategy_identity is the WP0
research_scope strategy_family_hash, preventing parameter/experiment variants from
bypassing strategy+dataset uniqueness. Holdout source data is opened only by the
single-use gate, checked against complete manifest rows and frozen array hash,
and evaluated by existing backtest on causal prefixes with frozen parameters.

DSR snapshot is the verified pre-reservation ledger snapshot, not a claim that no
other program trial can ever be registered later. Receipt preserves this snapshot.
A downgrade is recorded and does not itself block holdout (master rule). Even
HOLDOUT_PASSED sets promotion_requires_human=true; no trade/deployment action exists.

STOPPED reports name the failing stage and never authorize later data access.
Exceptions or publication failures preserve consumed reservations and artifacts;
no silent cleanup, automatic retry, repair or evidence reconstruction.

## Explicit limits

This package implements one declared historical profile. Form B is separately
implemented and reviewed in aggregate_statistics/statistics_report; this pipeline
uses Form A. Session/sparse runtime coverage, the full six-event execution timing
contract, prospective session orchestration, deployment/PR release and real
scientific validation remain separate work. Every pipeline result explicitly
reports phase5_acceptance_status INCOMPLETE with timing/real-validation limits.
Pipeline COMPLETED means this named historical profile reached a terminal holdout
result; it is never a whole-Phase5 completion or promotion signal. It must not be described as production-verified or unconditional
completion of every original Phase5 acceptance item merely because this path passes.
