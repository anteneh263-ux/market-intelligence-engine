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
```

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
    {"gate": "dsr_overfitting", "status": "FAIL", "measured": 0.657, "threshold": ">= 0.95"},
    {"gate": "input_audit", "status": "NOT_AVAILABLE", "measured": {"declared_present": false},
     "explanation": "Strategy did not declare its inputs (declared_inputs is None)."}
  ],
  "note": "Research gate only - no live trading, no broker, no order execution."
}
```

## Limitations

- The target-control verdict uses a Sharpe tolerance band; near-ties are `inconclusive`
  by design and make no causal claim.
- The input audit tracks column reads (`df[col]`); a strategy that reads data through a
  path other than the passed frame is out of scope — declare inputs explicitly.
- `family_dsr()` gives a stricter, ledger-wide deflated Sharpe; the pipeline's
  per-strategy DSR (its `grid_size` budget) is deliberately left unchanged for
  backward compatibility.
- The gate proves *consistency and causality* on a historical tail; it does not predict
  live performance and does not connect to any broker.
