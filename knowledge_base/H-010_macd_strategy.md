# Strategy Dossier — H-010: MACD Strategy

**strategy_id:** H-010 · **source:** TradingView built-in ("MACD Strategy") · **date:** 2026-07-08
**class:** trend-crossover · **status:** STEPs 1–4 complete; STEP 5 **pre-registered, pending real data**; verdict **Not Yet Tested**
**engine:** `app.strategy_research` · **frozen matrix:** `srp-matrix-v1` (`a740f3acbf6572d6`)

> ⚠️ Research only. No execution, no signals, no advice. The evaluation is a statement about
> *evidence*, never a recommendation to trade.

---

## STEP 1 — Strategy Analysis

Canonical TradingView built-in logic (the engine implementation in
`strategies/implementations.py::MACDStrategy` **is** the frozen, auditable spec):

- **Indicators:** MACD line = `EMA(close, fast) − EMA(close, slow)`; signal = `SMA(MACD, signal)`;
  histogram = MACD − signal.
- **Entry (long):** histogram crosses **above** 0 (MACD line crosses above its signal line).
- **Entry (short):** histogram crosses **below** 0.
- **Exit:** implicit — the strategy is always in the market and reverses on the opposite cross
  (long/short, stop-and-reverse).
- **Filters:** none in the base rule.
- **Parameters (canonical defaults):** `fast=12, slow=26, signal=9`; bar timeframe.
- **Risk management / position sizing:** none; fixed full-notional exposure.
- **Implicit assumptions (the real targets of testing):** (a) trends persist long enough to
  overcome the crossover's lag; (b) the histogram sign carries information beyond autocorrelation
  already priced in; (c) fills at close with modest cost; (d) the 12/26/9 triple is not a
  data-snooped artifact.
- **Script-integrity check:** EMAs/SMA use only closed bars ⇒ **no look-ahead**. The engine
  additionally *shifts positions forward one bar*, so same-bar peeking is structurally impossible
  (verified by `test_no_lookahead_*`).

## STEP 2 — Falsifiable Hypothesis

**H-010.** *The MACD(12,26,9) crossover produces positive risk-adjusted returns net of realistic
costs, and this edge (a) survives walk-forward out-of-sample, (b) generalizes across assets not
used to select parameters, and (c) is not explained by the number of MACD parameterizations tried.*

**Kill criteria (pre-committed):** **Not Supported** if, after the data-snooping correction, the
out-of-sample **deflated Sharpe < 0.95**, **or** the OOS edge appears in a minority of
assets/conditions, **or** it fails to beat **buy-and-hold** and **matched random-timing (95th pct)**
on risk-adjusted terms. **Graveyard** if OOS max drawdown exceeds the 60% gate.

## STEP 3 — Research Review (evidence, not truth)

The moving-average/MACD crossover is among the most-studied rule classes; the literature is
largely adversarial, which is a gift.

- **Brock, Lakonishok & LeBaron (1992)** — *supports (in-sample):* MA-crossover and trading-range
  rules had significant predictive power on the DJIA 1897–1986, before costs.
- **Chong & Ng (2008)** — *supports (MACD-specific, in-sample, gross):* MACD and RSI rules beat
  buy-and-hold on the FT30 over ~60 years, costs not fully modelled.
- **Chio (2022)** — *mixed:* standalone MACD crossover shows sub-50% win rate; improves only with
  RSI/MFI/volume filters.
- **Sullivan, Timmermann & White (1999)** — *contradicts (decisive):* correcting for the universe
  of rules searched, the best rules' superiority weakens sharply and does not persist post-1986.
- **Zakamulin (2014)** — *contradicts (OOS/real-life):* genuine walk-forward MA/momentum timing
  falls far below in-sample backtests and generally fails to beat buy-and-hold.
- **Bajgrowicz & Scaillet (2012)** — *contradicts (costs+FDR):* after false-discovery control and
  realistic costs, technical-rule profits vanish and do not persist.
- **Park & Irwin (2007)** — *mixed (survey):* ~56/95 studies found profitability, but flag
  data-snooping and a clear decline in equity-market profitability after the late 1980s.

**Expected failure modes (predicted before testing):** whipsaw in ranges; crossover **lag** giving
back gains at reversals; edge **concentrated in a few regimes**; sensitivity to 12/26/9 and to
costs/turnover; degradation out-of-sample and over time.

## STEP 4 — Experiment Design (pre-registered; frozen before any run)

Executed by the Market Intelligence Engine through the **identical** `srp-matrix-v1` pipeline used
for every strategy (hash `a740f3acbf6572d6`):

- **Data:** real OHLCV via a swappable provider (CSV / Binance / FMP / CoinDesk), across multiple
  assets **including assets not used to select parameters** (generalization).
- **Walk-forward:** 5-split expanding-window; parameters re-selected in-sample each fold, evaluated
  out-of-sample; OOS segments stitched into the only curve the verdict trusts.
- **Parameter grid (data-snooping budget):** `fast∈{8,12}, slow∈{21,26}, signal∈{9}` — priced into
  the deflated Sharpe.
- **Transaction costs:** {0, 5, 10} bps/side. **Slippage:** {0, 5, 10} bps. Headline scenario = 5+5.
- **Benchmarks:** buy-and-hold **and** matched random-timing (25 seeds; must beat the 95th pct).
- **Regimes:** trend / range / crisis labelled ex-ante from price only (never by outcome).
- **Primary metric (frozen):** OOS **deflated Sharpe** ≥ 0.95. **Secondary:** max drawdown, profit
  factor, turnover, regime-conditional performance, parameter-stability surface, bootstrap Sharpe CI.
