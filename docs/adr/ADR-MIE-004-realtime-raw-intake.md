# ADR-MIE-004: isolated real-time raw intake

Status: proposed implementation for review; operator approved the Mac location
and resumable intake on 2026-11-19. No deployed collector or research authority
is asserted. The operator supplies the approved absolute external root explicitly.

## Decision

The operator owns the root, capacity, access, backups, and retention.
`app.data.realtime_collector` owns only raw Coinbase Exchange BTC-USD transport
observations, under schema `mie.raw-intake.v1`. It never owns an OHLCV dataset,
strategy, Phase 5 trial, controlled session, or H-025 observation. No migration,
equivalence to historical providers, scientific evidence binding, or trading.
The existing probe remains unchanged.

Use a dedicated marked directory, external to the checkout and other stores.
An explicit `init` creates a new root only; collection cannot initialize one.
No automatic deletion, overwrite, retention pruning, or repair. A single
operator process holds an advisory exclusive lock. Limit total bytes, segment
bytes, duration, messages, number of sessions, and free space; stop at capacity.

### Operator-approved capacity amendment (2026-11-19)

The operator approved preparing a 2,000,000,000 to 6,000,000,000 byte increase
after confirming adequate local free space. Stop the LaunchAgent before the
change. `extend-capacity` checks the exact prior limit, verifies the existing
segment chain, verifies free disk space, and exclusively publishes a separate
canonical `capacity-v1.json` linked to the original root marker hash. It never
changes `root.json` or raw segments. Its SHA-256 is checked on load; the collector
honors the effective limit under the existing lock. No reduction or second
amendment is supported by v1. An interrupted amendment staging file fails
closed and requires review; no repair is automatic. No scientific authority,
schema migration, or historical equivalence is claimed.

## Raw segments and restart

Each connection opens a uniquely numbered `.partial` JSONL segment before
network access. The header identifies schema, provider, product, start UTC,
last sealed segment/hash/receipt and any earlier unfinished segments. Data
records preserve WebSocket payload bytes (base64), frame type and receipt UTC;
malformed messages remain raw. Separate gap records mark timeout, disconnect,
or a wall-clock pause. A footer gives counts, terminal reason and SHA-256 over
the exact preceding bytes plus the canonical footer without its hash field.
Compare wall clock on exit even without another frame, including timeout past
the segment deadline; compare the last sealed end time with the next start time
to mark extended downtime across process restarts. These comparisons flag
possible interruptions, not proof of the exact cause or missing market events.
Validated ticker trade IDs carry a high-water mark across sealed segments;
duplicate/reordered IDs and jumps are diagnostic counts only, not removed data.
Sealed files are fsynced, published without replacing
anything, then the directory is fsynced. A partial left by a crash is retained
and treated as incomplete, not silently recovered or promoted. A read-only
verifier checks sequence, hash, count, link, and the non-evidence flags.

On restart, scan and verify existing sealed files, then open the next number
after both sealed and partial files. Never append to a sealed segment. Resume
means collecting from the current feed, not recovering observations missed
while the computer slept or was off. Every connection boundary is a possible
gap; ticker batching and trade-ID jumps cannot establish completeness. The
collector stops on storage or integrity failure; network failures are recorded
and the next bounded connection may try again. A crash may lose unsynced bytes
in the incomplete segment. No claims of power-loss durability beyond filesystem
fsync semantics, or of guaranteed completeness, are made.

## Activation gates

Review and pass offline tests including interruption, restart, tamper, capacity,
and absent optional dependency. A separate Mac launchd setup and real-data
smoke run must be explicitly approved after verifying the physical root and
ownership. No always-on service or real-data capture is started by this change.
The operator should keep the Mac awake if uninterrupted capture matters. The
system must resume after login/restart when launchd is later configured, and
mark sleep/network gaps rather than fabricate past messages.
