# Pre-result data adapter amendment — 2026-10-27

The first fetch stopped before evaluation: 4,048 of 5,331 funding records have
empty markPrice, through 2023-10-31. All 5,331 scheduled 8h slots are present;
reported timestamps differ from their UTC 8h boundary by 0..47 milliseconds.
No strategy returns, rankings or control outcomes have been computed.

Resolve with an explicit data-adapter convention, leaving raw payloads and the
original registration untouched:
- Accept only timestamp reporting offsets 0..1000 ms; normalize DOWN to the 8h
  boundary, reject duplicates/missing boundaries. Preserve original timestamps.
- Use reported positive markPrice where available. For empty markPrice, use the
  historical Binance mark-price 4h candle OPEN at that same scheduled boundary.
  Never use later closes or fill funding rates. Require complete mark candles.
- This is a historical mark-price proxy at the scheduled instant, NOT proof of
  the exact millisecond exchange settlement mark. Report the number of proxies
  and timestamp normalization explicitly. All strategies and controls share it.
- Funding settles to the pre-boundary position before open orders in the model.
  This is an execution convention, not exchange-ordering reconstruction.

Data adapter code is re-hashed in DATA_AMENDMENT_REGISTRATION.json before the
additional mark-price download. The original source registration remains intact.
Research rules, interval, costs, sample size, and acceptance criteria are unchanged.

Timing-control clarification before results: intrabar trade exits are rounded UP
to whole-bar durations when allocating control paths. This is a conditional timing
diagnostic with coarser exits, not exact intrabar duration matching.
