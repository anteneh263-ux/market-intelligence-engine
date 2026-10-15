# WP6-2K — durable diagnostic bar and intent barrier v1

Date:2026-10-15. Additive journal integration under ADR-MIE-004. Existing reducer
arithmetic unchanged; no engine/store migration or actual prospective activation.

run_bar(state,observation,*,clock) requires OPEN, count3 and exact head from this
instance's successful run_diagnostic, with a detached private captured plan,
strategy output, decision payload and commit. A generic appended decision is not
accepted. Exactly one bar attempt, even when warmup produces no outcome event.

Exact reducer config maps P fields initial_cash,quantity_step,tick_size,max_target,
fee_bps,slippage_bps,max_drawdown,warmup_bars,max_decision_delay_ms,max_quote_delay_ms.
Require config_check on that mapping and byte-equal state.config; state_check;
not stopped/no pending; state.bar_count+1=captured strategy count. For count>0,
last_bar_close_ms=P.start_ms+state.bar_count*timeframe_ms; count0 retains null. This explicitly
rejects plan numerics outside the reducer domain before invocation.
Observation exact existing reducer.bar fields. close_ms=P.start_ms+count*timeframe_ms
and <=P.end_ms; positive close accepted by reducer amount precision8/limit1e12.
receivedUTC>=close and <=captured callback-start; receivedMono<=callback-start;
all times nonnegative signed64. decisionUTC/Mono equal captured callback-finished,
clocksession same as callback. Prior state clock/order and decision-delay bounds
checked before invocation too. Raw observation/bar/receipt/prefix and prior-state
provenance remain upstream; no hash of invented receipt is generated.

Publish exact input state, observation and descriptor with schema_version
p6.bar_invocation.v1,run_id,session_id,diagnostic_commit_hash,decision_event_hash,
input_state_hash,observation_hash,target. Then call fixed reducer.bar once on
copied state/observation and captured target. Freeze return and canonical argument
snapshots immediately; no external callback before freezing. Refuse mutation.
Validate output state and exact bar postconditions: unchanged cash/units/basis/fees/
realizedPnL/config, incremented count, exact observation clocks/mark, consistent
peak/equity, computed risk-stop, and target-derived rounded pending instruction.
No second reducer call; no fill can occur on a bar.

Publish raw reducer output state. If pending, prepare ORDER_INTENT referencing the
captured decision; if stopped, STOP/RISK_STOP; otherwise post-warmup NO_TRADE with
ZERO_QUANTITY. Warmup without risk stop has no new outcome event: event_hash null,
previous WARMUP is the decision link. Do not relabel warmup as evaluated zero size.
For an outcome event, sample clock after output publication; same session, UTC>=
last event, mono>last event; publish typed payload then J. If pending only, sample
again AFTER successful ORDER_INTENT publication/readback; UTC>=eventUTC and mono>
eventMono. Publish barrier exact schema p6.intent_barrier.v1,run_id,session_id,
intent_event_hash,utc_ms,monotonic_ns,clock_session_id. Sample is a conservative
observed post-publication bound, not an authenticated exact disk timestamp.

Finally publish commit exact schema p6.bar_call_commit.v1,invocation_hash,
output_state_hash,event_hash,barrier_hash. Last two nullable (barrier only pending).
After final root/lifecycle check acknowledge count/head if event exists and consume
bar slot. Return p6.bar_call_result.v1,state,event_hash,barrier_hash,commit_hash,
invocation_hash,technical_verification LOCAL_ONLY,runtime_accepted false,
prospective_quote_ready false,external_reference_verification UPSTREAM_REQUIRED.

Any admitted failure terminal INCOMPLETE, last acknowledged head preserved, no
retry/resume or second call. Eight pending-path publications: state,observation,
descriptor,outputstate,payload,J,barrier,commit. Ownership/reentry rules unchanged.
The output is the unmodified reducer state: pending.intent_* remains nominal
callback time, not durable publication time. No quote API, first-observed quote
proof, fill, checkpoint or prospective admission is implemented. A later reviewed
adapter must define barrier-based eligibility/timeout and authentic clock/receipt
ordering before using a quote. Do not feed this state directly to a prospective
quote consumer. No method/code deployment pins are authenticated here.

Tests cover real decision→bar→intent output, durable barriers, exact callback
postconditions, all eight write failure windows, no call before descriptor commit,
warmup/no-trade/risk distinction, clock failures/reentry, spoofed decisions, return
and argument mutation, final root checks and preserved nominal reducer timing.
Independent contract/code review required; test-only temporary roots. H025 excluded.
