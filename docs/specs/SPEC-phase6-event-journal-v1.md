# WP6-2I — durable typed local event journal v1

Date: 2026-10-13. Owner app.research; parent ADR-MIE-004. Reuses the
WP6-2B immutable object writer. No new storage backend, migration or engine.
No runtime/pilot activation; only temporary synthetic test roots.

EventJournal(writer,manifest,*,utc_ms,monotonic_ns) requires a fresh exact
ObjectWriter, a bounded detached WP0 manifest and explicit supplied timestamps.
Build and verify the first MANIFEST_VERIFIED payload/event with WP6-2H before
claiming the writer; invalid preflight leaves it untouched. Writer.run_id must match manifest.run_id. Manifest has no
owner/root fields: authorization of writer owner/root remains upstream, not inferred.
Persist canonical manifest, then payload, then exact WP0 J. Only after successful
publication/readback acknowledge OPEN with count1, session_id=H(M), head=H(J).
The event's supplied time is not proof of manifest persistence time or real clock.

append(kind,payload,*,utc_ms,monotonic_ns) snapshots bounded primitives and builds
J identity, next sequence and previous hash from pinned acknowledged context.
Caller cannot override those fields. Verify via WP6-2H before publication; publish
payload then J. Update count/head only after event publication returns and lifecycle
is still OPEN. Event is the append commit marker. No second commit format or final
checkpoint invented. Refused append is terminal even if it wrote no new bytes.
Stored payload references are not resolved by this journal. In particular, a
strategy-state/receipt hash alone does not prove that artifact was persisted.

Any post-claim construction, admitted append, close or verification failure is
INCOMPLETE and preserves partial publications. No repair, rollback, rewrite,
append retry or same-root reopen. Snapshot inventory can omit uncertain writes.
Bound to creating process/thread. Foreign callers refuse without poisoning owner;
same-thread reentrant mutations poison outer operation. Check lifecycle after I/O
boundaries before acknowledgement. Preserve original exception if cleanup fails.
close while OPEN closes writer then inspect_objects over exact acknowledged
inventory before CLOSED. Neither OPEN nor CLOSED means actual execution/admission,
complete journal lifecycle, anchored checkpoint or prospective completion. Repeated
close on terminal journal is inert. No context manager auto-completion semantics.
Snapshot exact p6.event_journal_status.v1: run_id,session_id,lifecycle,event_count,
event_head_hash,last_event,objects,technical_verification=LOCAL_ONLY,
runtime_accepted=false. All values detached.

The journal stores all ten reviewed payload kinds using the pure local link checks.
STOP is only a typed event here; subsequent locally valid events remain allowed.
It never implies a successful or terminal run. Exact detached canonical bytes are
prepared once, checked and published unchanged. Physical descriptor/root binding
is rechecked immediately before append/construction acknowledgement.
It does not enforce full event order, authenticate code/plan/clock, invoke a strategy,
select quotes, check arithmetic or perform external anchoring. A synthetic test
constructs payloads using actual strategy/reducer outputs; this is limited storage
integration, not the full causal execution path. Next is governed strategy/reducer
call-site integration, artifact resolution and checkpoint binding, including a
separate actual post-intent-publication barrier before first quote selection.

Negative-first tests: absent module; invalid preflight without writer consumption;
all ten kinds; pinned sequence/hash; detached snapshots; invalid append; payload
and event write-then-raise; manifest construction failure; reentry; foreign owners;
root replacement; final close/inspection failure; no reopen and immutable inspection.
Dedicated independent contract/code review required. No changes to historical
engines, old artifacts, scientific acceptance or H-025.
