# BTC chart patterns — mie-chart-patterns-v1.1

Historical BTCUSDT perpetuals, 4H. Evaluation 2021–2024; warmup from 2020-02-20.
Five fixed rules, no tuning; next-open fills, 1x entry notional, 2R target, 7-day timeout, actual funding.
Headline fees 5 bps + slippage 5 bps per side. Intrabar ambiguity resolved stop-first.
This is a new exploratory evaluator, not srp-matrix-v4 and not a live/promotion verdict.

| Pattern | Net return | Sharpe | Max drawdown | Trades | Funding USD | Holm timing p | Screen |
|---|---:|---:|---:|---:|---:|---:|---|
| double_bottom | 27.1% | 0.54 | 17.5% | 10 | -173.67 | 0.200 | underpowered |
| double_top | -17.1% | -0.43 | 17.9% | 7 | 115.69 | 1.000 | underpowered |
| inverse_head_shoulders | -20.1% | -0.59 | 31.0% | 4 | -33.40 | 1.000 | underpowered |
| head_shoulders | 0.0% | 0.00 | 0.0% | 0 | 0.00 | 1.000 | underpowered |
| symmetric_triangle | 0.0% | 0.00 | 0.0% | 0 | 0.00 | 1.000 | underpowered |

Buy-and-hold SAME perpetual: -102.5% net, Sharpe undefined (capital exhausted), max drawdown 101.3%.
A capital-exhausted benchmark cannot establish Sharpe superiority. Funding drains collateral in this fixed-quantity model; this is NOT a spot buy-and-hold result or a validated exchange-liquidation simulation.

## Limits

- Previously available and partly seen history; no pristine holdout or prospective evidence.
- One exact operational definition per shape; results do not reject all human/chart-pattern interpretations.
- Funding and fill accounting differ from existing MIE matrices. No historical results overwritten.
- 4,048 older funding marks use historical mark-candle open at the scheduled instant; reported timestamp jitter up to 47 ms normalized to the 8h boundary. These are explicit proxies/conventions, not exact millisecond settlement reconstruction.
- Timing controls condition on realised trade durations (intrabar exits rounded to bars), do not replay pattern stops/targets and use only 199 samples. Holm correction covers these five, not all previous research.
- No leverage beyond initial 1x notional; no exchange liquidation, order book, outage or capacity model. Stop-first is a conservative convention, not reconstructed intrabar execution.
- Funding, fees and realised price P&L reconciled to equity for each account. Dedicated local source review; no independent agent review or production verification.
- Sources/parameters are hashed in REGISTRATION.json, data in DATA_MANIFEST.json. See docs/BTC_CHART_PATTERNS_V1_PLAN.md and per-trade ledgers.
