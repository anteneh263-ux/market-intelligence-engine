# Strategy Dossier — H-001: EMA 50/200 Crossover

**strategy_id:** H-001 · **source:** TradingView (canonical example) · **date:** 2026-07-07
**status:** STEPs 1–4 complete; STEP 5 **pre-registered, pending real data**; verdict **Not Yet Tested**

> ⚠️ Research only. No execution, no signals, no advice. This dossier is the fixed format every
> future strategy will follow.

---

## STEP 1 — Strategy Analysis

Canonical "golden cross / death cross" trend-following rule.

- **Indicators:** EMA(50), EMA(200) of close.
- **Entry (long):** EMA-50 crosses **above** EMA-200 ("golden cross").
- **Exit / short:** EMA-50 crosses **below** EMA-200 ("death cross"); long-only variant exits to
  cash; long/short variant flips.
- **Filters:** none in the base rule (a common variant adds a trend/volatility or higher-timeframe
  filter).
- **Parameters:** the two lengths (50, 200); bar timeframe; long-only vs long/short.
- **Risk management:** none in the base rule (variants add stops / ATR sizing).
- **Position sizing:** implicitly fixed / all-in in the base rule.
- **Implicit assumptions (the real targets of testing):** (a) trends persist long enough to
  overcome crossover lag; (b) the crossover is not just repackaged autocorrelation already priced
  in; (c) fills at the close with modest cost; (d) no material gap/overnight risk; (e) the 50/200
  choice is not a data-snooped artifact.
- **Script-integrity check:** EMAs use only past/closed bars → **no look-ahead** if evaluated on
  bar close; flag any variant that references `close` of the *forming* bar (repainting risk).

## STEP 2 — Falsifiable Hypothesis

**H-001.** *An EMA-50/EMA-200 crossover rule produces positive risk-adjusted returns net of
realistic costs, and this edge (a) survives out-of-sample, (b) generalizes across assets, and
(c) is not explained by the number of length-pairs one could have tried.*

**Kill criteria (pre-committed):** the hypothesis is **Not Supported** if, after the
data-snooping correction, the deflated Sharpe is ≤ 0 out-of-sample, **or** the edge appears only
in a minority of assets/regimes, **or** it does not beat a buy-and-hold and a random-timing
benchmark on risk-adjusted terms.

## STEP 3 — Research Review (evidence, not truth)

The moving-average class is one of the most-studied in finance. The literature is a gift here
because it is largely **adversarial**:

- **Brock, Lakonishok & LeBaron (1992)** found MA rules had statistically significant predictive
  power on the DJIA (1897–1986) — the classic *supporting* in-sample result.
- **Sullivan, Timmermann & White (1999), "Data-Snooping, Technical Trading Rule Performance, and
  the Bootstrap" (Journal of Finance)** — the decisive *contradicting* result: once you correct
  for the **universe of rules** searched (data-snooping) via a bootstrap reality check, the
  apparent superiority of the best technical rules is far weaker and often not significant. This
  is the single most important prior for us — it is *exactly* the deflated-Sharpe discipline we
  already built.
- **Zakamulin ("A Comprehensive Look at the Empirical Performance of Moving Average Trading
  Strategies"; "Market Timing with Moving Averages," 2017)** — extensive out-of-sample and
  real-life analysis: MA timing's historical edge is largely an artifact of **in-sample
  optimization** and a **handful of crisis periods**; after realistic costs and true out-of-sample
  testing it **rarely and unreliably** beats buy-and-hold, and performance is strongly
  **regime-dependent**.
- **General finding:** technical-rule profitability has **declined over time** (markets adapt),
  is **sensitive to transaction costs and turnover**, and is **regime-dependent**.

**Expected failure modes (predicted before testing):** whipsaw losses in ranging markets;
crossover **lag** giving back trend gains at reversals; edge **concentrated in a few crises**;
sensitivity to the 50/200 choice; degradation after costs and out-of-sample.

## STEP 4 — Experiment Design (pre-registered; frozen before any run)

**Matrix (all combinations, frozen):**
- **Assets:** a diversified set (e.g., several liquid equities/indices + a few crypto + an FX pair)
  — *including assets not used to pick 50/200*, to test generalization.
- **Regimes:** trend / range / crisis, labelled ex-ante by an independent volatility/trend rule
  (never by outcome).
- **Volatility environments:** low / high vol sub-periods.
- **Time periods:** rolling **walk-forward** train/test splits (out-of-sample is sacred).
- **Transaction costs:** {0, realistic, stressed} bps per side.
- **Slippage:** {none, 1×, 2×} a modelled estimate.
- **Benchmarks:** buy-and-hold **and** random-timing (matched trade count) — the honest baselines.
- **Data-snooping budget:** record how many length-pairs/variants are considered; the significance
  bar rises accordingly (deflated Sharpe / PBO).

**Primary metric (frozen):** out-of-sample **deflated Sharpe** vs benchmarks. **Secondary:** max
drawdown, profit factor, turnover, regime-conditional performance, parameter-stability surface.

**This design is registered; it may not be altered after seeing results.** STEP 5 runs it once
real OHLCV data is supplied (see platform data-integrity constraint).

## STEP 5 — Scientific Evaluation

**Pending real data.** No results will be recorded here until the frozen matrix is run on real
OHLCV. Synthetic-data output, if requested, will be labelled illustrative and kept out of the KB.

## STEP 6 — Knowledge-Base Record (fields populated so far)

| Field | Value |
|---|---|
| robustness_score | pending |
| stability_score | pending |
| generalization_score | pending |
| risk_adjusted | pending (deflated Sharpe / maxDD / PF) |
| failure_modes | *predicted:* whipsaw in ranges; reversal lag; crisis-concentration; cost sensitivity |
| confidence | pending |
| verdict | **Not Yet Tested** |
| similar_strategies | (none yet — will link to future SMA/MACD/momentum crossovers) |
| literature | Brock–Lakonishok–LeBaron 1992 (supports, in-sample); Sullivan–Timmermann–White 1999 (contradicts, data-snooping); Zakamulin 2017 (contradicts, OOS/real-life) |
| provenance_hash | (assigned at STEP 5 run) |

## STEP 7 — Contribution to Learning

**Prior placed into the corpus (before evidence):** the literature strongly predicts this class
is *fragile* — its historical edge is largely in-sample/data-snooped and crisis-concentrated.
H-001 therefore becomes the corpus's first test of a broader meta-hypothesis:

> **M-001 (meta):** *simple lagging trend-crossover rules with few free parameters show low
> out-of-sample robustness and high regime-dependence, once the search cost is priced in.*

As more strategies are tested, H-001's result will be one data point supporting or contradicting
M-001, and will seed comparisons on the traits (parameter count, turnover, regime-dependence)
that separate robust from fragile strategies.

---

**Sources:**
- Sullivan, Timmermann & White (1999), *Data-Snooping, Technical Trading Rule Performance, and the Bootstrap*, Journal of Finance — https://onlinelibrary.wiley.com/doi/abs/10.1111/0022-1082.00163
- Zakamulin, *A Comprehensive Look at the Empirical Performance of Moving Average Trading Strategies* — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2677212
- Zakamulin, *Market Timing with Moving Averages: Anatomy and Performance of Trading Rules* — https://www.researchgate.net/publication/315433929_Market_Timing_with_Moving_Averages_Anatomy_and_Performance_of_Trading_Rules
- Technical-rule profitability & data-snooping overview — https://data-snooping.martinsewell.com/
