# Pure timing-row check v1

Internal check_timing_rows(rows, contract_record, intended_dataset_zone=...) assumes
CONTRACT_BINDING and TRIAL_TARGET already passed. It does not authorize a trial or
assert calendar membership. No I/O. Exact six integer timestamps excluding bool,
nonempty list, strict bar-open order, duplicate refusal. First schema/order faults
precede contract predicates. Output check_id,status,reason_code,result. Successful
result is timing-mode detail without trial_id (the dispatcher supplies verified ID).

Implements matrix R3 §7 twelve continuous predicates with R4 C1 corrected
fill_bar_offset_lower_bound source. Deferred coverage follows R2 K4: four monotonic
inequalities, declared zone containment and all nonnull derived offsets; never
assumes session/sparse grid membership. R5 D5 limitation enum is preserved.
TIMING_ROW_INVALID / DUPLICATE / OUT_OF_ORDER / TIMING_CONTRACT_MISMATCH only.
Hash is SHA256 of canonical ordered rows; no sorting of rows. Failure detail null.

Synthetic hand fixtures cover continuous/deferred modes, lower bounds, late valid
fill, bad types, malformed shape, duplicate/order and boundary/grid failures.
No modifications to frozen identity module or historical engines.

R4 C8 requires availability/decision/eligibility offsets to be integer in ALL
coverage modes. Only nominal earliest fill offset may be null in deferred modes.
Actual WP5-0A1 interface tests cover all three coverage modes.
