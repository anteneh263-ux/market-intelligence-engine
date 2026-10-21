# Strategy Research — MIE evaluation module

A module **inside** the Market Intelligence Engine that treats every trading strategy as a
falsifiable scientific hypothesis and runs it through **one frozen, reproducible pipeline**. The
goal is not profit — it is auditable, comparable evidence about *when* a strategy works, *when* it
fails, and *which principles survive changing markets*.

> ⚠️ Research only. No execution, no signals, no advice. A verdict is a statement about evidence.

## Architecture (clean seams)

```
providers/        swappable market-data layer — ONE interface, many vendors
  base.py           DataSource ABC + DataRequest + OHLCVFrame (canonical schema, is_real flag)
  csv_source.py     CSV import  (is_real=True)   <- primary real-data path in a locked-down env
  http_sources.py   Binance / FMP / CoinDesk     (is_real=True, keys from env)
  synthetic_source  deterministic, is_real=False  <- pipeline tests + methodology demos ONLY
  registry.py       get_provider(name) — the single seam the engine depends on

strategies/       strategy layer — ONE interface, many rules
  base.py           Strategy ABC: positions(df) -> {-1,0,+1}; param_grid() = snooping budget
  implementations   H-002 BarUpDn, H-005 ChannelBreakOut, H-010 MACD, H-020 RSI
  registry.py       get_strategy("H-010")

evaluation/       the frozen, identical-for-every-strategy pipeline
  frozen.py         FrozenMatrix (costs/slippage/splits/thresholds) + content-addressed hash
  regimes.py        ex-ante trend/range/crisis labels (price only, never outcome)
  walkforward.py    expanding-window OOS: select in-sample, evaluate out-of-sample, stitch
  backtest.py       position-based backtest; positions shifted forward (execution lag, not signal validation)
  benchmarks.py     buy-and-hold + matched random-timing (must beat 95th pct)
  stats.py          deflated Sharpe (Bailey–López de Prado) + BH-FDR + bootstrap (ported, golden)
  robustness.py     robustness / stability / generalization / confidence scores
  verdict.py        mechanical verdict: Supported / Not Supported / Inconclusive / Graveyard
  pipeline.py       evaluate(request) -> EvaluationReport

dossier.py        auto-update dossier STEP 5 + INDEX row + meta-hypothesis tallies (real data only)
run.py            CLI
```

The evaluation engine depends only on the `DataSource` and `Strategy` **interfaces**. Swapping a
data vendor or adding a strategy touches a registry, never the pipeline — so every strategy is
judged by identical code and results are directly comparable.

## The frozen pipeline (STEP 5)

For each asset → label regimes ex-ante → for each (cost, slippage) in the frozen ladder → walk-
forward OOS → deflated Sharpe (prices in the parameter grid = data-snooping budget) → benchmarks on
the OOS span → one condition row. Aggregate → pooled headline → robustness/stability/generalization
→ mechanical verdict. Same code, every strategy. Reproducible from `(request hash, matrix hash,
provider)`.

## Data integrity (non-negotiable)

`is_real=False` (synthetic) data is evaluated identically but flagged **ILLUSTRATIVE** and never
written to a dossier as a verdict. Real conclusions require real OHLCV. TradingView Strategy-Tester
numbers are treated as an in-sample **claim to falsify**, never as evidence.

## Usage

```bash
# List everything + the frozen-matrix identity
python -m app.strategy_research.run

# Real evaluation from CSVs in ./data_csv (e.g. BTCUSDT_1d.csv, AAPL_1d.csv), write to KB
python -m app.strategy_research.run --strategy H-010 --provider csv \
    --assets BTC/USDT,ETH/USDT,AAPL --timeframe 1d --write

# Methodology demo on synthetic data (never recorded as evidence)
python -m app.strategy_research.run --strategy H-020 --provider synthetic \
    --assets BTC/USDT,ETH/USDT --timeframe 1d
```

The standalone CLI defaults to v4. Select `--matrix v1`, `v2` or `v3` explicitly
for legacy methods. The older API (`GET /api/strategy-research/catalogue`,
`POST /api/strategy-research/evaluate`) and direct low-level evaluator stay on v1;
`POST /api/engine/evaluate` defaults to v4. See
[default selection and compatibility](../../docs/specs/NEW-EVALUATION-DEFAULT-V4.md).

## Extending

- **New provider:** subclass `DataSource`, implement `_fetch` + `is_real`, `register_provider(...)`.
- **New strategy:** subclass `Strategy`, implement `positions()` + `param_grid()`,
  `register_strategy(...)`. The remaining 19 TradingView strategies (H-003…H-024) plug in here with
  no change to the evaluation engine — that is the whole point.

## Tests

`tests/test_strategy_research.py` (16 checks): provider determinism + schema, no-look-ahead
(truncation invariance + shifted backtest), deflated-Sharpe trial penalty, BH-FDR, pipeline
byte-identical replay, synthetic-not-evidence guard, and dossier/INDEX/meta auto-update. Run without
pytest via `PYTHONPATH=.:tests python3 tests/_run_no_pytest.py`.


### Statistical and timing limits

Matrix v4 reports the per-asset Sharpe min–max as descriptive dispersion,
with sampling uncertainty explicitly not estimated. One asset gives a zero-width
asset range, not certainty. Legacy v2/v3 fields are preserved for reproduction but
are rendered as asset ranges. See `docs/specs/SHARPE-UNCERTAINTY-V4.md`.

One-bar execution lag does not sanitize a signal that reads future values. The
registry-derived synthetic causality suite covers every collected strategy's
standard and grid configurations using truncation and future-only OHLCV changes.
Passing these finite checks does not establish causality for all inputs, arbitrary
later registrations or revised/late market data. Some frozen strategy-source
comments still contain the earlier absolute claim; this limitation supersedes
that claim without changing source-file hashes used by historical manifests.
