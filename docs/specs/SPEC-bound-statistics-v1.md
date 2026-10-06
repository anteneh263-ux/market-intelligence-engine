# Bound statistics Form A adapter v1

Read-only evaluate_trial_statistics(registry_path,anchor_path,research_program_id,
trial_id,result_artifacts). Uses v2 registry/mutex snapshot, verifies all chains and
anchor, refuses pending; derives reservation order and ALL COMPLETED_VALID rows.
RESERVED/CRASHED/INVALID still count in verified N_total, never in Sharpe K.

Canonical result artifact schema: exact schema_version=wp5_3.trial_result.v1,
research_program_id,experiment_id,trial_id,net_returns (ordered per-period numeric
list). Entire canonical SHA256 must match persisted terminal result_hash; each
identity matches the reservation. Supplied hash-keyed artifact mapping is content,
not authority by itself. Missing/mutated/identity-mismatched results fail closed.
Each completed-valid artifact must support frozen T>=30/moment calculation; never
silently drop one. Snapshot each artifact canonically before computing. Derive
ordered complete Sharpe vector internally; no caller variance/count accepted.

Output wp5_3.bound_report.v1 contains binding_status, reason_code, program/trial,
verified ledger_snapshot_hash (frozen head hash), ordered trial_ids/result_hashes,
nested numerical statistics and canonical report_hash excluding itself. A verified
binding is NOT a statistical pass: nested stats_status and dsr_pass still apply,
including N=1 invalidity. Failure emits no vector, statistics or snapshot hash.

Bounded integration of approved numerical kernel and v2 ledger, not the entire
master WP5-3 interface. Form B independent-verifier authorization, policy changes,
full frozen report/check envelope, actual result production and pipeline/holdout
integration remain. Default threshold .95 only; no approval allowlist mutation.
No new durable store, no runtime data reads, no v1 migration or historical changes.
