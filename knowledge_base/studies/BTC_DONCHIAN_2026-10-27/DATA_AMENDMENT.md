# Data-only amendment before first evaluation

Binance API returned 15,326 of 15,342 expected 4h candles in 2018–2024.
The 16 missing timestamps lie between February 2018 and 2020-02-19 12:00 UTC.
No strategy returns, rankings or evaluation reports had been computed when this
decision was made. RAW_BTCUSDT_4h.csv preserves the received series.

Use the complete suffix beginning at the next UTC day after the last missing
candle: **2020-02-20 00:00 through 2024-12-31 20:00 UTC**, for both 4h and daily.
No interpolation, synthetic candles or parameter changes. This shortens the
historical coverage and limits conclusions. Original PLAN.md is preserved.
