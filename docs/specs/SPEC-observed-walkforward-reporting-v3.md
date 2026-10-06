# Observed walk-forward reporting v3

Scope: fixed schedules, actual-fill gross/net accounting and causal regime reporting
for the existing observed historical window-block profile. New policy, RUN, window,
receipt and final-report versions; existing v1/v2 policies keep their meanings.
No new execution engine or storage owner, no legacy migration, no real-data run.
Phase5 owns governance/evidence in the caller's existing explicit root; historical
window orchestration remains app.strategy_research.evaluation.observed_walkforward,
using the unchanged observed session execution engine. Separate reviewed PR required
before release. Code preservation does not publish, merge or activate the profile.

## Frozen policy

wp5.observed_wf_policy.v3 adds window_policy and regime_policy to v2, including its
validation_plan_hash. The complete policy is bound to reservation inputs/method.
window_policy has mode rolling|expanding, start_ms, train_span_ms, test_span_ms,
step_ms, n_windows. All durations are positive integer grid multiples; n_windows
is 1..10000 and step>=test_span. For i, test start=start+train_span+i*step;
train start=start+i*step for rolling, start for expanding. Explicit windows must
match every generated boundary exactly and remain in the reserved TRAIN/VAL zone.
Existing coverage, timing, no-warmup, min rows and fresh-capital constraints apply.

regime_policy is exactly TRAIN_CLOSE_RETURN_V1 with finite down_threshold and
up_threshold, -1<down<up. Training last close / first close -1 determines DOWN
when <=down, UP when >=up, otherwise NEUTRAL. This is a simple training-return
classification, not an economic or inferred future regime model. Full endpoint rows
are verified against the dataset's committed leaf hashes and exact training endpoints.
REGIME.json is fsynced before PARAMETERS.json/OPEN_OOS and any OOS read. Parameters
bind its hash; native manifest binds the regime-policy hash. Read-only verification
rederives the class from these persisted, source-bound endpoints, with no source read.
Regime labels are descriptive and do not change strategy selection or execution.

## Gross/net reconciliation

Observed native journal orders provide actual filled units, prices and fee/spread/
slippage amounts. Both native fee models are supported: bps-on-notional and per-unit
commission with its minimum. Unknown models refuse. Reconcile each order's cash and
position, profile-implied costs, cumulative snapshot costs and terminal broker state.
All amounts must be finite; costs nonnegative; return denominators positive.
Numeric cash/cost reconciliation tolerance is rel_tol=1e-11, abs_tol=1e-8; this covers
float arithmetic only and never repairs the journal. Native event hashes remain exact.

Let B=net equity at FIRST_CLOSE and C(t)=cumulative fees+spread+slippage.
Gross equity(t)=net equity(t)+C(t)-C(FIRST_CLOSE). Thus for each complete or partial
window, net PnL=gross PnL-incremental modeled costs; gross and net total returns
use common baseline B, so net total=gross total-costs/B. Gross/net per-period
returns each use their own preceding equity denominator; their difference is NOT
reported as a common-denominator cost return. Terminal adjustment replaces a point
at the same timestamp rather than inventing a zero-duration return.

Gross means COST_ADDED_BACK_ON_ACTUAL_FILLS. Cost-dependent position sizes, partial
fills and risk behavior remain the actual path. It is not a separately executed
frictionless strategy. Spread/slippage are already in fill prices and are added back
once only; fees are added back once. Net statistics continue to use the same actual
net return series, frequency identity and complete trial ledger as before.

## Report and verification

Each v3 window report binds selected param_hash, REGIME receipt, complete accounting
points, gross/net metrics, modeled costs, coverage and OK/INVALID status. Parent
receipt binds every report plus the derived summary; TRIAL_RESULT hashes the receipt,
and the committed OUTCOME hashes that result. Final summary is checked against this
chain; rehashing a changed summary or execution/status claim cannot pass replay.
Cached statistics remain explicitly not revalidated by WF replay; the outer pipeline
separately refreshes current ledger statistics. Replay invokes no source/strategy and
creates no reservation. v3 final report has an exact field set and rederived counts,
blocks, timing status and fixed non-prospective/non-continuous claims.

All complete windows receive equal arithmetic weight on total returns/PnL; by-regime
aggregates use the same rule and include counts and exact window IDs. Drawdown summary
is explicitly the worst individual-window drawdown, not portfolio drawdown. Do not
compound across independent capital resets. A partial/stopped window remains visible,
later windows are NOT_EVALUATED, aggregate is null and walkforward_status INVALID.
Ordered checks retain WINDOW_POLICY, PARAM_FREEZE, LOOKAHEAD_GUARD, PURGE_EMBARGO,
COST_APPLICATION, then explicit EXECUTION_COMPLETENESS, WF_HASH. Exceptions before
completion retain counted reservations and partial evidence; no synthetic final result
is manufactured. Schema/input errors fail before reservation whenever preflight can
establish them. This does not implement automatic recovery or insufficient-data repair.

Actual integration: run_observed_pipeline accepts v3 WF with existing observed plan
v1/v2; run_observed_walkforward_trial -> run_windows -> persisted regime/native session
-> execution_accounting -> verified window reports -> summarize_windows -> committed
receipt/outcome. MC/holdout continue using the actual frozen v3 parameters and profile.
Required remaining work: full observed MC perturbation/reporting coverage, reviewed
full-stage real-data pilot authorization and separate release. No whole-Phase5 or
prospective validity claim is made.
