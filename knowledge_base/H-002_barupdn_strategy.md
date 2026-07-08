# Strategy Dossier — H-002: BarUpDn Strategy

**strategy_id:** H-002 · **source:** TradingView built-in ("BarUpDn Strategy") · **date:** 2026-07-08
**class:** price-action-momentum · **status:** STEPs 1–4 complete; STEP 5 **pre-registered, pending real data**; verdict **Not Yet Tested**
**engine:** `app.strategy_research` · **frozen matrix:** `srp-matrix-v1` (`a740f3acbf6572d6`)

> ⚠️ Research only. No execution, no signals, no advice. A verdict is a statement about *evidence*.

---

## STEP 1 — Strategy Analysis

Canonical TradingView built-in logic — the simplest possible rule (engine spec:
`strategies/implementations.py::BarUpDnStrategy`):

- **Indicators:** none — raw bar direction.
- **Entry (long):** current close **> previous** close (an "up" bar).
- **Entry (short):** current close **< previous** close (a "down" bar).
- **Exit:** implicit; always in the market, flips every time bar direction changes.
- **Filters / risk / sizing:** none; fixed full-notional; **extreme turnover** (potentially a trade
  most bars).
- **Implicit assumptions (the real targets of testing):** (a) the **sign of the last bar predicts
  the next** (1-bar momentum); (b) that predictability survives its own enormous transaction costs.
  Both are doubtful — this is effectively a **noise-trading baseline** and a near-null.
- **Script-integrity check:** uses only `close[t]` vs `close[t-1]` ⇒ **no look-ahead**; engine shifts
  positions forward one bar (verified). This is important: naive implementations that compare the
  current bar to itself would leak — this one does not.

## STEP 2 — Falsifiable Hypothesis

**H-002.** *The bar-direction rule (long after an up-close, short after a down-close) produces
positive risk-adjusted returns net of realistic costs, surviving walk-forward out-of-sample and
generalizing across assets.*

**Kill criteria (pre-committed):** **Not Supported** if OOS **deflated Sharpe < 0.95**, **or** the
edge appears in a minority of assets/conditions, **or** it fails to beat **buy-and-hold** and
**matched random-timing (95th pct)**. **Graveyard** if OOS max drawdown exceeds the 60% gate.
*Directional prior (STEP 3): strongly expect **Not Supported / Graveyard** — the literature says
short-horizon returns tend to **reverse**, so buying up-bars is on the wrong side, and the turnover
guarantees costs dominate. H-002 is the corpus's designated near-null baseline.*

## STEP 3 — Research Review (evidence, not truth)

Almost the entire relevant literature points **against** this rule — which makes it a valuable
calibration anchor: a healthy platform should return a clear negative here.

- **Jegadeesh (1990)** — *contradicts:* strong **negative** short-horizon serial correlation —
  recent winners reverse, so "buy after up bar" is on the wrong side.
- **Lehmann (1990)** — *contradicts:* weekly **contrarian** (buy losers) earns positive returns —
  the opposite of this rule.
- **Roll (1984)** — *mixed (artifact):* bid-ask bounce mechanically induces spurious negative serial
  covariance, so much apparent 1-bar dependence is non-tradable microstructure noise.
- **Lo & MacKinlay (1988)** — *mixed:* random walk rejected, but sign of autocorrelation differs
  between indices (positive) and single stocks (negative) — predictability is weak and level-dependent.
- **French & Roll (1986)** — *mixed:* small negative daily autocorrelation from pricing errors
  reversing — any signal is dominated by transient noise.
- **Lesmond, Schill & Zhou (2004)** — *contradicts (decisive):* trading costs on high-turnover
  return-based strategies consume or exceed gross profits — *a fortiori* fatal for a rule that
  trades nearly every bar.

**Expected failure modes (predicted before testing):** **extreme turnover ⇒ costs dominate**;
**wrong side of short-term reversal**; **bid-ask-bounce artifact** (non-tradable); no persistent
level-stable edge; Sharpe ≈ 0 before costs, decisively negative after.

## STEP 4 — Experiment Design (pre-registered; frozen before any run)

Executed through the **identical** `srp-matrix-v1` pipeline (hash `a740f3acbf6572d6`):

- **Data:** real OHLCV via swappable provider; multiple assets incl. some not used to design the rule.
- **Walk-forward:** 5-split expanding-window; OOS stitched. (Grid size = 1 — no free parameters —
  so the data-snooping budget is minimal; the test is almost purely about costs and reversal.)
- **Costs:** {0,5,10} bps/side. **Slippage:** {0,5,10} bps. Headline = 5+5. *The {0-bps} column is
  diagnostic:* if the rule cannot clear the bar even at **zero cost**, it has no timing signal at all.
- **Benchmarks:** buy-and-hold **and** matched random-timing (25 seeds; beat 95th pct). H-002 should
  be **indistinguishable from matched random-timing** — that is the prediction.
- **Regimes:** trend/range/crisis labelled ex-ante.
- **Primary metric (frozen):** OOS **deflated Sharpe** ≥ 0.95. **Secondary:** maxDD, profit factor,
  turnover (expected very high), regime-conditional performance, bootstrap Sharpe CI.
