# H-030 pre-registration

**Name:** Cross-Asset Time-Series Momentum (portfolio)
**State:** pre-registered 2026-10-23; data not yet downloaded; not yet research-evaluated
**Scope:** research only; no forward test, broker, orders or capital. Excluded from the legacy
paper-trading universe (`app.paper.engine.EVALUATION_ONLY_STRATEGIES`); the paper universe is unchanged.
**H-025 boundary:** H-030 is a new, separate hypothesis. It does not replace, re-test,
reconstruct, extend or relabel H-025 or any H-025 artifact, and no H-025 artifact is read or written.

## Why this hypothesis

The 2026-10-23 calibration showed that matrices v1–v4 cannot support a strategy with a
realistic edge, and that `srp-matrix-v5` (portfolio evaluation) can when the strategy is tested
on many weakly correlated assets. Time-series momentum across asset classes is one of the
most widely documented return premia in the literature. H-030 asks whether it is present in a
broad, diversified ETF universe after realistic costs, using MIE's unchanged v5 thresholds.

## Frozen rule

- Input: `close` only (dividend- and split-adjusted).
- Momentum: `close / close.shift(lookback) - 1`.
- Position: +1 if momentum > 0, -1 if momentum < 0, 0 during warm-up or when exactly 0.
- Signal on completed close; the engine executes on the next bar.
- Direction: long/short. Discrete `{-1, 0, +1}`; no volatility scaling (v5 is equal-weight).

## Frozen parameters

Default lookback 252. Pre-registered grid: lookback `{21, 63, 126, 252}` trading bars.
Grid budget: four configurations. One shared lookback per walk-forward fold (v5 rule).

## Frozen universe (24 US-listed ETFs, daily)

| Class | Tickers |
|---|---|
| Equities | SPY, IWM, EFA, EEM, EWJ, VGK |
| Government and inflation-linked bonds | SHY, IEF, TLT, TIP |
| Credit | LQD, HYG, EMB |
| Commodities | GLD, SLV, USO, DBC, DBA, UNG |
| Real estate | VNQ, RWX |
| Currencies | UUP, FXE, FXY |

Crypto is excluded: with equal capital weights its volatility would dominate the portfolio.
Each ETF enters from its own first available bar (v5 handles staggered starts).

## Frozen data

- Source: Yahoo Finance via `yfinance`, `auto_adjust=True` (OHLC adjusted for splits and
  dividends), downloaded once by `scripts/fetch_h030_data.py`.
- **Evaluation period:** from each ETF's first bar through **2024-12-31**, written to `data_csv_h030/`.
- **Sealed holdout:** **2025-01-01 through 2026-09-30**, written to `data_holdout_h030/`. It must
  not be opened, plotted or evaluated before the evaluation verdict is recorded.
- `data_csv_h030/MANIFEST.json` records the download time, `yfinance` version, row counts,
  first/last dates and SHA-256 of every file (evaluation and holdout). The data files are
  not modified after the manifest is written. If data quality checks fail, record the failure
  instead of editing the files.

## Frozen evaluation

- Matrix: `srp-matrix-v5` (hash `a41f5eb49e7a1a4b`), timeframe 1d, `--no-write`.
- Headline costs: 5 bps fee plus 5 bps slippage (unchanged ladder 0/5/10 × 0/5/10).
- **Primary outcome:** the mechanical v5 verdict. "Supported" requires all unchanged conditions:
  deflated Sharpe ≥ 0.95, positive OOS Sharpe, beats equal-weight buy-and-hold, beats
  trade-matched random timing, and robustness ≥ 0.5.
- Command: `mie evaluate --target H-030 --assets <24 tickers> --matrix v5 --csv-root data_csv_h030 --no-write`

## Holdout (only if the verdict is "Supported")

Evaluate once on the sealed holdout with the lookback selected for the final evaluation fold.
Pass: non-negative total net return and maximum drawdown ≤ 30% (same criteria as the Phase 5
retrospective probe). No retry, no tuning.

## Interpretation fixed in advance

- "Not Supported" closes H-030 on this data. The rule, grid, universe, costs and dates must not
  be changed and re-run; any change is a new hypothesis and a new trial.
- Calibration shows ~50% power at true Sharpe 0.5 with 30 assets at correlation 0.3, so
  "Not Supported" is not proof that no premium exists.
- "Supported" plus a passed holdout is evidence about history only. It is not proof of
  profitability. The next step would be a prospective test, not live capital.
- Expected outcome stated before data: the literature reports weaker time-series momentum
  after 2009 than before; "Not Supported" is a plausible result.
