# Locked forward testing

Forward testing is the step **after** a strategy has cleared the research promotion
gate. A promoted strategy is *locked* — its code, parameters and risk rules are frozen
into a manifest — and then run forward on **new** market data it has never seen, with
**no** changes allowed mid-test. The question it answers is narrow and honest:

> Does the edge the backtest reported still show up, out-of-sample, on data that arrived
> after we committed to the strategy — under the exact same rules?

This is still a **simulation**. There is no live broker, no real orders, no private API
keys, no real money. A forward verdict is a statement about *evidence*, never advice.

---

## Where it sits in the lifecycle

```
Discovery ─▶ evaluation (srp-matrix-v2) ─▶ promotion gate ─▶ LOCK (manifest)
                                                                   │
                                                    forward paper-test on new data
                                                                   │
                                              evaluation ─▶ PASSED | FAILED | EXTEND
```

A forward test can only **start** for a strategy whose promotion gate returned
`PROMOTE`. `HOLD` or `REJECT` are refused — you cannot forward-test a strategy the
research gate has not cleared.

---

## How it differs from backtest and OOS walk-forward

| | Backtest | OOS walk-forward (in `srp-matrix-v2`) | **Locked forward test** |
|---|---|---|---|
| Data | historical, in-sample | historical, held-out folds | **new data after the lock date** |
| Strategy code / params | may still be tuned | frozen per fold, but the study is still iterating | **frozen and hashed — any change invalidates the test** |
| Risk rules | may change between runs | fixed for the run | **locked in the manifest** |
| Purpose | find an edge | estimate honest OOS performance | **confirm the edge persists on genuinely unseen data, unchanged** |
| Re-running after an edit | expected | expected | **forbidden — a new edit means a new strategy version and a new test** |

The forward test is deliberately the strictest of the three: the whole point is that
**nothing** about the strategy moves once the test begins. If you change the code, the
parameters, or the risk rules, the test is invalidated — it is no longer a test of the
thing you locked.

---

## Why "locked", and how the lock is enforced

When a forward test starts, `app/forward/manifest.py` writes a deterministic,
canonical-JSON manifest containing, among other fields:

- the strategy id and a **strategy version** (first 12 hex of the code hash);
- a **code hash** — SHA-256 of the strategy class source;
- a **parameter hash** — SHA-256 of the canonical-JSON parameters, plus the parameters
  themselves;
- the declared strategy source files, each with its own SHA-256;
- the assets, timeframe, costs (bps) and slippage (bps);
- the position limits, `signal_timing = "close_of_bar"` and
  `execution_delay = "next_bar_open"`;
- references to the promotion report and the locked backtest / OOS reference metrics;
- the git commit SHA (when available);
- the frozen evaluation **thresholds**;
- the manifest version (`forward-v1`).

The `forward_test_id` is itself a hash of the immutable core
(`FT-<strategy_id>-<12 hex>`), so **identical inputs always produce the identical id and
the identical manifest** — and a **changed strategy automatically gets a new id**. You
can never silently overwrite a prior test.

Before **every** forward run, `ForwardStore.integrity_check` recomputes the code and
parameter hashes and compares them to the locked manifest. If either has drifted:

- the run does **not** proceed;
- the test status becomes **`INVALIDATED`**;
- the event log records **exactly what changed** (locked vs current hash);
- the original manifest, event history and observations are **left untouched**;
- to continue you must create a **new strategy version and a new forward-test id** — the
  invalidated test is never reused.

(File-level SHA drift on a shared source file is reported as *informational* only; the
decisive invalidation signal is the class code hash and the parameter hash, so an
unrelated edit elsewhere in a shared module does not falsely invalidate a locked test.)

---

## Statuses

Every status change is written to an **append-only** event log (`events.jsonl`); history
is never rewritten.