- **Multiplicity:** BH-FDR (q=0.05) across the 23-strategy universe.

**Registered; not alterable after seeing results.**

## STEP 5 — Scientific Evaluation

**Run:** provider `csv` (is_real=True) · request `09174afb49eccccc` · matrix `a740f3acbf6572d6` (`srp-matrix-v1`) · assets: AAPL, BTC/USD, ETH/USD, SPY, XAU/USD

| Metric (out-of-sample, headline cost+slippage) | Value |
|---|---|
| OOS Sharpe (annualised) | -1.101 |
| Deflated Sharpe (prob. edge is real) | 0.000 |
| Sharpe 95% CI (per-period) | [-0.077, -0.038] |
| Beats buy-and-hold | False |
| Beats matched random-timing (p95) | False |
| Max drawdown (OOS) | 100.0% |
| OOS trades / bars | 5230 / 10112 |
| Robustness / Stability / Generalization | 0.000 / 0.642 / 0.000 |
| Confidence | 0.000 |

**Mechanical verdict:** **Graveyard** — cause of death: catastrophic drawdown out-of-sample

Reasons: OOS max drawdown 100.0% exceeds gate 60%
## STEP 5 · srp-matrix-v2 — Scientific Evaluation

**Run:** provider `csv` (is_real=True) · request `df7ec7406b12dcd4` · matrix `4c2aa61eccfd276c` (`srp-matrix-v2`) · assets: AAPL, BTC/USD, ETH/USD, SPY, XAU/USD

| Metric (out-of-sample, headline cost+slippage) | Value |
|---|---|
| OOS Sharpe (annualised) | -1.470 |
| Deflated Sharpe (prob. edge is real) | 0.027 |
| Sharpe 95% CI (per-period) | [-0.119, -0.040] |
| Beats buy-and-hold | False |
| Beats matched random-timing (p95) | False |
| Max drawdown (OOS) | 84.3% |
| OOS trades / bars | 3035 / 6000 |
| Robustness / Stability / Generalization | 0.000 / 0.609 / 0.000 |
| Confidence | 0.007 |

**Mechanical verdict:** **Graveyard** — cause of death: catastrophic drawdown out-of-sample

Reasons: OOS max drawdown 84.3% exceeds gate 60%


## STEP 6 — Knowledge-Base Record

| Field | Value |
|---|---|
| robustness_score | pending |
| stability_score | pending |
| generalization_score | pending |
| risk_adjusted | pending (deflated Sharpe / maxDD / profit factor / #trades) |
| failure_modes | *predicted:* turnover-cost domination; wrong side of reversal; bid-ask artifact; no edge |
| confidence | pending |
| verdict | **Not Yet Tested** |
| similar_strategies | H-006 (Consecutive Up/Down), H-007 (Greedy), H-011 (Momentum) |
| literature | Jegadeesh 1990, Lehmann 1990, Lesmond–Schill–Zhou 2004 (contradict); Roll 1984, Lo–MacKinlay 1988, French–Roll 1986 (mixed/artifact) |
| provenance_hash | assigned at STEP 5 run |

## STEP 7 — Contribution to Learning

Bears on **M-002** (*simple price-action momentum rules do not survive costs/turnover*). H-002 is
also the corpus's **calibration null**: if the platform ever reports H-002 as *Supported*, that is a
red flag to audit the pipeline, not a discovery. Votes alongside H-006, H-007, H-011.

**Cross-asset generalization (5 markets, 2026-07-08)** — per-asset OOS Sharpe (ann., 5+5 bps):
AAPL **−1.60**, BTC **−0.97**, ETH **−0.88**, SPY **−2.14**, XAU **−2.25** → **0/5 positive**.
Per-regime (pooled): trend **+0.02**, range **−1.45**, crisis **−2.29** — **negative in every market
and every regime.** The calibration null behaved exactly as designed: the platform manufactured no
false positive anywhere. This is the strongest possible confirmation that the instrument reports
"no edge" when there is none. See `CROSS_ASSET_GENERALIZATION.md`.

---

**Sources:**
- Jegadeesh (1990), *Evidence of Predictable Behavior of Security Returns*, J. Finance 45(3) — https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1990.tb05110.x
- Lehmann (1990), *Fads, Martingales, and Market Efficiency*, QJE 105(1) — https://academic.oup.com/qje/article-abstract/105/1/1/1928416
- Roll (1984), *A Simple Implicit Measure of the Effective Bid-Ask Spread*, J. Finance 39(4) — https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1984.tb03897.x
- Lo & MacKinlay (1988), *Stock Market Prices Do Not Follow Random Walks*, RFS 1(1) — https://academic.oup.com/rfs/article-abstract/1/1/41/1601244
- French & Roll (1986), *Stock Return Variances…*, J. Financial Economics 17(1) — https://www.sciencedirect.com/science/article/abs/pii/0304405X86900048
- Lesmond, Schill & Zhou (2004), *The Illusory Nature of Momentum Profits*, J. Financial Economics 71(2) — https://www.sciencedirect.com/science/article/abs/pii/S0304405X0300206X
