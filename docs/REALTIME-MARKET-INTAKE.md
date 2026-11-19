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
