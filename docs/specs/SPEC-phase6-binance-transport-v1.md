# One-shot Binance transport candidate

Status: implemented; synthetic and real loopback WebSocket integration tested.
Not connected to Binance, not admitted for prospective execution. No source terms
or geographical-access verification is implied by this implementation.

The fixed endpoint is wss://stream.binance.com:443/ws/. One subscription requests
btcusdt@kline_1m and btcusdt@ticker; one exact null-result acknowledgement with
integer id1 is required before collection. Any other initial message refuses setup;
there is no silent market-message skipping or reconnect. The optional phase6-source
extra pins websockets16.1.1, also checked at connection time. Default certificate
and hostname verification remain enabled; proxies and compression are disabled.
Open/ack timeouts are10s, close timeout2s; message limit65536bytes and queue high
watermark1 are explicit. Ping/pong and fragmentation are handled by the library.

BinanceClockBoundTransport constructs and owns ClockBoundSourceIntake. Every
application message returned as original bytes by recv(decode=False) is immediately
passed to its receive method, then the durable result to a synchronous on_commit
callback. A failed callback leaves the durable intake commit intact and fails the
run without retry. This callback is an integration seam, not an implemented remote
receipt-service client or proof that external retention acknowledged the commit.

One run only, with explicit1–10000message bound and1–1800second collection budget.
The duration starts after subscription acknowledgement. Each receive is capped
at the smaller of5seconds or remaining duration; timeout refuses the run. A message
returned at the deadline is retained through intake before overrun is reported;
no success callback is issued for that overrun. Time is checked again after callback.
Callbacks/storage are cooperative: a blocking callback or fsync can exceed the
budget. An external watchdog is required for a hard process wall-time bound.
Close attempts socket and intake cleanup; cleanup failures never replace an
existing primary exception. Foreign-owner calls cannot operate the connection.

Queue high watermark is flow control, not a maximum network arrival-age guarantee
or proof that all market events were observed. The clock measures application
intake, not wire arrival. Kernel/TLS/library buffering, quote freshness, stream
gaps/revisions, real provider accessibility, full capacity and the admitted operator
must still be verified. Runtime/source authentication flags remain false.

Sources checked2026-10-17:
- https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md
- Installed websockets16.1.1 sync/client.py API and implementation (single handshake;
  no reconnect in connect). Public API reference:
  https://websockets.readthedocs.io/en/15.0/reference/sync/client.html