| Status | Meaning |
|---|---|
| `DRAFT` | manifest locked, not yet activated (or activation was blocked) |
| `ACTIVE` | consistency check passed; collecting forward observations |
| `COMPLETED` | data collection stopped by the operator |
| `PASSED` | evaluation: every mandatory gate passed on a sufficient sample |
| `FAILED` | evaluation: at least one critical gate failed |
| `EXTEND` | evaluation: not enough data yet (or a mandatory input was unavailable) |
| `INVALIDATED` | integrity check failed — locked code/params changed |
| `CANCELLED` | cancelled by the operator |

---

## Same strategy in backtest and forward (no separate "forward strategy")

There is **one** causal simulator, `app/forward/simulate.py`, and it drives **both** the
locked backtest reference and the forward run. The forward path is not a re-implementation
— it is literally the same `Strategy.positions(...)` and the same execution model, so the
two can never silently diverge.

Before a test is activated, a **historical-tail consistency check** runs:

1. **Causality / no-repaint** — positions computed on truncated prefixes must equal the
   full-frame positions on those prefixes. A look-ahead or repainting strategy fails here.
2. **Engine equivalence** — the backtest simulator and the forward simulator must produce
   **identical executed positions** on the overlapping historical tail.

If either check fails, **activation is blocked**: the test stays `DRAFT` and never starts
collecting forward observations. Only a strategy whose forward signals provably match its
backtest signals on the same data is allowed to go `ACTIVE`.

---

## Signal timing and data separation (no look-ahead)

- A signal is decided at a bar's **close**.
- It is executed **no earlier than the next bar** — positions are shifted forward one bar
  (`executed[0] = 0`; `executed[t] = signal[t-1]`).
- Forward observations cover **only** bars on or after the locked start date. Every
  observation is tagged `data_partition = "forward"` and is written to a separate
  `observations.jsonl`. Training / backtest data (bars strictly before the start date) is
  used only for the locked reference and is **never** part of the forward record.

Each forward observation records: candle open time and UTC datetime, the signal, the
desired position, the simulated executed position, the execution price, gross P&L, costs,
slippage, net P&L, cumulative return, drawdown, and an error field.

---

## Evaluation: PASSED / FAILED / EXTEND

`app/forward/evaluate.py` compares the forward result to the **locked reference** using
**only** the thresholds frozen in the manifest — never numbers chosen after the fact. The
default thresholds are:

| Threshold | Default | Role |
|---|---|---|
| `min_observations` | 60 | sufficiency → `EXTEND` if below |
| `min_active_days` | 40 | sufficiency → `EXTEND` if below |
| `min_trades` | 10 | sufficiency → `EXTEND` if below |
| `max_drawdown` | 0.60 | **critical** → `FAILED` if breached |
| `sharpe_floor` | 0.0 | **critical** → `FAILED` if below (with enough data) |
| `reference_retention` | 0.30 | **critical** → forward Sharpe must retain ≥ 30% of the locked reference Sharpe |

Decision precedence:

1. **Realised drawdown breach** → `FAILED` (decisive regardless of sample size).
2. **Not enough data** (observations / active days / trades) → `EXTEND`.
3. **Sharpe floor or reference-retention failure** (on a sufficient sample) → `FAILED`.
4. A **`NOT_AVAILABLE`** mandatory gate (e.g. the reference Sharpe is missing or
   non-positive) → `EXTEND`. **`NOT_AVAILABLE` is never treated as PASS.**
5. Otherwise → `PASSED`.

Two invariants are enforced structurally:

- **A positive return alone is never `PASSED`.** Passing requires the drawdown gate, the
  Sharpe floor and the reference-retention gate to all pass — a strategy that made money
  with a Sharpe below the floor fails.
- **`NOT_AVAILABLE` is never `PASSED`**, mirroring the promotion gate.

---

## CLI

All commands accept `--json` for machine-readable output; otherwise a compact human
summary is printed.

