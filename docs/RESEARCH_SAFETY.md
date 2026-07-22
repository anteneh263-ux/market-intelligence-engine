# Research safety gates & trial auditing

Four additive, deterministic tools that reduce **false positives** in the Market
Intelligence Engine. They change **no** existing evaluation result — they wrap the
existing pipeline with stricter accounting and a promotion gate.

> Research only. No live trading, no broker, no order execution.

## What they do

1. **Target-only control** (`app/research_safety/target_control.py`). Compares a
   candidate to a deliberately simple control that uses **only the target asset's own
   price** (no external signals), on the *same* asset(s) and period. Records candidate/
   control IDs, ΔSharpe, Δreturn, ΔmaxDD and a verdict `candidate_wins | control_wins |
   inconclusive`. It is a **control comparison, not a causal claim**.
   *Why it cuts false positives:* a candidate that cannot beat trivial price-only
   exposure on its own asset has not demonstrated added value.

2. **Full DSR trial-ledger** (`trial_ledger.py`). The deflated Sharpe must be deflated
   by **every** real research attempt — candidates, controls, rejected rounds,
   parameter combinations, discovery variants — not only the survivors. Technical
   failures that never produced a valid result are recorded but **excluded**.
   *Why it cuts false positives:* under-counting trials inflates the deflated Sharpe;
   counting them all keeps the data-snooping penalty honest.

3. **Declared-versus-actual input audit** (`input_audit.py`). Runs the strategy against
   a `TrackingFrame` that records which OHLCV columns it **actually reads** (explicit
   instrumentation, not a source-text search), and compares *declared* vs *prepared* vs
   *read*. Flags `declared_not_used`, `used_not_declared`, `missing_source`,
   `central_not_read`. A candidate is **not promotable** if it reads undeclared data or
   if a central declared input is never read.
   *Why it cuts false positives:* a "signal" that secretly uses undeclared data — or a
   headline claim that never actually reads the data it credits — is a hidden leak.

4. **Promotion gate** (`promotion_gate.py`). One aggregate gate before paper trading:
   valid backtest, out-of-sample/walk-forward, max-drawdown limit, minimum trades,
   costs & slippage, DSR/overfitting, input audit, target-control, paper-vs-backtest
   signal match on a historical tail, and signal-at-close/execute-next-bar. Each
   sub-gate returns `PASS | FAIL | NOT_AVAILABLE` with the measured value, threshold and
   an explanation. Decision: any mandatory `FAIL` → **REJECT**; else any mandatory
   `NOT_AVAILABLE` → **HOLD**; else **PROMOTE**. `NOT_AVAILABLE` is **never** treated as
   `PASS`.

5. **Batch runner** (`batch.py`). Runs items 3–4 over **every registered strategy** at
   once, under **identical** assets, date range, costs and slippage. It records the
   candidate, the shared control and every parameter combination in the append-only
   ledger (technical failures use `in_dsr = false`). Strategies are discovered **dynamically**
   from the registry — nothing is hard-coded — and processed in a **deterministic**
   (sorted) order, so the JSON is reproducible. Each trial carries a **content-derived
   id**, so re-running the identical batch is **idempotent** and never duplicates trials
   or rewrites history. After registration it freezes one final ledger trial count,
   reports both the immutable raw record count and an effective economic-trial count.
   Exact reruns and the candidate/default-parameter representation remain in history
   but count once economically. It computes `family_dsr` for every valid candidate with
   that same effective count and the matching parameter-trial Sharpe population, and uses this
   stricter value in the promotion gate. It writes an atomic, timestamped report plus `latest.json` under
   `knowledge_base/research_safety/batches/`. It **never** starts a forward test, and it
   changes no existing evaluation, threshold, strategy behaviour or historical result.
   *Why it helps:* it gives one honest, side-by-side, reproducible safety snapshot of the
   whole corpus and keeps the DSR trial budget complete without any manual bookkeeping.

## CLI example

```bash
# Candidate vs a target-only control
python -m app.cli safety control --target H-025 --control CTRL-TARGET-ONLY \
    --assets BTC/USD,ETH/USD,SPY,XAU/USD

# Full promotion gate (add --control to enable the target-control sub-gate)
python -m app.cli safety promote --target H-028 --assets BTC/USD

# Input audit of one strategy
python -m app.cli safety audit --target H-020 --assets BTC/USD

# Trial-ledger summary
python -m app.cli safety trials

# Batch: run the safety checks over EVERY registered strategy (human summary)
python -m app.cli safety batch --assets BTC/USD,ETH/USD,SPY,XAU/USD

# Batch as deterministic JSON
python -m app.cli safety batch --assets BTC/USD,ETH/USD,SPY,XAU/USD --json
```

Each batch row reports: `strategy_id`, evaluation `status`, raw `sharpe_per_bar`,
`sharpe_annual`, bar frequency, return aggregation, `total_return`, `max_drawdown`,
`num_trades`, both `strategy_dsr` (`sDSR`) and `family_dsr` (`fDSR`), the target-control `verdict`, the
`promotion_decision`, and the failing gates as `failure_reasons`. A summary line counts
valid strategies, technical failures and the promote / hold / reject split; a ledger
line reports how many trials count toward the DSR budget. The report is written to
`knowledge_base/research_safety/batches/batch-<timestamp>.json` and mirrored to
`latest.json` (both atomic).

## Example JSON output (promotion gate, abridged)

```json
{
  "kind": "promotion_gate",
  "strategy_id": "H-028",
  "decision": "REJECT",
  "gates": [
    {"gate": "valid_backtest", "status": "PASS", "measured": {"n_bars": 8401}, "threshold": "is_real and n_bars > 0"},
    {"gate": "max_drawdown", "status": "FAIL", "measured": 0.6368, "threshold": "<= 0.6",
     "explanation": "OOS max drawdown within the limit."},
    {"gate": "dsr_overfitting", "status": "FAIL",
     "measured": {"family_dsr": 0.657, "strategy_dsr": 0.81, "source": "trial_ledger"},
     "threshold": ">= 0.95"}
  ],
  "note": "Research gate only - no live trading, no broker, no order execution."
}
```

## Limitations

- The target-control verdict uses a Sharpe tolerance band; near-ties are `inconclusive`
  by design and make no causal claim.
- The input audit tracks column reads (`df[col]`); a strategy that reads data through a
  path other than the passed frame is out of scope — declare inputs explicitly.
- The report keeps the pipeline's per-strategy `strategy_dsr` for backward
  compatibility. Promotion uses the separately labelled ledger-wide `family_dsr`.
- The ledger is never rewritten. `raw_dsr_trials` is the append-only historical count;
  `effective_dsr_trials` reconciles exact legacy reruns and candidate/default-parameter
  aliases. `duplicate_dsr_records` makes the difference visible. New records carry an
  economic identity derived from data, settings, parameters and realised signal
  behaviour, so changed evidence or trading logic remains a new trial.
- The gate proves *consistency and causality* on a historical tail; it does not predict
  live performance and does not connect to any broker.
- Trial identity includes strategy code, parameters, provider, period, rules version,
  assets, timeframe, matrix, cost and slippage. Changing research-relevant content
  creates a new trial; an identical rerun remains idempotent. It never starts a forward
  test.
