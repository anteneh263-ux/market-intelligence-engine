# WP5-1 purge and embargo v1

Pure implementation of master R3 WP5-1, unchanged by R3A/R3B. No historical
methodology or existing execution engine modified. No runtime integration claim.

API purge_embargo(request). Exact request keys sample_index, test_window,
embargo_ms, experiment_id. test_window is [start,end]; sample_index is an ordered
list of exact {sample_id,feature_time_ms,label_start_ms,label_end_ms} dictionaries.
IDs are unique nonempty strings; experiment_id lowercase hex64; timestamps signed
integer epoch milliseconds excluding bool. Intervals strictly positive duration.
Embargo duration nonnegative integer milliseconds. Empty input is valid empty
partition. Stable caller order within each partition.

Purging uses half-open interval overlap; embargo is a separate predicate on
feature timestamps in [test_end,test_end+embargo_ms). Purging takes precedence.
Exact master output fields and check order. Invalid inputs return null partitions
and null counts/hash; no partial data can be mistaken for an accepted partition.
Bad top-level/sample shape or duplicate IDs => SAMPLE_SCHEMA_INVALID; invalid
interval/timestamp => INTERVAL_AMBIGUOUS; invalid duration => EMBARGO_WINDOW_INVALID.
Report hash SHA256 canonical UTF-8 JSON {input:request,report:output_without_hash},
sorted dictionary keys, compact separators, no NaN, no normalization. This binds
experiment and interval even when two requests produce the same partition.

No assumed horizon, temporal repair, dataset access, external library or scientific
threshold. Tests cover frozen boundary truth table, independent expected partition,
invalid intervals, unknown horizons, duplicate IDs, fail ordering and hash binding.
