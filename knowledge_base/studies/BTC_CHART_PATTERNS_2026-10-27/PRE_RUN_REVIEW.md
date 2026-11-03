# Dedicated source and execution review before real evaluation

Reviewer: implementing assistant, separate review pass. NOT independent review.
Scope: new chart_patterns_v1.py, chart_patterns_run.py, both synthetic test files,
registration, raw-input schema and data amendment. Existing engine code unchanged.

Executed: 25 new synthetic tests passed, followed by a combined run including the
existing test_strategy_research.py regression suite; all executed tests passed.
No real strategy returns had been calculated when this review was recorded.

Checked:
- All five shapes produce signals in full synthetic OHLC paths, not just isolated
  geometry tests. Signals are absent before confirmation/breakout; source-prefix
  and future-perturbation checks pass. Shapes and preceding trend are symmetric.
- State uses only pivots confirmed at current close, strict ties and crossed
  neckline rules; entry sees previous candle's signal. No centred-window signal
  placed on the pivot's historical timestamp.
- Signed fixed quantity accounting: entry/exit fees, long/short mark-to-market,
  adverse fills, positive/negative funding, funding before same-time open orders,
  gap cancellation, gap stops, conservative intrabar collision, seven-day expiry,
  terminal liquidation and drawdown relative to initial capital have tests.
- Timing controls preserve signed duration multiset in synthetic integration
  tests; explicitly approximate intrabar exit durations by rounding up to bars.
  Do not interpret this diagnostic as an exact exchange execution null model.
- Holm adjustment covers all five rules jointly; no optimisation/search of
  tolerance, width, exits or periods. Cost grid is not used to select a favourite.
- Input: 10,662 contiguous 4h candles and 5,331 complete funding boundaries.
  Mark-price proxies and millisecond normalization documented BEFORE evaluation.
- Source hashes match the pre-result data-amendment registration.

Limitations retained explicitly: uncalibrated exploratory screen; no independent
review, exchange-order-book/margin model or production verification. Reported
holding hours are a candle-resolution approximation, not exact intrabar time.
Bar-close drawdown can understate intrabar drawdown. Positive results would still
require independent review and new-data research before promotion.
