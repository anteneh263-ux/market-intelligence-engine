# Phase 5 request preflight v1

This new internal component implements INPUT_MODE, FORBIDDEN_ASSIGNED_FIELDS,
REQUEST_SCHEMA and (where applicable) the existing CONTRACT_BINDING helper.
It does not implement fingerprint derivation, registry access or any trial action.
A passed prefix is never authorization to execute a trial.

API: `check_request_preflight(request) -> {checks, reason_code}`. Each check has
exactly check_id/status. Unknown mode/non-dict gives only INPUT_MODE FAIL and
INPUT_MODE_INVALID. Known modes emit the three prefix checks plus CONTRACT_BINDING
only for RESERVATION/TIMING_VALIDATION. First failure wins and remaining prefix
checks are NOT_EVALUATED. No registry response envelope is manufactured.

Built-in dict/list/scalar JSON inputs only; custom Python mappings are outside
this internal JSON boundary. Unsupported objects, cycles and non-string keys
outside contract_record are REQUEST_SCHEMA errors. Scan supported containers for
assigned fields before schema errors. contract_record is exempt only as the
root field of RESERVATION/TIMING_VALIDATION; its content is handled by binding.
A nested field merely named contract_record is never exempt.

Assigned fields: trial_id, reservation_sequence, reservation_utc_ms, N, N_total,
row_hash, previous_row_hash, verification_replay_id, outcome_sequence, outcome_utc_ms.
Only top-level trial_id is allowed in TIMING_VALIDATION/OUTCOME_APPEND.
A forbidden occurrence gives CALLER_SUPPLIED_RESERVATION_FIELD. Top-level extras
owned by another mode give OUT_OF_MODE_FIELD; other extras, missing fields and
invalid types give INPUT_SCHEMA_INVALID. At nested depth non-assigned extras
always give INPUT_SCHEMA_INVALID. On simultaneous schema defects, prioritize
foreign top-level keys, then other shape/type errors (new deterministic tie rule).

Exact top-level fields:

- RESERVATION: mode, research_program_id, experiment_id, contract_hash,
  contract_record, attempt_fingerprint_record.
- TIMING_VALIDATION: mode, research_program_id, experiment_id, contract_hash,
  contract_record, trial_id, timing_ledger_rows.
- OUTCOME_APPEND: mode, research_program_id, trial_id, trial_outcome, result_hash.
- VERIFICATION_REPLAY: mode, research_program_id, verification_replay_record,
  performance_data_accessed, append_reservation_requested.
- REGISTRY_STATE: mode, research_program_id.

All IDs/hashes are exact lowercase hex64 strings. Outcomes are COMPLETED_VALID,
COMPLETED_INVALID or CRASHED. Replay flags require bool type; true is deliberately
left for REPLAY_ELIGIBILITY, as final test plan d27/d28 requires. A prefix PASS
therefore does NOT mean a replay request is eligible.

Fingerprint record has exactly research_program_id, experiment_id,
ordered_input_hashes, method_hash, intended_dataset_zone. IDs/hashes are hex64;
zone is TRAIN/VAL/HOLDOUT; input hashes is a list of hex64. Empty list and mismatched
nested identities are left for ATTEMPT_FINGERPRINT (R5 D-4/R6).
Replay record has exactly replay_of_trial_id, original_input_hashes,
original_method_hash, original_result_hash; IDs/hashes are hex64 and input hashes
is a list of hex64. No additional non-empty-list rule is invented here.

contract_record content and type go to CONTRACT_BINDING; a present malformed
record must not be reclassified as a request-schema error. timing_ledger_rows
must be a list; row shape/types/emptiness are left for TIMING_ROWS under R3 M-5
and R6 timing rulings. The assigned-field scanner still inspects its containers.

Sources: WP5-0A2 R2 section 4; R3 B-3, B-4/B-5, M-5 and section 12; R5 D-3;
R6 E-2/fingerprint/timing rulings; final test plan d27/d28. Later rulings supersede
R2's older order and NOT_APPLICABLE status. Frozen WP5-0A1 output is unchanged.

Implementation scope: add app/research/request_preflight.py,
tests/test_phase5_request_preflight.py and this spec only. Preserve the previously
reviewed CONTRACT_BINDING files. No CLI, persistence, frozen test edits or real
research artifacts. The user's continuation authorizes this next internal
validation step; these API/tie choices are local implementation decisions.

Verification: negative tests execute against a temporary failing stub before
implementation; then new tests, Phase 5 regressions and full suite. Independent
review is required. Report actual results, integration boundary and any omissions.
