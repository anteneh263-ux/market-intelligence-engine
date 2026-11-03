# BTC Donchian 20/10 — historical MIE study

**Primary result: Not Supported (4H). Daily reference: Inconclusive.**

Tested long/cash, full unlevered notional, close breakout above the prior 20-bar high; exit below the prior 10-bar low. One fixed setting. This is a Donchian core rule, NOT the complete geneZ_fi Turtle TradingView script (no shorts, ATR sizing, pyramiding or stop orders).

## Data and method

Binance BTCUSDT spot; complete input 2020-02-20 through 2024-12-31 UTC. Daily candles aggregate the same six 4H bars per day. Original 2018 start was shortened solely for 16 missing source candles, before evaluation; see DATA_AMENDMENT.md. No 2025+ or holdout inputs were accessed.

MIE srp-matrix-v4 at commit c41afe43fc70d50140754558fda62a2deea48761. The engine with n_splits=5 produces four nonempty test folds; output dates start on 2021-02-09 (4H at 08:00 UTC, daily at 00:00 UTC). These are algorithmic walk-forward segments of previously available historical data, not a pristine prospective holdout.

Headline cost = 5 bps fee + 5 bps slippage per side, 20 bps round trip. Nine unchanged cost/slippage conditions. No parameter optimisation.

| Metric | 4H primary | 1D reference |
|---|---:|---:|
| Net cumulative return | 91.9% | 59.7% |
| Annualised Sharpe | 0.661 | 0.504 |
| Maximum drawdown | 51.6% | 53.9% |
| Trade count (engine) | 114 | 22 |
| Deflated Sharpe statistic | 0.904 | 0.840 |
| Mechanical verdict | Not Supported | Inconclusive |

4H: DSR 0.904 is below 0.95; failed matched-random timing and robustness (0/9 conditions meet its criterion). Annualised buy-and-hold Sharpe is approximately 0.601 versus strategy 0.661; the random-timing p95 is approximately 1.126. Buy-and-hold gross cumulative return over the matched OOS bars is 97.0%, with 77.0% drawdown. A slightly higher Sharpe than buy-and-hold is not the same as higher absolute return or evidence of skill.

Daily: only 22 trades versus minimum 30, DSR 0.840, and neither benchmark beaten. No supported strategy was found.

## Verification and limits

- Transition, equality-boundary and prior-bar exclusion tests passed; eight synthetic prefix/future checks and six real-data prefix checks passed. Input timestamps are gap-free, unique and OHLC-consistent in the retained sample.
- Independently recomputed position-shift, close returns and all turnover costs match saved OOS returns; equity and drawdown match both reports. Engine-source and runner hashes verified. No independent agent review was performed.
- MIE uses shifted close-to-close exposure, not next-open executable fills. Its trade accounting is the existing engine definition. No exchange liquidity, outage execution, funding or intrabar stop modelling; this is spot long/cash.
- Single asset; no cross-asset generalisation. v4 sampling uncertainty is explicitly not estimated. DSR is not a probability that the strategy will make money. The engine does not price all prior research or the two-timeframe family into its one-configuration trial count.
- Matched random timing uses 25 seeds and matches trade counts, not exposure/holding duration. Regime fields are not used to claim a tradable regime filter.
- Existing historical verdicts, INDEX, strategy registries on disk, H-025 and paper/forward state were not changed. Separate exploratory study; no automatic promotion.

## Reproduce

Use the preserved CSVs and manifests in a new output directory to avoid overwriting this run. `run_study.py check` verifies rule logic; `run_study.py run` calls MIE and intentionally refuses to overwrite existing results. The read-only engine path and pinned commit must be available. `fetch` preserves the original gap check; reproduce the data amendment with `prepare` after the raw download.

Raw results: SUMMARY.json, REPORT_4h.json, REPORT_1d.json; OOS returns in OOS_4h.csv and OOS_1d.csv. Provenance: RUN_MANIFEST.json, DATA_MANIFEST.json and VERIFICATION.json.
