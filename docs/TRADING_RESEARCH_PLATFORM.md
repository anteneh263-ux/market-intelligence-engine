# Trading Strategy Research Platform — Operating Protocol

**What this is.** A repurposing of the Experiment One rigor engine into a platform that treats
every uploaded TradingView strategy as a **scientific hypothesis to be falsified**, not a signal
to be trusted. The deliverable is not a strategy and not a signal — it is **accumulated,
provenance-backed knowledge** about *why* strategies work, *when* they work, *when* they fail,
and *which principles survive changing market conditions*.

> ⚠️ **Research only. No execution. No buy/sell signals. No profit optimization.** This platform
> studies trading hypotheses; it does not provide financial advice and does not trade.

---

## How the existing engine is repurposed (nothing discarded)

| Capability we already built | Role in the trading platform |
|---|---|
| **Pre-registration + freezing** (M0) | Every experiment's config, metrics, and thresholds are frozen *before* the backtest runs — the antidote to curve-fitting. |
| **Frozen metrics + golden tests** (M2) | Strategy evaluation metrics are fixed and immutable, so results can't be tuned. |
| **Statistics + mechanical verdict** (M6) | Deflated-Sharpe / multiple-testing correction and a discretion-free Supported / Not-Supported / Inconclusive verdict. |
| **Provenance + hash-chained log + replay** (M5) | Every strategy evaluation is reproducible byte-for-byte from its recorded provenance. |
| **The V1 crypto backtester** (`market-intelligence-engine/backend`) | The execution substrate — data → features → backtest → metrics → **Strategy Graveyard**. |
| **Adversarial / falsification discipline** (whole program) | Each strategy is attacked, not defended: leakage checks, regime splits, cost stress, out-of-sample. |

**In one line:** the V1 backtester executes; the Experiment One rigor layer keeps it honest; the
7-step workflow drives it; the knowledge base compounds.

---

## The 7-step workflow (executed for every uploaded strategy)

