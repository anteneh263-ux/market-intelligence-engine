# WP6-2N — pure checkpoint artifact binding verifier v1

2026-10-15. verify_checkpoint accepts exactly manifest,checkpoint,checkpoint_commit,
event,payload,previous_event,state,strategy_state,execution_commit,diagnostic_commit.
All are decoded plain objects; bounded snapshot precedes checking. No I/O, code
execution, clock callback, source loading or runtime admission. Supplied mappings
cannot prove canonical original bytes or reject duplicate keys lost during parsing.

Exact WP0 K schema, native types and exact amount representations must agree with
validated reducer output. Canonical strategy state count/params/lastsegment bind to
K, reducer and manifest. K previous checkpoint comes from M. CHECKPOINT_PREPARED is
validated against its payload, manifest and immediate predecessor. K binds its
count/head; payload state/strategy/prefix/receipt/pending fields bind to K and
supplied states. Checkpoint commit hashes bind all six supplied targets. Diagnostic
commit output binds strategy state; execution commit output binds reducer state.

Dispatch exact bar or quote commit schemas. Bar checkpoint requires no pending or
barrier; a null bar event is only warmup, with predecessor count3, matching diagnostic
event and count <= config.warmup_bars. Otherwise predecessor count4 is NO_TRADE or
risk STOP. Quote event hashes are one/two distinct hashes, ending at predecessor
count5/6. Two-event risk requires predecessor.previous_event_hash equal first hash.
Timeout pending requires quote execution, stopped QUOTE_TIMEOUT and previous STOP.
One-event non-timeout requires nonstopped SIMULATED_FILL/REJECTED. Checkpoint clock
must dominate reducer action clock; ordinary event chronology applies too.

VALID means supplied local artifact linkage only. The previous event envelope is
checked, but its payload/earlier ancestry are NOT supplied or verified. Pending
intent artifact, invocation descriptors, earlier events, receipt/prefix artifacts,
previous checkpoint contents, anchors, actual loaded source and authentic time are
still upstream. This is not replay equivalence or a proof of actual execution.
A consistently fabricated complete input bundle is outside this pure check's trust
boundary. Consumers must authenticate expected hashes and physical publication.

Result p6.checkpoint_validation.v1 reports binding_status VALID/INVALID, reason_code,
checkpoint_hash only on success, runtime_accepted=false,
external_reference_verification=UPSTREAM_REQUIRED,durability_verified=false,
full_journal_verified=false. No automatic path to READY/ANCHORED/resume. This helper
is tested against objects read from real temporary EventJournal publications but is
not yet installed as full runtime admission or a disk-store inspector.
