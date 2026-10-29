# VANTA-SMA200-SCREEN-v1

Exploratory historical compatibility screen, authorised separately on 2026-10-29.
Not a new execution engine, durable runtime store, evaluation matrix, promotion,
or H-025 test. No protected artifact was read or written. Output owner is this
reviewable diagnostic document and its adjacent JSON; no MIE evidence store is used.

Base MIE commit: 30bc9e4d1874ef9add8128f107c1c4ac530ebf71.
Data: bundled data_csv/BTCUSD_1d.csv, 3111 daily bars, 2018-01-01 to
2026-07-08. Hash is recorded in the adjacent JSON. Exchange provenance is not
established here; these are not verified Vanta reference prices.

## Method

Independent SMA-200 long/cash rule. Prior closed-bar signal executes at next
open. Buy with available equity, hold fixed asset units until exit, no daily
rebalance. No parameter tuning or strategy selection. Start with unit cash,
use 200 earlier bars for warm-up. Full-window start and January starts are
exploratory overlapping suffixes, not independent trials or walk-forward folds.

Track first dates exceeding a 5% intraday low proxy, 5% challenge EOD drawdown,
8% main EOD drawdown, and reaching 60 days without a state-change order.
EOD high-water includes initial capital. Inactivity clock starts at the
scenario start, including while flat. Confirm exact validator clock semantics
before using these diagnostics for eligibility decisions.

Two scenarios: zero fees/funding, and illustrative 8 bps per side (3 bps spread
plus assumed 5 bps slippage) plus 10.95% simple annual carry on daily open
notional. Actual instrument funding and slippage are not supplied. Neither
scenario reproduces Vanta leveraged accounting or validator settlement.

## Results

All nine overlapping start scenarios trigger at least one screened rule in both
cost scenarios. Eight trigger a 5% challenge EOD breach; the 2026 start remains
cash with zero orders and triggers inactivity. With a 2025-01-01 start, the
zero-cost proxy first exceeds 5% challenge EOD drawdown on 2025-01-07 and 8%
main EOD drawdown on 2025-01-09. With a 2024-01-01 start both EOD thresholds
are first exceeded on 2024-01-12.

Do not interpret the JSON's unrestricted curve returns as funded returns:
calculation continues after breaches only to diagnose rule incompatibilities.
No payout, ranking or expected monetary profit has been estimated. Daily OHLC
cannot validate the intraday path or exact breach time. Lower exposure would be
a new strategy variant requiring separately declared testing; it is not tested.

Conclusion: no basis to pay for mainnet participation with this full-exposure
SMA-200 configuration. First obtain instrument-specific funding, reference price
and validator accounting equivalence before any exact eligibility claim.

## Sources checked 2026-10-29

- https://github.com/taoshidev/vanta-network/blob/main/README.md
- https://github.com/taoshidev/vanta-network/blob/main/docs/miner.md

Docs describe 300 Theta minimum collateral, at least 61 trading days, >10%
crypto challenge return, ranking qualification, and drawdown/inactivity limits.
Online main-branch docs are mutable and were not pinned to a Vanta commit here.
No successful challenge or collateral safety claim follows from this screen.

## Reproduce and validation

    python scripts/vanta_sma200_screen.py --csv data_csv/BTCUSD_1d.csv
    python -m unittest discover -s tests -p test_vanta_sma200_screen.py -v

Implemented: standalone script. Executed: bundled-data screen and four isolated
unit tests; four passed. Not executed: full repository suite, official validator
integration, testnet or mainnet. No network transactions or credentials used.
