# WP5-TIMING-INTEGRATION-001 — observed simulated replay v1

Authorization: renewed user instruction to complete remaining Phase5 integration.
Architecture decision: existing app.paper.session retains execution authority.
A separately versioned module app.paper.session.observed_engine_v1 contains the
instrumented numeric engine; legacy engine.py remains byte-identical to its frozen
Phase 4 source pin. No migration, legacy cutoff or scientific result replacement.
Software version paper-session-observed-v1; numeric behavior and journal bytes
remain equivalent for the common continuous profile. A separately owned Phase5 observation
artifact records callback-point events using the engine's VIRTUAL market clock,
not wall-clock reception or prospective validity. No H025 inputs/artifacts allowed.

Ownership: caller supplies existing empty absolute evidence root. Phase5 exclusively
claims RUN/MANIFEST, creates its journal child explicitly, and owns immutable
fsynced event-N.json observation receipts and terminal result there. Existing
session Journal still owns journal.jsonl in that child. Final result binds both
chains. No implicit source-root stores, resume, repair or dedup-based reopening.
Failure during observation is fatal, preserving an incomplete root. Actual trial
orchestration will reserve before performance read and bind this root/result.

Optional engine observer fires at actual execution points: virtual bar release at
close BEFORE close-price use; risk-feature observation before risk evaluation;
strategy-feature observation before causal strategy call; decision after accepted
sanitization; order submission before broker.execute; order result after broker
returns; broker errors and rejected signals explicitly observed. Correlate every
submission with the originating decision separately from numeric pending target.
Risk exits are explicit risk decisions; retries retain source identity. Only
filled/partial outcomes with nonzero filled units yield six-field timing rows.
No-trade/rejected/error/final pending/cancelled outcomes remain accounted for.

Availability means release in this new virtual scheduler, never legacy
bar_ingested (which records completed OHLCV at bar open). Zero settlement lag,
signal delay and submission latency only. Other policies must refuse before read.
All event timestamps originate in this opt-in execution, never reconstructed from
an old journal. Prefix frames/params are isolated in this mode; parameter mutation
is rejected. Recording failures propagate, including after a simulated execution.

Dedicated review required. Tests: opt-in negative first; observer-off equivalence;
full six events; future isolation; partial/rejected/no-trade/errors/risk exit/final
pending accounting; interrupted recording; no same-root replay; manifest mutation.
Real pilot, continuous prospective operation and source-data provenance remain
separate from synthetic execution integration.


Reviewed failure corrections: before broker.execute the opt-in branch snapshots
financial state and closed trades. BrokerError after mutation emits an explicit
EXECUTION_OUTCOME_UNKNOWN observation and aborts; no retry/VERIFIED result. All
branches clearing numeric pending also clear its source. Terminal pending identity
must exactly match unresolved decisions. Same-time terminal equity adjustments are
merged; first close is the return baseline; explicit equity timestamps are retained.
Read-only verify_observed_session requires an externally bound report hash and
validates every immutable event, native journal, orders and derived equity/returns.
