# Real-time market intake — diagnostic v1

**Scope:** isolated technical prototype; not a research dataset or prospective
experiment. No new store, runtime root, scientific authority, orders or strategy
changes. H-025 and all existing artifacts remain untouched.

## Run the bounded diagnostic

```sh
pip install -e '.[realtime]'
python -m app.data.realtime_probe --product BTC-USD --seconds 30
```

The public Coinbase Exchange WebSocket delivers ticker and heartbeat messages.
The diagnostic records counts and the most recent validated price, best bid/ask,
top-of-book sizes, last trade size and exchange/receipt timestamps in memory.
It reports JSON to stdout, does not persist anything, and never falls back to
synthetic market data. It is bounded by elapsed time and number of messages.
An observed quote is not evidence of feed completeness; ticker messages may be
batched. Trade-ID jumps are diagnostics, not proven dropped events. This is not a
reconstructed order book or reliable total trade volume feed.

Documentation: https://docs.cdp.coinbase.com/exchange/websocket-feed/overview
and https://docs.cdp.coinbase.com/exchange/websocket-feed/channels.

## Next decision — proposed, not authorized or active

Before durable capture, approve an architecture decision and concrete external
storage root owned by the operator. Intake would own raw market observations only;
it would have no scientific authority and no migration or equivalence claim over
existing OHLCV providers. Proposed controls: immutable UTC segments, provider and
schema identity, raw bytes and receipt time, content hashes, explicit reconnect
and missing-data markers, capacity limits, no overwrite, quarantine of incomplete
segments, independently reviewed verification and explicit retention rules.
Research Core and controlled sessions retain their authority; no dataset or
prospective evidence binding until separately approved and validated under
ADR-MIE-001. Level-2 order-book reconstruction, feature calculation, and strategy
evaluation are later, distinct work packages. No live-money execution.

## Raw collector v1 — proposed local deployment

See [ADR-MIE-004](adr/ADR-MIE-004-realtime-raw-intake.md). The dedicated root
must be operator-approved and separate from the checkout and every research
store. These commands are for the operator's Mac only, after review; they have
not been executed against a real root. Replace `APPROVED_ROOT` with the approved
absolute location and create its parent separately if needed.

```sh
pip install -e '.[realtime]'
python -m app.data.realtime_collector --root APPROVED_ROOT init --owner "operator"
python -m app.data.realtime_collector --root APPROVED_ROOT collect --seconds 60 --segments 1
python -m app.data.realtime_collector --root APPROVED_ROOT verify
```

Re-running `collect` starts the next numbered segment; previously sealed files
are verified and never overwritten. An interrupted `.partial` remains available
for inspection; it is never promoted to evidence. Connection failures and
wall-clock pauses are recorded. A Mac launch agent (RunAtLoad and bounded retry)
can be generated with `python -m app.data.realtime_collector --root APPROVED_ROOT launchd-plist`.
It prints configuration only; installing it in `~/Library/LaunchAgents` and
bootstrapping it with launchctl are separate operator-approved activation steps
after the reviewed smoke test. At login it starts capture; a periodic retry
does not create an overlapping process. Capacity/integrity failures stop each
invocation rather than deleting or repairing files. After system restart a new
login is required for this user LaunchAgent.
No data can be collected while the Mac is powered off or asleep. Resume records
the boundary and resumes the live feed; it does not backfill or invent events.

### Extend an existing root's capacity (operator-approved, v1)

Stop the launch agent first, update to the reviewed code, and confirm the root
is not collecting. The following command checks the existing limit and free
space, verifies the chain, and writes a separate capacity amendment. The root
marker and all existing segments remain byte-identical. An interrupted
collection may leave a `.partial`; preserve it. Do not manually edit markers.

```sh
python -m app.data.realtime_collector --root APPROVED_ROOT extend-capacity --expected-bytes 2000000000 --new-bytes 6000000000
python -m app.data.realtime_collector --root APPROVED_ROOT verify
```

After successful verification of `max_total_bytes: 6000000000`, restart the
existing launch agent. No new research dataset or experiment is activated.
