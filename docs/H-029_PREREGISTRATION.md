# H-029 pre-registration

**Name:** Volatility-Gated Time-Series Momentum  
**State:** pre-registered; not yet research-evaluated  
**Scope:** research only; no automatic forward test, broker or orders

## Claim

The sign of medium-term own-price momentum may persist across BTC/USD, ETH/USD,
SPY and XAU/USD. Moving to cash when recent realised volatility is unusually high
relative to its own trailing baseline may reduce drawdown without introducing an
external input.

## Frozen rule

- Input: `close` only.
- Direction: long/short/cash, discrete positions in `{-1, 0, +1}`.
- Momentum: `close / close.shift(trend_lookback) - 1`.
- Recent volatility: trailing sample standard deviation of close returns over
  `volatility_lookback` bars.
- Baseline volatility: trailing 252-observation mean of recent volatility.
- Long when momentum is positive and recent volatility is at most 1.5 times baseline.
- Short when momentum is negative and the same gate permits exposure.
- Cash during warm-up, zero momentum, or volatility above the gate.
- Signal on completed close; execution no earlier than the next bar.

## Frozen parameters

Default: trend 252, recent volatility 60, volatility baseline 252, multiplier 1.5.

Pre-registered grid: trend `{126, 252}` × recent volatility `{30, 60}`;
baseline `252` and multiplier `1.5` fixed. Total grid budget: four configurations.

## Frozen evaluation

- Assets: BTC/USD, ETH/USD, SPY, XAU/USD.
- Timeframe: 1d.
- Matrix: srp-matrix-v2.
- Headline costs: 5 bps fee plus 5 bps slippage.
- Existing promotion thresholds remain unchanged.
- Target-only control: CTRL-TARGET-ONLY.

The rule, grid, assets and thresholds must not change after the first result is
viewed. Any change is a new hypothesis and a new economic trial. A successful batch
does not start forward testing automatically.
