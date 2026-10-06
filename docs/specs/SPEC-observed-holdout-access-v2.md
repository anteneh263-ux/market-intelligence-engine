# Observed holdout access policy v2

This is a narrow methodology version, not complete Phase 5 conformance.
The original R3 WP5-6 (lines 568–571) permits holdout after valid Monte Carlo
with either PRESERVED or DOWNGRADED readiness, retaining the downgrade.
Observed plan v1 intentionally remains PRESERVED-only.

An exact wp5.observed_plan.v2 adds the mandatory field
holdout_access_policy=AFTER_VALID_MC_RETAIN_DOWNGRADE. The field is hashed into
the preregistered plan before evaluation, then bound through WF policy, method,
MC artifacts, pre-access proof and the ledger-anchored holdout execution receipt.
There is no implicit conversion of existing v1 plans or persisted evidence.

The actual run and prerequisite-verification paths both require MC status VALID
and a closed PRESERVED/DOWNGRADED readiness value. v2 permits either; v1 still
rejects DOWNGRADED. Complete scenario/evidence verification remains mandatory
before holdout reservation. Invalid, tampered, omitted or incomplete evidence
cannot become eligible merely through a readiness label.

v2 RUN and report use separate schema versions. The final report retains MC
readiness explicitly, including when HOLDOUT_PASSED. HOLDOUT_FAILED remains a
completed evaluation. promotion_authorized=false and LIMITED_PROFILE remain.
Read-only verification rederives the MC report and rejects any mismatch between
its readiness and the final report, including a rehashed relabeling.

This adds no engine/store, no recovery and no data access entitlement. Existing
holdout ownership, consumed-before-read ordering and cross-version single-use
protection remain authoritative. Real-data execution requires its own approved
contract/root. No real holdout or pilot001 artifact was changed by this package.
A separate reviewed PR is required before publication under AGENTS; no commit,
PR, merge or deployment is performed merely to preserve this work.