- **Multiplicity:** Benjamini–Hochberg FDR (q=0.05) across the universe of 23 strategies.

**Registered; not alterable after seeing results.** Run:
`python -m app.strategy_research.run --strategy H-010 --provider csv --assets ... --timeframe 1d --write`

## STEP 5 — Scientific Evaluation

**Run:** provider `csv` (is_real=True) · request `cb21d5ed1234e6eb` · matrix `a740f3acbf6572d6` (`srp-matrix-v1`) · assets: AAPL, BTC/USD, ETH/USD, SPY, XAU/USD

| Metric (out-of-sample, headline cost+slippage) | Value |
|---|---|
| OOS Sharpe (annualised) | 0.099 |
| Deflated Sharpe (prob. edge is real) | 0.011 |
| Sharpe 95% CI (per-period) | [-0.014, 0.024] |
| Beats buy-and-hold | False |
| Beats matched random-timing (p95) | False |
| Max drawdown (OOS) | 97.4% |
| OOS trades / bars | 887 / 10112 |
| Robustness / Stability / Generalization | 0.000 / 0.458 / 0.219 |
| Confidence | 0.003 |

**Mechanical verdict:** **Graveyard** — cause of death: catastrophic drawdown out-of-sample

Reasons: OOS max drawdown 97.4% exceeds gate 60%
## STEP 5 · srp-matrix-v2 — Scientific Evaluation

**Run:** provider `csv` (is_real=True) · request `cc7159ec2f31f4c2` · matrix `4c2aa61eccfd276c` (`srp-matrix-v2`) · assets: AAPL, BTC/USD, ETH/USD, SPY, XAU/USD

| Metric (out-of-sample, headline cost+slippage) | Value |
|---|---|
| OOS Sharpe (annualised) | -0.138 |
| Deflated Sharpe (prob. edge is real) | 0.321 |
| Sharpe 95% CI (per-period) | [-0.028, 0.024] |
| Beats buy-and-hold | False |
| Beats matched random-timing (p95) | False |
| Max drawdown (OOS) | 55.7% |
| OOS trades / bars | 548 / 6000 |
| Robustness / Stability / Generalization | 0.000 / 0.464 / 0.000 |
| Confidence | 0.080 |

**Mechanical verdict:** **Not Supported**

Reasons: failed: deflated Sharpe clears bar; failed: OOS Sharpe positive; failed: beats buy-and-hold (risk-adjusted); failed: beats matched random-timing; failed: robust across majority of conditions


## STEP 6 — Knowledge-Base Record

| Field | Value |
|---|---|
| robustness_score | pending |
| stability_score | pending |
| generalization_score | pending |
| risk_adjusted | pending (deflated Sharpe / maxDD / profit factor / #trades) |
| failure_modes | *predicted:* whipsaw; reversal lag; regime-concentration; cost sensitivity |
| confidence | pending |
| verdict | **Not Yet Tested** |
| similar_strategies | H-012, H-013 (MA crosses); H-015, H-022 (trend); H-001 (EMA cross) |
| literature | BLR 1992 (supports, in-sample); Chong–Ng 2008 (supports, gross); STW 1999, Zakamulin 2014, Bajgrowicz–Scaillet 2012 (contradict) |
| provenance_hash | assigned at STEP 5 run (request+matrix hash) |

## STEP 7 — Contribution to Learning

Bears on **M-001** (*lagging trend/crossover rules show low OOS robustness once search cost is
priced in*). H-010's OOS result will be one vote for or against M-001, alongside H-001, H-012,
H-013, H-015, H-022.

**Cross-asset generalization (5 markets, 2026-07-08)** — per-asset OOS Sharpe (ann., 5+5 bps):
AAPL **+0.27**, BTC **+0.17**, ETH **+0.30**, SPY **−0.39**, XAU **−0.86** → **3/5 positive** (trendy
assets only; fails on index/gold). Per-regime (pooled): trend **+1.79**, range **−1.37**, crisis
**−0.35** — a textbook momentum signature. M-001 supported, but the weakness is **regime-gated, not
uniform**. See `CROSS_ASSET_GENERALIZATION.md`.

---

**Sources:**
- Brock, Lakonishok & LeBaron (1992), *Simple Technical Trading Rules…*, J. Finance 47(5) — https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1992.tb04681.x
- Chong & Ng (2008), *Testing the MACD and RSI rules using the FT30*, Applied Economics Letters 15(14) — https://www.tandfonline.com/doi/full/10.1080/13504850600993598
- Chio (2022), *A Comparative Study of MACD-Based Trading Strategies*, arXiv:2206.12282 — https://arxiv.org/abs/2206.12282
- Sullivan, Timmermann & White (1999), *Data-Snooping, Technical Trading Rule Performance, and the Bootstrap*, J. Finance 54(5) — https://onlinelibrary.wiley.com/doi/abs/10.1111/0022-1082.00163
- Zakamulin (2014), *The Real-Life Performance of Market Timing…*, J. Asset Management 15(4) — https://link.springer.com/article/10.1057/jam.2014.25
- Bajgrowicz & Scaillet (2012), *Technical Trading Revisited…*, J. Financial Economics 106(3) — https://www.sciencedirect.com/science/article/abs/pii/S0304405X1200116X
- Park & Irwin (2007), *What Do We Know About the Profitability of Technical Analysis?*, J. Economic Surveys 21(4) — https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-6419.2007.00519.x
