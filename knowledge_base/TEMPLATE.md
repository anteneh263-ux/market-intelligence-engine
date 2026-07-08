# Strategy Dossier — H-XXX: <Strategy Name>

**strategy_id:** H-XXX · **source:** TradingView · **date:** YYYY-MM-DD
**status:** <which STEPs are complete> · verdict **<Not Yet Tested | Supported | Not Supported | Inconclusive | Graveyard>**

> ⚠️ Research only. No execution, no signals, no advice.

---

## STEP 1 — Strategy Analysis

*Read the full Pine Script. Extract verbatim and unambiguous; do not infer intent the code
does not express.*

- **Indicators:** <every indicator + its inputs>
- **Entry (long / short):** <exact conditions>
- **Exit:** <exact conditions; note long-only vs long/short>
- **Filters:** <trend / volatility / session / higher-timeframe filters, or "none">
- **Parameters:** <every tunable input + the range the author actually uses>
- **Risk management:** <stops, targets, ATR sizing, or "none">
- **Position sizing:** <fixed, %-equity, volatility-scaled, or "none">
- **Implicit assumptions (the real targets of testing):** <e.g. trends persist; fills at close;
  no overnight gaps; the chosen params are not data-snooped>
- **Script-integrity check:** <look-ahead / repainting audit — does it reference the forming
  bar, `security()` with lookahead, future-peeking? Flag explicitly. This gate is mandatory.>

## STEP 2 — Falsifiable Hypothesis

**H-XXX.** *<One or more pre-registered, falsifiable claims about risk-adjusted performance,
out-of-sample survival, and generalization — never about profit alone.>*

**Kill criteria (pre-committed, before any run):** *<the exact, numeric conditions under which
this hypothesis is declared Not Supported — e.g. deflated Sharpe ≤ 0 OOS, edge in a minority of
assets/regimes, fails to beat buy-and-hold AND random-timing on risk-adjusted terms.>*

## STEP 3 — Research Review (evidence, not truth)

*Search the academic literature for the strategy's **class**. Papers are evidence, not truth —
data-snooping and publication bias apply to them too.*

- **<Citation>** — <supports / contradicts / mixed>: <one-line finding>.
- **<Citation>** — <supports / contradicts / mixed>: <one-line finding>.

**Expected failure modes (predicted before testing):** <named, specific conditions under which
this class is known or expected to break.>

## STEP 4 — Experiment Design (pre-registered; frozen before any run)

**Matrix (all combinations, frozen):**
- **Assets:** <diversified set, including assets NOT used to design the rule>
- **Regimes:** trend / range / crisis, labelled ex-ante by an independent rule (never by outcome)
- **Volatility environments:** low / high
- **Time periods:** rolling walk-forward train/test splits (out-of-sample is sacred)
- **Transaction costs:** {0, realistic, stressed} bps/side
- **Slippage:** {none, 1×, 2×} modelled
- **Benchmarks:** buy-and-hold **and** random-timing (matched trade count)
- **Data-snooping budget:** <how many param sets / variants considered — the significance bar
  rises with it>

**Primary metric (frozen):** out-of-sample **deflated Sharpe** vs benchmarks.
**Secondary:** max drawdown, profit factor, turnover, regime-conditional performance,
parameter-stability surface.

**This design is registered; it may not be altered after seeing results.**

## STEP 5 — Scientific Evaluation

*Runs once **real** OHLCV data is supplied (platform data-integrity constraint). Evaluate
Robustness / Stability / Generalization / Risk-adjusted / Failure modes / Confidence — never
profit. Apply the data-snooping correction. Emit a mechanical verdict.*

**Status:** <Pending real data | Results below>

## STEP 6 — Knowledge-Base Record

| Field | Value |
|---|---|
| robustness_score | <pending> |
| stability_score | <pending> |
| generalization_score | <pending> |
| risk_adjusted | <deflated Sharpe / maxDD / profit factor / #trades> |
| failure_modes | <predicted, then observed> |
| confidence | <calibrated, penalized by search breadth> |
| verdict | **<Not Yet Tested>** |
| similar_strategies | <links to related dossiers> |
| literature | <refs + evidence direction> |
| provenance_hash | <assigned at STEP 5 run> |

## STEP 7 — Contribution to Learning

*How this strategy updates the corpus. Which meta-hypothesis (M-xxx) it bears on, and which
robust-vs-fragile traits it moves. Add/point to the relevant M-xxx and update INDEX.md.*

---

**Sources:**
- <full citation + URL>
- <full citation + URL>