```bash
# Start (locks the manifest; refused unless the promotion gate says PROMOTE)
python -m app.cli forward start --target H-0XX --assets BTC/USD \
    --start-date 2026-01-01 --provider csv --csv-root data_csv

# Collect new forward observations (integrity-checked first)
python -m app.cli forward run --id FT-H-0XX-xxxxxxxxxxxx

# Inspect status
python -m app.cli forward status --id FT-H-0XX-xxxxxxxxxxxx

# Evaluate against the locked reference -> PASSED | FAILED | EXTEND
python -m app.cli forward evaluate --id FT-H-0XX-xxxxxxxxxxxx

# List all forward tests
python -m app.cli forward list

# Re-check the lock (add --enforce to invalidate on drift)
python -m app.cli forward verify --id FT-H-0XX-xxxxxxxxxxxx
```

`forward start` is refused with a clear message if the strategy's promotion gate decision
is not `PROMOTE`:

```
forward start refused: promotion gate decision is 'REJECT', not 'PROMOTE';
a forward test can only start for a PROMOTE strategy
```

---

## Example manifest (abridged)

```json
{
  "manifest_version": "forward-v1",
  "forward_test_id": "FT-H-0XX-1a2b3c4d5e6f",
  "strategy_id": "H-0XX",
  "strategy_version": "1a2b3c4d5e6f",
  "status": "ACTIVE",
  "start_date": "2026-01-01",
  "earliest_eval_date": "2026-03-02",
  "code_hash": "1a2b3c4d5e6f...",
  "params": {"n": 55},
  "param_hash": "9f8e7d...",
  "assets": ["BTC/USD"],
  "timeframe": "1d",
  "cost_bps": 5.0,
  "slippage_bps": 5.0,
  "position_limits": {"min": -1, "max": 1, "max_gross_exposure": 1.0, "direction": "long_short"},
  "signal_timing": "close_of_bar",
  "execution_delay": "next_bar_open",
  "promotion_report_ref": "research_safety/promotions/H-0XX.json",
  "thresholds": {"min_observations": 60, "min_active_days": 40, "min_trades": 10,
                 "max_drawdown": 0.60, "sharpe_floor": 0.0, "reference_retention": 0.30}
}
```

## Example evaluation report (abridged)

```json
{
  "kind": "forward_evaluation",
  "forward_test_id": "FT-H-0XX-1a2b3c4d5e6f",
  "decision": "EXTEND",
  "reasons": ["only 42 observations (<60)"],
  "sufficient": false,
  "gates": [
    {"gate": "min_observations", "kind": "sufficiency", "status": "FAIL", "measured": 42, "threshold": 60},
    {"gate": "max_drawdown", "kind": "critical", "status": "PASS", "measured": 0.21, "threshold": 0.60},
    {"gate": "reference_retention", "kind": "critical", "status": "PASS", "measured": 0.04, "threshold": 0.03}
  ],
  "note": "Forward test is a SIMULATION ... Positive return alone is never PASSED; NOT_AVAILABLE is never PASSED."
}
```

---

## Files

```
app/forward/
├── __init__.py        public surface
├── manifest.py        deterministic locked manifest + hashing
├── store.py           atomic, append-only filesystem store + integrity check
├── simulate.py        the single causal simulator for backtest AND forward
├── engine.py          lifecycle: start / run / verify / evaluate / status
├── evaluate.py        PASSED | FAILED | EXTEND against the locked reference
└── cli_forward.py     CLI wiring (forward start/run/status/evaluate/list/verify)

knowledge_base/forward_tests/<forward_test_id>/
├── manifest.json      the lock
├── events.jsonl       append-only status history
├── observations.jsonl append-only forward observations (data_partition="forward")
└── evaluation.json    the latest evaluation report
```

---

**Reminder:** a forward test is still a simulation. It uses new, out-of-sample data and a
frozen strategy to gather honest evidence about persistence — it is **not** live trading,
and a `PASSED` verdict is evidence, not a recommendation to trade real money.
