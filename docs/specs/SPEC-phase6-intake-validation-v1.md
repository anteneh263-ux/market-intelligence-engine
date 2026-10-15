# WP6-2O — supplied intake prefix verification v1

2026-10-15. Pure verify_intake_prefix accepts exact fields plan,reservation,receipts,
segments,payloads,clock_probes,prefix. Bounded plain snapshot first; 1..64 receipts
and 1..64 segments, each hash map at most64 entries under shared snapshot limits.
Empty prefixes are unsupported. This bounds a supplied prefix, not complete-run
coverage or an unbounded live intake API. No I/O or MIE strategy execution.

The existing plan and receipt validator checks plan, reservation binding, the complete
mixed receipt chain from sequence1/zero predecessor, normalized hashes, provider/
instrument, decimal/data rules and clock probes/session/chronology. Both maps must
contain exactly every normalized/probe hash referenced by receipts; unused future
objects refuse. Raw payload hashes remain unresolved, so provider adapter finality
and normalization truth are NOT established.

After these checks, first BAR_FINAL occurrences are selected by provider,instrument,
open_ms. An identical full normalized hash is a retained duplicate, never another
segment. Any different full normalized hash for the same key refuses REVISION_CONFLICT,
even if the changed receipt is not referenced by a segment. QUOTE receipts are checked
but never enter the returned bar prefix. Quotes may precede or follow accepted bars.

Segments must correspond exactly to first bar occurrences in receipt order. Each
segment is checked using the receipt-chain prefix ending at its accepted receipt,
with previous segment supplied. This enforces first segment seq1, previous zero,
first open=plan.start_ms, contiguous planned intervals/sequences and hash chaining.
Missing, reordered or duplicate segments refuse; nothing is silently sorted/dropped.
Trailing quotes/identical duplicates are still verified by the global chain check.

New additive commitment schema p6.accepted_prefix.v1 has exactly schema_version,
run_id,segment_hashes. run_id=H(reservation), segment_hashes is the ordered list of
all accepted segment hashes in this supplied prefix. The supplied commitment must
match this derived object. Earlier opaque prefix hashes are not rewritten or certified
under this schema. No historical evidence migration or runtime deployment occurs.

Result p6.intake_prefix_validation.v1: binding_status VALID/INVALID, reason_code,
accepted_prefix_hash, ordered bar_hashes, last_receipt_seq (only populated on success),
runtime_accepted=false,callback_inputs_verified=false,durability_verified=false,
external_reference_verification=UPSTREAM_REQUIRED. Binding a supplied prefix does
not prove actual callback consumption, completeness against the live stream, first
observed eligible quote, source/clock trust, reservation authorization or durability.
Future runtime must link this result to actual callback input and checkpoint content.
