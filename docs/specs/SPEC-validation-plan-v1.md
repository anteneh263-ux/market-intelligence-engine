# Durable validation plan v1 — architecture decision

Phase5 owns immutable run-local VALIDATION_PLAN.json. Caller supplies an existing
empty absolute nonsymlink directory; no implicit root creation, overwrite,
migration or resume. Uses the reviewed exclusive-create, file/directory fsync and
canonical readback protocol. Failure preserves partial artifacts and refuses a
second freeze in that directory. The artifact has no execution authority.

The plan hash binds strategy ID, dataset segment manifest hash, CPCV applicability
with explicit justification/pin/config, all eight MC policy fields, final holdout
dataset hash and numerical rule, and advisory corpus/retrieval limit. No defaults.
Experiment ID is deliberately outside the plan to avoid recursive hashing; the
outer method identity must include plan_hash before constructing experiment_id.
Read verifies exact schema, canonical bytes and independently expected hash.
A frozen file alone does NOT prove pre-results timing: the full pipeline must
persist it before its first reservation/reader and bind its hash into the method.
Global duplicate-experiment policy enforcement is an outer lifecycle duty.

MC policies cover the entire parameter-patch × absolute-cost grid equally.
Scenario count must be a positive multiple of grid size, at most10000. Seed is an
explicit uint64; perturbation enum NONE/SHUFFLE_TRADES; missed fills probability
[0,1]; nonnegative integer delay; fixed survival predicate min_total_return,
max_drawdown [0,1], and min_fraction_surviving [0,1]. No result-dependent policy.
Holdout dsr_min cannot be below .95. H025 strategy IDs refuse.

This creates new versioned plan evidence only in synthetic test roots at present.
Existing policies, legacy engines and reportedly frozen experiments are untouched.

Full-pipeline holdout policy additionally and explicitly requires parameter_source
LAST_WF_TRAIN and warmup_policy NONE. The standalone validator still accepts the
original bounded plan shape, but the full pipeline refuses it without these fields.
No implicit final-parameter selection or warmup assumption is introduced.
