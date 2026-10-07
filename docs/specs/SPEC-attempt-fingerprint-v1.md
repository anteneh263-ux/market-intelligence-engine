# Attempt fingerprint v1 — internal validation continuation

Implement original WP5-0A2 section 5.1, R5 D-4 and R6 fingerprint rulings.
Hash exactly five fields: research_program_id, experiment_id,
ordered_input_hashes, method_hash, intended_dataset_zone. SHA-256 lowercase hex
of UTF-8 json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=False,
allow_nan=False). List order and duplicates are preserved. No normalization,
sorting of lists, deduplication, reservation suppression, clock or I/O.

New APIs in app/research/attempt_fingerprint.py:

- check_attempt_fingerprint(record, *, research_program_id, experiment_id)
  returns exactly {check_id: ATTEMPT_FINGERPRINT, status: PASS|FAIL,
  reason_code: null|ATTEMPT_FINGERPRINT_INVALID, attempt_fingerprint: hex64|null}.
  A defensive internal helper: exact built-in dict with the five keys, string
  keys, lowercase hex64 IDs/hash, non-empty built-in list of lowercase hex64,
  zone TRAIN/VAL/HOLDOUT. Reject custom Python subclasses. Nested program and
  experiment must match the supplied identities. Every invalid input fails
  without mutation; no fingerprint is exposed on failure.
- check_trial_preflight(request) composes the existing check_request_preflight
  with this helper, without changing either previous component. Returns exactly
  {checks, reason_code, attempt_fingerprint}. Existing prefix failures retain
  their reason; for known RESERVATION add ATTEMPT_FINGERPRINT NOT_EVALUATED if
  earlier checks fail, otherwise execute it. Invalid mode retains only INPUT_MODE.
  Other modes retain their existing prefix and expose attempt_fingerprint=null.
  No later registry checks are pretended to have run.

Caller-supplied attempt_fingerprint remains REQUEST_SCHEMA/INPUT_SCHEMA_INVALID.
Ordinary malformed fingerprint-list elements fail REQUEST_SCHEMA before this
helper. Nested identity mismatch or empty input list passes schema/binding and
fails ATTEMPT_FINGERPRINT_INVALID. When a request has multiple defects, the
existing prefix owns earlier failures. This wrapper is internal only; it is
not a public registry report or execution permission.

Scope: only the new module, tests/test_phase5_attempt_fingerprint.py and this
spec. Preserve all six files from earlier packages and all committed files.
Synthetic inputs only. Do not import frozen test fixtures. No live storage,
H-025, migration, production activation, commit/push/merge or orchestration.

Write and execute negative tests against a failing stub first. Verify a fixed
canonical known-answer vector, each field's sensitivity, dict-order invariance,
list-order/duplicate sensitivity, repeat stability and no mutation/I/O. Test the
composed prefix's check order for schema, binding and fingerprint failures.
Then run Phase 5 regressions/full suite and independent read-only agent review.
Report engineering success separately from missing registry integration.
