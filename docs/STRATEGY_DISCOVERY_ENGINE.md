# Strategy Discovery Engine — Design

**status:** design + implementation · **date:** 2026-07-08 · **id namespace:** `G-####` (generated),
kept separate from the pre-registered `H-###` TradingView universe.

The Discovery Engine is the generative layer that closes the loop:

```
learn from failed strategies → identify robust traits → generate better hypotheses
   → test them scientifically (Research Core) → update the Knowledge Base → improve future discovery
```

It is the last module in the chain and the only one that *writes new science back into the front of
the pipeline*:

```
… → Knowledge Base → Meta-learning → AI Research Assistant → Strategy Discovery Engine ─┐
      ▲                                                                                   │
      └────────────── generated hypotheses re-enter Research Core / Strategy Evaluation ◀─┘
```

---

## 1. What it generates (composition, not hand-coding)

The engine does **not** hand-write strategies. It composes them from a library of **building
blocks** — reusable indicator signals — using a small set of **composition rules**. A generated
strategy is a *recipe* (blocks + rule + params) that materialises into an object implementing the
existing `Strategy` interface, so it drops straight into the frozen evaluation pipeline unchanged.

- **Building blocks** (`blocks.py`): MA-cross, price-vs-MA, MACD, RSI-reversion, Bollinger-reversion,
  Donchian breakout, momentum, Keltner breakout, ATR-expansion, Stochastic-reversion, Supertrend,
  and an ADX **regime gate**. Each block carries **traits** (family = trend / mean-reversion /
  breakout / volatility; turnover = low/med/high; regime affinity).
- **Composition rules** (`genome.py`): `VOTE` (majority vote of N directional blocks) and `FILTER`
  (a trigger block gated by a confirmer/trend block or the ADX regime gate). Both emit `{-1,0,+1}`,
  carried forward, causal — the backtester shifts positions, so look-ahead stays impossible.

Every recipe is **content-addressed and frozen at generation time** — its hash is recorded *before*
it ever sees test data. Auto-generated ideas are therefore **pre-registered**, exactly like the
TradingView strategies. This is what keeps a generative search scientifically legitimate.

## 2. How it learns what to generate (traits + priors)

`traits.py` maintains a **trait-success memory** mined from the whole corpus (H results via the KB
query API **and** G results via the discovery ledger):

- for each trait value (e.g. family=`breakout`, turnover=`high`) it tracks *count, fraction
  non-fragile, mean deflated Sharpe*;
- a **prior score** ranks a candidate by the historical promise of its trait profile;
- an **avoidance rule** rejects candidates whose dominant traits match a *repeatedly-failed* pattern
  (e.g. price-action momentum with high turnover — the confirmed null H-002), or that duplicate an
  already-tested recipe.

So the engine **prioritises** (requirement 3) using meta-learning, and **avoids** (requirement 4)
what has failed — and both improve every round as results accrue.

## 3. The closed loop (`engine.py`)

```
StrategyDiscoveryEngine.run_round(k, assets, matrix=v2):
  1. read corpus + meta-learning        → trait-success stats           (learn from failures)
  2. enumerate candidate recipes        → blocks × composition rules
  3. dedupe (recipe hash) + avoid       → drop tested / repeatedly-failed trait patterns
  4. score by learned priors            → prioritise
  5. take top-K, FREEZE + record        → ledger: G-#### 'generated' (recipe hash, round, score)
  6. register + send to Research Core   → orchestrator.run() on the SAME frozen pipeline
  7. record verdicts to the discovery   → discovery/results/G-####.json (separate namespace)
     ledger; update survival + traits   → improves priors for round n+1
```

`propose(k)` runs steps 1–5 only (generate without testing) for review.

## 4. Where it is recorded (separate from H-strategies)

Generated hypotheses live in their **own** collection so they never contaminate the pre-registered
TradingView catalogue (requirement 6):

```
trading_research/knowledge_base/discovery/
  ledger.json            registry of every G-#### : recipe, hash, traits, round, priority, status
  results/G-####.json    per-generated-strategy evaluation result
  DISCOVERY_REPORT.md    generated survival/leaderboard summary
```

`INDEX.md`, the H-`meta_ledger.json`, and the pre-registered dossiers are **untouched** by
discovery. Meta-learning still *reads* both collections (the learning loop needs all evidence), but
the provenance is cleanly separated.

## 5. Survival tracking (requirement 7)

The ledger records each generation's outcome and computes **survival statistics over time**: per
round, the fraction of generated strategies that were not sent to the Graveyard, the best deflated
Sharpe achieved, and the trait profiles that survived vs died. `DISCOVERY_REPORT.md` shows the trend
across rounds — the measurable signal of whether the *generator itself* is getting better.

## 6. Scientific integrity (the discovery multiplicity)

Generating and testing many candidates is data-snooping **at the discovery level**. The engine makes
this explicit and honest:

- the ledger tracks the **discovery search budget** (total generated / total tested);
- generated verdicts use the **same** deflated-Sharpe + BH-FDR corrections, now across the G-corpus,
  so a "Supported" generated strategy must clear an *even higher* bar;
- recipes are **frozen before testing** (pre-registration), and the verdict remains the mechanical
  pipeline output — the engine may bias *what it proposes*, never *whether it passes*.

The goal is not to manufacture a winner. It is to build a system whose *proposals* improve as it
learns which traits survive — measured honestly, and Graveyarded without mercy when they don't.
