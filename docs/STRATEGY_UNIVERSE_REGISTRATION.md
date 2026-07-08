# Pre-Registered Strategy Universe — TradingView Built-in Library

**registered:** 2026-07-08 · **source:** TradingView built-in Strategies tab (read live) ·
**universe size N = 23** · **status:** FROZEN before any STEP-5 evaluation

> ⚠️ Research only. No execution, no signals, no advice.

## Why this document exists (the load-bearing act)

The single most important defense against fooling ourselves when we test *many* strategies is to
commit to the **full set of things we will test — and how many there are — before we look at any
result.** That number is the **data-snooping budget**. It is what the deflated-Sharpe
(Bailey–López de Prado) and Benjamini–Hochberg FDR corrections consume: a strategy is only
credited with an edge if it survives a bar that has *already priced in all 23 attempts*.

Registering the universe here, in advance, is what lets us later say "3 of 23 survived correction"
and mean it. If we grew the list opportunistically after seeing results, every later correction
would be a lie. **N = 23 is now frozen.** Adding a strategy later starts a *new*, separately-tracked
registration with its own budget — it does not silently expand this one.

## The universe (N = 23)

| id | strategy (TradingView) | class | primary indicator(s) | bears on meta |
|---|---|---|---|---|
| H-002 | BarUpDn Strategy | Price-action momentum | bar up/down count | M-002 |
| H-003 | Bollinger Bands Strategy | Mean-reversion (bands) | Bollinger Bands | M-003 |
| H-004 | Bollinger Bands Strategy directed | Mean-reversion (directional) | Bollinger Bands | M-003 |
| H-005 | ChannelBreakOutStrategy | Breakout | Donchian/price channel | M-004 |
| H-006 | Consecutive Up/Down Strategy | Price-action momentum | consecutive closes | M-002 |
| H-007 | Greedy Strategy | Price-action | prior bar logic | M-002 |
| H-008 | InSide Bar Strategy | Price-action pattern | inside bar | M-005 |
| H-009 | Keltner Channels Strategy | Mean-reversion / breakout | Keltner Channels (ATR) | M-003 |
| H-010 | MACD Strategy | Trend / momentum crossover | MACD | M-001 |
| H-011 | Momentum Strategy | Momentum | momentum oscillator | M-002 |
| H-012 | MovingAvg2Line Cross | Lagging trend crossover | 2 moving averages | M-001 |
| H-013 | MovingAvg Cross | Lagging trend (price vs MA) | 1 moving average | M-001 |
| H-014 | OutSide Bar Strategy | Price-action pattern | outside bar | M-005 |
| H-015 | Parabolic SAR Strategy | Trend-following (stop-and-reverse) | Parabolic SAR | M-001 |
| H-016 | Pivot Extension Strategy | Price-action / pivots | pivot points | M-005 |
| H-017 | Pivot Reversal Strategy | Reversal | pivot points | M-005 |
| H-018 | Price Channel Strategy | Breakout | price channel | M-004 |
| H-019 | Rob Booker - ADX Breakout | Trend-strength breakout | ADX | M-004 |
| H-020 | RSI Strategy | Mean-reversion (oscillator) | RSI | M-003 |
| H-021 | Stochastic Slow Strategy | Mean-reversion (oscillator) | Stochastic | M-003 |
| H-022 | Supertrend Strategy | Trend-following (ATR bands) | Supertrend (ATR) | M-001 |
| H-023 | Technical Ratings Strategy | Composite signal | aggregate technicals | M-006 |
| H-024 | Volty Expan Close Strategy | Volatility breakout | ATR / volatility expansion | M-004 |

*H-001 (EMA 50/200 crossover) is the reference dossier that locked the format; it also bears on
M-001. The 23 above are the built-in universe.*

## Meta-hypotheses this universe is designed to test (STEP 7)

The point of testing all 23 is not to find "the winner" — it is to let *classes* of strategy vote
on general claims about robustness:

- **M-001** — *Lagging trend/crossover rules* (MACD, MA crosses, Parabolic SAR, Supertrend) show
  low out-of-sample robustness once search cost is priced in. (seeded by H-001)
- **M-002** — *Simple price-action momentum rules* (BarUpDn, Consecutive, Greedy, Momentum) do not
  survive costs/turnover.
- **M-003** — *Mean-reversion oscillator/band rules* (Bollinger, Keltner, RSI, Stochastic) are
  strongly regime-dependent (work in ranges, fail in trends).
- **M-004** — *Breakout/volatility rules* (ChannelBreakOut, Price Channel, ADX, Volty) concentrate
  their edge in a few high-volatility episodes.
- **M-005** — *Bar-pattern rules* (InSide, OutSide, Pivots) have too few, too-noisy signals to
  establish a credible edge.
- **M-006** — *Composite/aggregate signals* (Technical Ratings) do not outperform their best single
  component after correction.

Each of these is decided only when enough member strategies reach a STEP-5 verdict. All are **Open**
today (0 strategies tested).

## Frozen evaluation rules (apply identically to all 23)

- **Primary metric:** out-of-sample **deflated Sharpe** vs benchmarks (buy-and-hold + random-timing,
  matched trade count).
- **Multiplicity correction:** Benjamini–Hochberg FDR across the full N = 23 (plus deflated Sharpe
  per strategy that prices in parameter variants tried).
- **Matrix (frozen, identical per strategy):** multiple assets incl. assets not used to design the
  rule; regimes {trend/range/crisis} labelled ex-ante; volatility {low/high}; walk-forward OOS
  splits; transaction costs {0, realistic, stressed} bps/side; slippage {none, 1×, 2×}.
- **Verdict categories:** Supported / Not Supported / Inconclusive / Graveyard.
- **Data-integrity constraint (absolute):** STEP 5 runs on **real OHLCV only**. TradingView Strategy
  Tester output is treated as an **in-sample claim to be falsified**, never as evidence.

## Sequencing (STEP 1–4 vs STEP 5)

STEP 1–4 (analysis, hypothesis, literature, pre-registered design) need **no market data** and will
be produced per strategy as dossiers `H-002…H-024`. STEP 5 (evaluation) is gated on real OHLCV per
the data-integrity constraint and runs as one correction-controlled batch across the universe so the
multiplicity math is honest.

---
*The universe is the unit of honesty here: 23 declared in advance, corrected as 23, reported as 23.*