**STEP 1 — Strategy Analysis.** Read the full Pine Script. Extract, verbatim and unambiguous:
entry rules, exit rules, filters, indicators, parameters (and their tested ranges), risk
management, position sizing, and every *implicit assumption* (e.g., "trends persist," "no
overnight gaps," "fills at close"). Flag look-ahead/repainting risks in the script itself.

**STEP 2 — Convert to a Falsifiable Hypothesis.** Rewrite the strategy as one or more
pre-registered, falsifiable claims, each with a stated **kill criterion**. Example:
*"H-001: an EMA-50 / EMA-200 crossover produces positive risk-adjusted returns net of costs
during trending regimes, and this edge survives out-of-sample and across assets."*

**STEP 3 — Research Review.** Search the academic literature for the strategy's *class*. Record
supporting evidence, contradicting evidence, known weaknesses, and expected failure modes.
**Papers are evidence, not truth** — data-snooping and publication bias apply to them too.

**STEP 4 — Experiment Design (pre-registered).** Design a reproducible experiment **matrix** and
freeze it before any run: multiple **assets**, **market regimes** (trend / range / crisis),
**volatility environments**, **time periods** (with walk-forward out-of-sample), **transaction
costs**, and **slippage** assumptions. Pre-register the primary metric, thresholds, and the
data-snooping budget (how many parameterizations were tried).

**STEP 5 — Scientific Evaluation.** Run the frozen matrix. Evaluate — **not profit** — but:
Robustness, Stability, Generalization, Risk-adjusted performance, Failure modes, Confidence
(definitions in the rubric below). Apply the deflated-Sharpe / multiple-testing correction.
Emit a mechanical verdict; send fragile strategies to the **Graveyard** with a cause of death.

**STEP 6 — Knowledge Base.** Store the strategy dossier (schema below). The KB grows with every
strategy and is the real asset.

**STEP 7 — Learning.** Compare across the KB: what do *robust* strategies share? what do *fragile*
ones share? Generate meta-hypotheses (e.g., "rules with fewer free parameters survive regime
change more often") and test them as the corpus grows.

---

## Evaluation rubric (STEP 5) — scientific, not profit-seeking

- **Robustness** — fraction of matrix conditions (asset × regime × cost) where risk-adjusted
  performance stays positive *and* statistically credible after the data-snooping correction.
- **Stability** — sensitivity to small parameter perturbations and to sub-period choice; a stable
  edge does not collapse when EMA-50 becomes EMA-45.
- **Generalization** — in-sample vs walk-forward out-of-sample degradation; performance on assets
  *not* used to design the rule.
- **Risk-adjusted performance** — Sharpe/Sortino, max drawdown, profit factor — reported as
  **deflated Sharpe** (Bailey–López de Prado style) to account for the number of rules tried.
- **Failure modes** — the specific, named conditions under which it breaks (regime, cost level,
  turnover, gap risk).
- **Confidence** — calibrated confidence in the verdict, penalized by search breadth and rewarded
  by out-of-sample survival. Reuses the Experiment One "priced-search" doctrine.

**Verdict categories:** `Supported` · `Not Supported` · `Inconclusive` · `Graveyard` (failed the
robustness gates: e.g., Sharpe < threshold, drawdown > threshold, no out-of-sample survival,
data-snooping-adjusted edge ≈ 0).

---

## Knowledge-base schema (STEP 6) — one dossier per strategy

`strategy_id`, `name`, `source` (TradingView), `date`, `hypotheses[]` (+ kill criteria),
`extracted_rules` (entries/exits/filters/indicators/params/risk/sizing/assumptions),
`literature[]` (ref + evidence direction: supports / contradicts / mixed),
`experiment_registry[]` (frozen configs + hashes — pre-registered before running),
`results_by_condition[]`, `robustness_score`, `stability_score`, `generalization_score`,
`risk_adjusted` (deflated Sharpe, max DD, profit factor, #trades), `failure_modes[]`,
`confidence`, `verdict`, `similar_strategies[]`, `provenance_hash`.

Dossiers live in `trading_research/knowledge_base/`; `INDEX.md` is the growing catalogue;
`TEMPLATE.md` is the fixed format.

---

## The engine (STEP 5 implementation)

STEP 5 is executed by a module **inside** the Market Intelligence Engine backend:
`backend/app/strategy_research/` (see its `README.md`). Nothing is a separate project — this is the
MIE becoming the central research engine for quantitative strategy evaluation.

- **Pluggable data** — one `DataSource` interface, interchangeable providers (`csv`, `binance`,
  `fmp`, `coindesk`, `synthetic`). Evaluation code never depends on a concrete vendor, so the data
  source is replaceable without touching the pipeline.
- **One frozen pipeline for every strategy** — walk-forward OOS, the same cost/slippage ladder, the
  same benchmarks (buy-and-hold + matched random-timing), the same statistics (deflated Sharpe,
  BH-FDR, bootstrap CIs), the same mechanical verdict. The matrix is content-addressed
  (`srp-matrix-v1`, hash `a740f3acbf6572d6`); any change to it changes the hash and shows in provenance.
- **No look-ahead by construction** — positions are shifted forward one bar before returns apply;
  tests assert truncation-invariance and that same-bar peeking earns nothing.
- **Auto-updates the knowledge base** — on a real-data run the engine writes STEP 5 into the
  dossier, updates the INDEX row, and refreshes the meta-hypothesis tallies (`meta_ledger.json`).
- **Reference strategies implemented** — H-002, H-005, H-010, H-020. The other 19 subclass the same
  `Strategy` interface with no change to the engine.

Run: `python -m app.strategy_research.run --strategy H-010 --provider csv --assets BTC/USDT,ETH/USDT
--timeframe 1d --write`  ·  API: `POST /api/strategy-research/evaluate`.

---

## Data-integrity constraint (read before STEP 5)

Steps 1–4 (analysis, hypothesis, literature, pre-registered design) need **no market data** and
are done immediately for any uploaded strategy. **STEP 5 execution requires *real* historical
OHLCV data**, and the honesty rule from Experiment One is absolute: **we will not present
synthetic-data backtests as evidence about a real strategy.** In this sandbox, exchange APIs are
blocked, so to run STEP 5 with scientific validity, choose one:
1. **Upload real OHLCV data** (CSV: timestamp, open, high, low, close, volume) for the assets and
   periods to test; or
2. **Run the platform locally** (the V1 backtester + this rigor layer) where data access exists; or
3. Explicitly request a **methodology demonstration on synthetic data** — which will be clearly
   labelled *illustrative, not evidential* and will never enter the knowledge base as a real result.

---

## How to submit a strategy

Paste or upload the **Pine Script** (and note any parameter ranges you actually use). I will run
STEP 1 → STEP 4 immediately and pre-register the experiment; STEP 5 proceeds once real data is
provided per the constraint above. The first reference dossier — the EMA-50/200 crossover you
cited — is already in the knowledge base as `H-001` to lock the format.

*The accumulated knowledge, not any individual strategy, is the asset.*
