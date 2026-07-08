# Strategy Dossier — H-020: RSI Strategy

**strategy_id:** H-020 · **source:** TradingView built-in ("RSI Strategy") · **date:** 2026-07-08
**class:** mean-reversion-oscillator · **status:** STEPs 1–4 complete; STEP 5 **pre-registered, pending real data**; verdict **Not Yet Tested**
**engine:** `app.strategy_research` · **frozen matrix:** `srp-matrix-v1` (`a740f3acbf6572d6`)

> ⚠️ Research only. No execution, no signals, no advice. A verdict is a statement about *evidence*.

---

## STEP 1 — Strategy Analysis

Canonical TradingView built-in logic (engine spec: `strategies/implementations.py::RSIStrategy`):

- **Indicators:** Wilder's `RSI(close, length)`.
- **Entry (long):** RSI crosses **above** the oversold level (buy the dip).
- **Entry (short):** RSI crosses **below** the overbought level (fade the rip).
- **Exit:** implicit; long/short, held until the opposite signal.
- **Filters:** none in the base rule.
- **Parameters (canonical defaults):** `length=14, oversold=30, overbought=70`.
- **Risk management / position sizing:** none; fixed full-notional.
- **Implicit assumptions (the real targets of testing):** (a) short-horizon returns mean-revert, so
  oversold precedes a bounce; (b) the market is **ranging**, not trending, when signals fire;
  (c) fills at close with modest cost; (d) 14/30/70 is not data-snooped.
- **Script-integrity check:** RSI uses only closed bars ⇒ **no look-ahead**; engine shifts positions
  forward one bar (verified).

## STEP 2 — Falsifiable Hypothesis

**H-020.** *The RSI(14, 30/70) mean-reversion rule produces positive risk-adjusted returns net of
realistic costs, and this edge (a) survives walk-forward out-of-sample, (b) generalizes across
assets, and (c) is not explained by the number of RSI parameterizations tried.*

**Kill criteria (pre-committed):** **Not Supported** if OOS **deflated Sharpe < 0.95**, **or** the
edge appears in a minority of assets/conditions, **or** it fails to beat **buy-and-hold** and
**matched random-timing (95th pct)** on risk-adjusted terms. **Graveyard** if OOS max drawdown
exceeds the 60% gate. *Directional prior (STEP 3): expect the edge, if any, to live only in ranging
regimes and to be eroded by costs.*

## STEP 3 — Research Review (evidence, not truth)

- **Jegadeesh (1990)** — *supports (mechanism):* strong negative short-horizon serial correlation —
  the reversal RSI tries to harvest is real in the data.
- **Lehmann (1990)** — *supports (mechanism):* weekly contrarian (buy losers) earns positive
  abnormal returns — overreaction/reversal exists.
- **Chong & Ng (2008)** — *supports (in-sample, gross):* RSI(21) overbought/oversold beat
  buy-and-hold on the FT30 — but one index, gross of costs.
- **Chong, Ng & Liew (2014)** — *mixed:* extended to five markets, RSI/MACD profitable in some
  (Milan, Frankfurt), not others — market-specific, not robust.
- **Lo & MacKinlay (1990)** — *mixed:* contrarian profits arise substantially from cross-stock
  lead-lag, so single-name own-reversal is overstated.
- **Giner & Zakamulin (2023)** — *mixed/contradicts:* returns switch between a trending and a
  mean-reverting regime ⇒ reversal pays only in the ranging regime and loses in trends.
- **Avramov, Chordia & Goyal (2006)** — *contradicts (decisive):* short-term reversal profits
  concentrate in illiquid high-turnover names and are essentially **eliminated by realistic costs**.

**Expected failure modes (predicted before testing):** gets run over in **trends** (oversold →
more oversold); **costs/bid-ask bounce** eat the edge; **threshold sensitivity** (30/70 vs 20/80);
edge lives in **illiquid** names and doesn't scale; regime-dependence.

## STEP 4 — Experiment Design (pre-registered; frozen before any run)

Executed through the **identical** `srp-matrix-v1` pipeline (hash `a740f3acbf6572d6`):

- **Data:** real OHLCV via swappable provider; multiple assets incl. some not used to select params.
- **Walk-forward:** 5-split expanding-window; params re-selected in-sample, evaluated OOS, stitched.
- **Parameter grid (data-snooping budget):** `length∈{7,14,21}, oversold∈{30}, overbought∈{70}`.
- **Costs:** {0,5,10} bps/side. **Slippage:** {0,5,10} bps. Headline = 5+5.
- **Benchmarks:** buy-and-hold **and** matched random-timing (25 seeds; beat 95th pct).
- **Regimes:** trend/range/crisis labelled ex-ante from price only — **critical here**, since the
  prior says RSI should work in range and fail in trend; regime-conditional results test that directly.
- **Primary metric (frozen):** OOS **deflated Sharpe** ≥ 0.95. **Secondary:** maxDD, profit factor,
  turnover, regime-conditional performance, parameter-stability, bootstrap Sharpe CI.
- **Multiplicity:** BH-FDR (q=0.05) across the 23-strategy universe.

**Registered; not alterable after seeing results.**

