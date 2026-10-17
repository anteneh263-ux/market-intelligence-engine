# ADR-MIE-008 — single-owner raw intake on existing object storage

Status: accepted for local implementation; live deployment remains pending.

Use the existing descriptor-bound ObjectWriter with an explicitly provided empty
physical root, owner and reservation run identity. SourceIntake exclusively claims
that writer. It is an input recorder, not scientific registration, admission,
independent retention, execution authority or an additional database engine.
The final operator must bind the approved reservation and authorized root before
construction; this class verifies structural reservation semantics, not approval.

Persist each accepted or structurally valid non-final Binance frame, supplied clock
probe, normalization result and (where applicable) normalized payload and receipt.
Then persist a hash-linked source_intake_commit containing sequence, frame bytes,
cumulative observed bytes and object hashes. Count every frame, including identical
frames and non-final bars; content-addressed deduplication does not refund intake.
Enforce the plan's cumulative raw byte limit before further writes. ObjectWriter's
separate physical storage limit also applies, including derived records.

Any validation, clock, ownership, storage or uncertain acknowledgement failure stops
the owner. Partial objects stay preserved; no restart/recovery/retry path is added.
An over-limit frame is refused without an acknowledged intake commit. This class
does not promise full forensic capture of invalid/refused network frames. The future
operator must persist its stopped/incomplete outcome and never replace an attempt.

No migration, equivalence transfer or replay of prior evidence. This is a new typed
local record stream. Existing store encoding and historical artifacts are unchanged.
Live transport, measured clock trust, source finality/revision/gap checks across the
full stream, journal gate consumption and independent anchoring remain upstream.
