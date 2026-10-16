# WP6-3A — Binance raw normalization and source linkage v1

Authorized scope: continued Phase6 code and synthetic tests. Base
 d30baddc11f1ac93b6c89968ad43a42b283eec13. No network connection, credentials,
pilot, provider activation, storage root or historical evidence modification.

Primary protocol reference checked 2026-10-16:
https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md
This is a versioned parser for a selected documented subset, not a guarantee that
all future exchange messages are supported. Schema changes fail closed.

normalize_message accepts immutable raw bytes only, 1..65536 bytes, ASCII JSON,
unique keys, no JSON floats/constants. It supports raw (not combined-wrapper)
BTCUSDT UTC1m kline and individual24hrTicker messages. Strict field/type checks
apply also to unused fields. Open candles are IGNORED, not accepted observations.
Closed-candle inclusive T becomes exclusive close_ms=T+1; interval/alignment,
closed flag, OHLC/volume and event-time finality are checked. Source E is retained,
not replaced by a local clock. Ticker b/a become sampled bid/ask, never rolling
statistics as feature bars. All decimals are parsed from strings, trailing zeros
removed without binary float conversion. Input decimals have at most18 fractional
digits; normalized prices at most8 and at most1e12 to match existing model bounds.
Prices must be positive; spread nonnegative. Nonnegative integer ids/times are a
supported subset; unsupported provider values stop instead of guessing.

Output p6.source_normalization.v1 records adapter_version=p6.binance_spot_source.v1,
status/reason, exact raw byte hash, normalized canonical hash/payload, kind and
provider_event_ms. Identity is fixed to binance-public-spot / BTC:USDT. Invalid or
ignored output is not receipt-ready. runtime_accepted and source_authenticated
remain false on all outcomes. Existing payload/receipt schemas are unchanged.

verify_source_binding recomputes normalization and compares exact payload and four
receipt links (kind,eventtime,rawhash,payloadhash). It accepts either the full
structurally valid p6.receipt.v1 or exactly those4 projection fields; it does not
validate receipt chronology or the full intake chain. Its result explicitly keeps
receipt_chronology_verified=false, source_authenticated=false and runtimefalse.

No transport, wall/monotonic capture, trusted clock probe, persistence, reconnect,
normalization of combined streams, provider-term assessment or mandatory source
binding in the runtime gate is implemented by this module. It is not live-ready.
A changed raw byte stream retains a different raw hash even when whitespace or
trailing numeric zeros produce the same normalized payload. Adapters and their
source schema/version must be pinned by the actual pilot contract.

48 tests cover accepted/ignored/invalid data, symbol/schema/time/decimal/OHLC/spread
attacks, duplicate keys and non-finite/float inputs, raw-hash distinction and
rehashed normalized-payload substitution. One integration feeds normalized bar and
receipt links through the existing intake validator. Dedicated review is required
before representing this as supported source normalization.