## STEP 5 — Scientific Evaluation

**Run:** provider `csv` (is_real=True) · request `b1cb98d98f2fd49a` · matrix `a740f3acbf6572d6` (`srp-matrix-v1`) · assets: AAPL, BTC/USD, ETH/USD, SPY, XAU/USD

| Metric (out-of-sample, headline cost+slippage) | Value |
|---|---|
| OOS Sharpe (annualised) | -0.284 |
| Deflated Sharpe (prob. edge is real) | 0.000 |
| Sharpe 95% CI (per-period) | [-0.035, 0.004] |
| Beats buy-and-hold | False |
| Beats matched random-timing (p95) | False |
| Max drawdown (OOS) | 100.0% |
| OOS trades / bars | 81 / 10112 |
| Robustness / Stability / Generalization | 0.000 / 0.574 / 0.000 |
| Confidence | 0.000 |

**Mechanical verdict:** **Graveyard** — cause of death: catastrophic drawdown out-of-sample

Reasons: OOS max drawdown 100.0% exceeds gate 60%
## STEP 5 · srp-matrix-v2 — Scientific Evaluation

**Run:** provider `csv` (is_real=True) · request `23c3767d3ddcf4c0` · matrix `4c2aa61eccfd276c` (`srp-matrix-v2`) · assets: AAPL, BTC/USD, ETH/USD, SPY, XAU/USD

| Metric (out-of-sample, headline cost+slippage) | Value |
|---|---|
| OOS Sharpe (annualised) | -0.499 |
| Deflated Sharpe (prob. edge is real) | 0.123 |
| Sharpe 95% CI (per-period) | [-0.045, 0.002] |
| Beats buy-and-hold | False |
| Beats matched random-timing (p95) | False |
| Max drawdown (OOS) | 66.2% |
| OOS trades / bars | 105 / 6000 |
| Robustness / Stability / Generalization | 0.000 / 0.620 / 0.000 |
| Confidence | 0.031 |

**Mechanical verdict:** **Graveyard** — cause of death: catastrophic drawdown out-of-sample

Reasons: OOS max drawdown 66.2% exceeds gate 60%


## STEP 6 — Knowledge-Base Record

| Field | Value |
|---|---|
| robustness_score | pending |
| stability_score | pending |
| generalization_score | pending |
| risk_adjusted | pending (deflated Sharpe / maxDD / profit factor / #trades) |
| failure_modes | *predicted:* run over in trends; cost/bid-ask erosion; threshold sensitivity; regime-dependence |
| confidence | pending |
| verdict | **Not Yet Tested** |
| similar_strategies | H-003, H-004 (Bollinger), H-009 (Keltner), H-021 (Stochastic) |
| literature | Jegadeesh 1990, Lehmann 1990, Chong–Ng 2008 (support); Chong–Ng–Liew 2014, Lo–MacKinlay 1990, Giner–Zakamulin 2023 (mixed); Avramov–Chordia–Goyal 2006 (contradicts) |
| provenance_hash | assigned at STEP 5 run |

## STEP 7 — Contribution to Learning

Bears on **M-003** (*mean-reversion oscillator/band rules are strongly regime-dependent*). The
regime-conditional OOS result is the key evidence; H-020 votes alongside H-003, H-004, H-009, H-021.

**Cross-asset generalization (5 markets, 2026-07-08)** — per-asset OOS Sharpe (ann., 5+5 bps):
AAPL **−0.57**, BTC **−0.47**, ETH **+0.07**, SPY **−0.42**, XAU **−1.18** → **1/5 positive** (no
cross-market edge). Per-regime (pooled): trend **−1.03**, range **+0.38**, crisis **−0.13** — the
**mirror image** of the momentum rules, **directly confirming M-003**: RSI mean-reversion pays only
in ranging markets and is run over in trends, exactly as Giner–Zakamulin predict. See
`CROSS_ASSET_GENERALIZATION.md`.

---

**Sources:**
- Jegadeesh (1990), *Evidence of Predictable Behavior of Security Returns*, J. Finance 45(3) — https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1990.tb05110.x
- Lehmann (1990), *Fads, Martingales, and Market Efficiency*, QJE 105(1) — https://academic.oup.com/qje/article-abstract/105/1/1/1928416
- Chong & Ng (2008), *Testing the MACD and RSI rules using the FT30*, Applied Economics Letters 15(14) — https://www.tandfonline.com/doi/full/10.1080/13504850600993598
- Chong, Ng & Liew (2014), *Revisiting the Performance of MACD and RSI Oscillators*, JRFM 7(1) — https://www.mdpi.com/1911-8074/7/1/1
- Lo & MacKinlay (1990), *When Are Contrarian Profits Due to Stock Market Overreaction?*, RFS 3(2) — https://academic.oup.com/rfs/article-abstract/3/2/175/1595488
- Giner & Zakamulin (2023), *A regime-switching model of stock returns with momentum and mean reversion*, Economic Modelling 122 — https://www.sciencedirect.com/science/article/pii/S0264999323000494
- Avramov, Chordia & Goyal (2006), *Liquidity and Autocorrelations in Individual Stock Returns*, J. Finance 61(5) — https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.2006.01060.x
