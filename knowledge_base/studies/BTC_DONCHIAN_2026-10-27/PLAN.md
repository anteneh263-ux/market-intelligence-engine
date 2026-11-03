# BTC Donchian historical study — 2026-10-27

User authorised a BTC Donchian/Turtle test in MIE. This is a new, exploratory
historical study, not a replacement H-005 verdict or the full geneZ_fi Turtle
script. No claim of pristine unseen data or prospective evidence.

Frozen before downloading data or inspecting results:
- BTCUSDT spot, Binance public OHLCV, 2018-01-01 through 2024-12-31 UTC.
- Primary 4h; daily reference aggregated from the same complete UTC days.
- Long/cash, full unlevered notional. No shorts, pyramiding, ATR sizing or stop.
- Enter at a completed close strictly above the highest HIGH of the previous
  20 bars (current bar excluded). Exit strictly below the lowest LOW of the
  previous 10 bars. Otherwise retain state; start flat.
- One fixed parameter set, no optimisation, two timeframe evaluations. Daily
  reference cannot rescue a failed primary test. Engine DSR counts one parameter
  set per evaluation; it does not correct all prior MIE research or two timeframes.
- Existing MIE srp-matrix-v4 unchanged, 0/5/10 bps fee × 0/5/10 bps slippage;
  headline 5+5 bps per side (20 bps round trip). Buy-and-hold and matched random
  timing are both evaluated. No new thresholds or automatic promotion.
- Engine: /Users/antenehhailu/MIE_CALIBRATION_WORKTREE, commit
  c41afe43fc70d50140754558fda62a2deea48761, imported read-only without bytecode.
- Study files and outputs owned by this standalone study directory in the current
  workspace; no engine stores, registry, INDEX, forward or paper writes.
- Exclude all holdout paths and 2025+ data. Existing H-025 work is not accessed.
- Verify prefix invariance, future perturbations, prior-bar channel boundaries,
  flat-to-long-to-flat transitions, and complete real input data before evaluation.

Execution limitation: MIE is a close-to-close position-return model shifted by one
bar, not an exchange order simulator. It grants exposure to the next close-to-close
return and models costs on position changes; next-open fills and intrabar stops
are not represented. Results are not directly equivalent to TradingView fills.

Report raw mechanical verdicts with single-asset and historical-selection limits.
No parameter revisions after results. Save source hashes, input hashes, reports,
OOS returns and reproduction command. No claim of independent review.
