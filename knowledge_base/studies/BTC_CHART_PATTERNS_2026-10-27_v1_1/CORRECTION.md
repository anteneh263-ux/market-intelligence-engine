# Accounting correction v1.1 — original v1 preserved

Original v1 results are in the sibling BTC_CHART_PATTERNS_2026-10-27 directory.
Original production source is preserved in its source_v1 subdirectory.
Post-run reconciliation detected that capital-exhausted buy-and-hold was clamped
to zero equity, losing a USD 248.94 residual negative balance, and assigned a
meaningless zero Sharpe. Strategy ledgers did not exhaust capital.

v1.1 preserves signed ending cash/debt, stops trading and reports Sharpe as
undefined for exhausted accounts. No rule, threshold, parameter, price, funding
rate, period or random seed changes. Benchmark superiority fails closed when
benchmark Sharpe is undefined. This is a transparent bug-correction replay of
already-seen data, NOT a new independent trial or a new holdout.

A synthetic capital-exhaustion regression plus all other new and existing research
tests passed before this replay. All five strategy results must be compared to v1
and remain identical except qualification criteria referencing the invalid benchmark.

Funding marks remain explicit scheduled-open proxies for 4048 events. A bankrupt
unlevered-at-entry perpetual account can require later liquidation as collateral
is depleted by funding. This simplified model does not reconstruct exchange margin
calls and must not be described as an actual exchange liquidation backtest.

No strategy is promoted; original numerical outputs are retained for audit.
