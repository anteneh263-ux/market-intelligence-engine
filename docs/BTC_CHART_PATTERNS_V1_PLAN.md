# BTC chart-pattern study v1 — authorised 2026-10-27

## Authority and architecture

User explicitly authorised extending MIE for all five patterns with next-open fills
and historical funding. This document is the scoped architecture decision and
pre-result research contract. Version: `mie-chart-patterns-v1`. It is an additive
experimental historical evaluator under `app.strategy_research`, not a change to
srp-matrix-v1..v5, a live broker, or Phase 5/forward authority. No old verdicts are
rewritten. The source checkout baseline is f04e11c468f71327d1117a43a9096eab8e6eb25b.

The study owns only `knowledge_base/studies/BTC_CHART_PATTERNS_2026-10-27/`.
No store constructors, database writes, global catalogue additions or migrations.
Raw data, source hashes, rules, validation and results are kept there. Runs refuse
overwrites. Production promotion/merge remains separate from this exploratory run.
Failure of data or accounting checks stops evaluation, never substitutes evidence.

## Fixed universe and data

Five rules: double bottom; double top; inverse head-and-shoulders;
head-and-shoulders; symmetric triangle (both breakout directions in ONE rule).
One configuration each, total family budget five. No optimisation or parameter
changes after seeing results. These are explicit operational definitions, not
replicas of TradingView's proprietary Auto Chart Patterns implementation.

All five use Binance BTCUSDT USD-M perpetual 4h candles plus actual funding
timestamps, rates and associated mark prices. Using perpetuals for all five keeps
execution/funding comparable. It is not a BTCUSD spot replication.
Inputs: 2020-02-20 00:00 UTC through 2024-12-31 20:00 UTC; no 2025+ or holdout
files accessed. Evaluation: 2021-01-01 through 2024-12-31, start flat; earlier bars
are warmup only. This is previously available/partly seen research history, NOT a
new untouched holdout. Year-by-year results are descriptive, no model selection.
Require unique complete 4h timestamps and complete regular 8h funding timestamps
in this selected historical interval; missing mark prices or irregular schedules
stop the run for investigation. Never fill missing rates with later observations.

## Causal pattern definitions

- Strict 5-left/5-right pivot HIGH/LOW; ties are not pivots. Pivot at i is available
  only at close i+5. If both high and low qualify on a bar, discard that pivot.
- Keep alternating pivots; a more extreme same-kind pivot replaces only the
  latest pivot in the current state, never historical signals.
- Use the latest 3/5/6 pivots respectively. First-to-last span 12..120 bars;
  pattern expires 30 bars after last pivot. One breakout signal per pivot tuple.
- ATR = trailing 14-bar arithmetic mean of true range, available at signal close.
  Pattern height >= 2 ATR; double/shoulder mismatch <= 0.5 ATR.
- Double bottom: L-H-L; bottoms within tolerance; horizontal neckline at H;
  close crosses upward through neckline. Double top: exact H-L-H mirror.
- H&S: H-L-H-L-H; middle head exceeds BOTH shoulders by >= 1 ATR, shoulders
  within tolerance; neckline is line through the two lows. Break below line.
  Inverse H&S is the exact vertical mirror.
- Preceding trend for four reversal patterns: close at first pivot is lower
  (bullish) / higher (bearish) than close 20 bars earlier. Require those bars.
- Triangle: latest six alternating pivots contain three strictly declining highs
  and three strictly rising lows. Lines join first/last high and first/last low;
  middle points within 0.5 ATR of their respective line. Positive width at first
  pivot and signal, width at signal <= 75% initial width; first width >= 2 ATR.
  Break above upper or below lower line BEFORE projected intersection.
- Breakout requires prior close on/inside boundary and current close outside;
  all required pivots must be confirmed by current close. Equality is no break.
- Stop: beyond lowest/highest pattern extreme by 0.1 ATR. Triangle stop beyond
  opposite line at signal by 0.1 ATR. Stop fixed at entry, not redrawn later.

## Execution and accounting

- Signal at close t; fill earliest at open t+1 with adverse slippage. One position
  per pattern account, no pyramiding or same-bar reentry, no borrowed leverage.
- Entry notional = current equity (1x), quantity fixed until exit. Fee charged
  on actual fill notional at BOTH entry and exit. No interest on idle cash.
- Cancel entry if open is already through stop. Target = 2 times initial
  entry-to-stop risk. Protective stop/target active on entry bar.
- Gap through stop fills at open with adverse slippage. Target limit fills at
  target (no favourable gap improvement). If stop and target both touched in one
  4h candle, stop wins. No claim this reconstructs true intrabar ordering.
- Exit remaining trade at open after 42 held bars (seven days). Force final exit
  at last close; include fees/slippage. No carried pre-test positions.
- Funding events settle BEFORE open orders/exits at the same timestamp against
  quantity held just before settlement: cash -= signed_quantity * mark * rate.
  Positive rate costs longs and benefits shorts. No charge to new entry at the
  same timestamp. Complete actual historical rates, no constant-rate proxy.
- Mark equity at candle close; nonpositive equity terminates the simulation as
  bankrupt, without creating negative subsequent positions.
- Fees {0,5,10} bps × slippage {0,5,10} bps, headline 5+5 per side. Funding included
  at all nine conditions. 4h bars used for fills, daily equity returns for Sharpe.
- No exchange liquidation/order-book model; 1x plus stops reduces but does not
  remove gap, outage and counterparty risk. Funding timestamps at bar boundaries
  are required. New model is not numerically equivalent to old close-return MIE.

## Comparisons and multiplicity

- Report net return, daily annualised Sharpe (sqrt365), drawdown INCLUDING initial
  equity, completed trades, fees, funding, exposure, holding time and yearly P&L.
- Compare with cash and 1x buy-and-hold of SAME perpetual including funding/fees.
- Timing control: 199 seeded random reallocations of the strategy's realised
  signed trade durations and flat gaps over OOS. Preserve count, direction per
  trade and durations; reprice fixed-notional-at-entry trades at actual opens
  with identical fees/funding, final close where needed. No pattern stops in
  timing controls: a conditional timing diagnostic, not an identical exit rule.
- p = (1 + controls with Sharpe >= observed Sharpe)/200, Holm across all five.
  Do not present this conditional diagnostic as universal proof of skill.
- Study pass requires >=30 completed trades, positive net return, drawdown <=60%,
  headline Sharpe above buy-and-hold, Holm-adjusted timing p<=.05 and positive
  net return in >=5/9 cost conditions. This is a NEW exploratory screen, NOT an
  inherited MIE Supported verdict or evidence of live profitability.
- Fewer than 30 trades => underpowered; failed economics => not qualified.
  No threshold relaxation if nothing passes. No new variants on these data.

## Verification and review

Synthetic tests must cover positive/negative cases for all five geometries,
confirmation delay and prefix/future invariance, next-open gaps, stop/target
ambiguity, long/short fee accounting, positive/negative funding signs, same-time
funding ownership, time exit, final liquidation, initial-equity drawdown and Holm.
Dedicated source review before real evaluation; record findings and fixes.
Independent agent review has NOT been authorised; do not claim one occurred.
Persist per-trade ledger and verify total net P&L = price P&L - fees + funding.

References (definitions/data interfaces, not evidence these rules are profitable):
- https://www.tradingview.com/support/solutions/43000690659-chart-pattern-double-bottom/
- https://developers.binance.info/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data
