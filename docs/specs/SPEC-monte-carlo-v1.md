# WP5-5 preregistered historical fragility v1

New methodology; existing historical engine unchanged. Scope callable scenario
runner plus actual engine integration, not the full outer gate. Durable frozen
plan is read by expected hash before execution. Caller-supplied BASE must hash its
complete parameters, contiguous positive-price bars, signal positions, costs,
experiment and timeframe. Parameter hash verified; causal callback replays base
positions exactly. Full authoritative-result provenance remains UPSTREAM_REQUIRED.

Phase5 owns append-only BASE/POLICY/SCENARIO-i/REPORT artifacts in one caller-owned
existing empty directory, using exclusive fsync/readback. No automatic root,
repair, resume, migration or overwrite. Run failures preserve evidence. Counters,
DSR and holdout stores are not accepted as writable capabilities.

Every parameter-patch × absolute-cost grid cell receives the same number of
replicates. Explicit seed derives each scenario SHA256 from master_seed,
mc_policy_hash and ordinal; random uint256 stream is SHA256(seed:counter).
Unbiased rejection sampling drives Fisher-Yates. Bernoulli compares against exact
float.as_integer_ratio. No ambient random state or wall clock.

Signal callbacks get only causal price prefixes and copies of immutable variant
parameters. Additional execution delay shifts decision positions before the
historical engine's existing one-bar shift. A missed order preserves previous
target position, including missed exit orders. Cost model delegates to existing
run_backtest. SHUFFLE_TRADES shuffles maximal constant-held-position blocks,
including explicit flat-gap blocks, after cost calculation. This is a pinned
historical fragility approximation, not a prospective execution simulation.

No negative-equity compounding: scenario insolvency absorbs equity at zero and
records total_return=-1/max_drawdown=1/ruined=true; it ALWAYS fails survival and is
reported, never discarded. Invalid/nonfinite numerical generation invalidates the
whole MC report, rather than selectively dropping unfavorable scenarios. Baseline
insolvency refuses the base. All successful scenarios have persisted receipts;
readback and completeness are checked before summary publication.

Survival is the frozen total-return/drawdown rule AND not ruined. Readiness is
PRESERVED iff surviving fraction >= frozen minimum, otherwise DOWNGRADED. There
is no supplied readiness, ranking, preferred parameters, changed DSR or promotion
field. Quantiles use linear interpolation on sorted returns at .05,.5,.95. Policy
and base bytes reverified during and after run; callback argument mutation refuses.
No selection threshold removes a scenario. No scientific evidence outside tests.

## v2 integration extension

Full pipeline uses wp5_5.fragility.v2: optional warmup_rows supply only earlier
training prices to causal signal prefixes while the engine prices the separate
seed+OOS frame. Base hash binds warmup. Each scenario persists its net_returns,
allowing cross-window aggregation without source-data rereads. Scenario seed now
also binds base_run_ref, separating deterministic window streams. Existing v1
checkpoint artifacts are preserved; no historical output is rewritten. These
extensions receive dedicated review and new replay tests as part of integration.
