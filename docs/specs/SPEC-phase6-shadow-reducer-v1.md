# WP6-2A — pure quote-driven simulation reducer v1

Design candidate, 2026-10-08. Parent ADR-MIE-004. Scope: pure deterministic numerical
transitions with synthetic tests, no store, feed, clock acquisition or activation.
The future coordinator remains responsible for reservations, accepted receipt/segment
chains, actual timestamps, method/strategy identity, durable publication and anchors.
This reducer never labels its output runtime-authorized or scientifically verified.

## Representation and interface

New module app.paper.session.shadow_reducer_v1, METHOD=`p6.shadow.reducer.v1`.
No changes to legacy replay engines or their schemas. Functions `initial(config)`,
`bar(state, observation, target)`, `quote(state, observation)`, `stop(state, reason)`
return a new complete state; input objects remain unchanged. A rejected argument
raises ValueError with a closed reason; it never returns a partially applied state.
Reducer return alone is not a durable execution event.

Config exact fields: `initial_cash, quantity_step, tick_size, max_target, fee_bps,
slippage_bps, max_drawdown, warmup_bars, max_decision_delay_ms, max_quote_delay_ms`.
Decimal fields are canonical nonnegative strings, max8fractional places, value<=1e12;
initial_cash/step/tick>0;0<max_target<=1;fees/slippage<10000;0<drawdown<1.
Integers are nonnegative signed64 excluding bool. Quantum for money/basis/fees is
1e-16. Use exact rational arithmetic, no ambient Decimal context or binary floats.
Buy/sell notionals are exact (8+8places); fees round UP to money quantum. Cost basis
allocation on partial sale rounds DOWN to money quantum; final sale allocates all
remaining basis. Thus bookkeeping preserves basis exactly without repeating decimals.
Realized P&L is gross of fees; fees are separately cumulative and deducted from cash.
Price rounding: buy UP to tick, sell DOWN to tick. Quantity always DOWN to step.

State exact fields: `schema_version, config, cash, units, cost_basis,
cumulative_fees, cumulative_realized_pnl, equity, peak_equity, mark_price,
bar_count, last_bar_close_ms, last_receipt_monotonic_ns, last_action_utc_ms,
last_action_monotonic_ns, clock_session_id,
pending, stopped, reason_code`.
State uses canonical decimal strings: cash/basis/fees/realized P&L max16fractional
places, units/pending quantity max8, mark max9 and equity/peak max17. Midpoint and
equity remain exact, not rounded to the fee quantum. Signed realized pnl allowed.
Absolute state amounts<=1e30; exceeding this supported domain is NUMERIC_INVALID.
mark_price/last_bar_close_ms/last_receipt_monotonic_ns/clock_session_id are null at
initialization only. schema_version=METHOD. stopped boolean. pending null or exactly
`side, quantity, intent_utc_ms, intent_monotonic_ns`; side BUY/SELL, quantity>0.
reason_code null until stopped, then RISK_STOP/OPERATOR_STOP/QUOTE_TIMEOUT.
Quantities must lie on the quantity_step grid; zero units requires zero basis.
Pending SELL quantity<=units, all pending quantities positive and grid-aligned;
pending intent times equal last_action times except terminal QUOTE_TIMEOUT, where
original intent times must be <=last_action times; timeout advances observation/action
times without changing the original intent. bar_count0 requires all clock/mark
fields null, no intent, and exact initial amounts. Later states require nonnull
clock/mark fields; last_action_monotonic>=last_receipt_monotonic. stopped iff reason
is nonnull; pending may remain in terminal state. A state snapshot is an input, not trusted evidence: arithmetic/state shape is checked,
but persisted identity and authenticity require the future coordinator.

Initial cash=equity=peak=initial_cash; remaining amounts0; no pending; bar_count0.
The reducer accepts only config-consistent decimal bounds. Negative cash/units/fees/
basis, equity mismatch or peak<equity is STATE_INVALID. Enforce conservation:
cash+cost_basis+cumulative_fees=initial_cash+cumulative_realized_pnl. No deposits
are supported. No unknown fields.

BAR observation exact fields: `close_ms, close, received_utc_ms,
received_monotonic_ns, clock_session_id, decision_utc_ms, decision_monotonic_ns`.
Clock-session tokens match WP6-0 token grammar. Timestamps nonnegative signed64.
Bar close/receipt times are supplied already-normalized observations; price>0 with
<=8decimal places. received_utc>=close_ms; decision_utc>=received_utc and difference
<=max_decision_delay; decision_monotonic>=received_monotonic and elapsed nanoseconds
<=max_decision_delay*1e6. Cross-event receipt monotonic strictly increases and session
ID cannot change. Bar close_ms strictly increases. New decision UTC must not decrease relative to
last_action_utc_ms and decision monotonic must strictly exceed last_action_monotonic_ns.
Update both last_action fields at every bar decision or accepted quote. Queued
receipts may predate a prior action, but callbacks/actions may not be backdated. Full UTC/coverage/provider validation
is upstream WP6-1, not duplicated here. A bar while an intent is pending is refused,
never implicitly cancels/replaces it. End of bar never liquidates/reset cash/state.

For first warmup_bars observations target must be null; update marks/count only.
Thereafter target is canonical nonnegative decimal<=max_target, max8places. No hidden
strategy callback: target comes from coordinator's already-recorded causal callback.
At a bar, mark equity=cash+units*close; peak=max(previous peak,equity). If
(peak-equity)/peak>=max_drawdown, return stopped RISK_STOP, no new intent. Otherwise
desired_units=floor_to_step(target*equity/close). Difference vs current units becomes
one pending intent; zero difference produces no intent. No fill happens on a bar.

QUOTE observation exact fields: `bid, ask, received_utc_ms,
received_monotonic_ns, clock_session_id` with0<bid<=ask, canonical8place prices.
Requires pending, same clock session and received_monotonic strictly after both last
receipt and intent. received_utc>=intent_utc. If UTC or monotonic elapsed exceeds
max_quote_delay, return stopped QUOTE_TIMEOUT, keep pending, no fill. Otherwise use
this first quote offered to the reducer; proving no prior quote was omitted is the
coordinator's responsibility. Fill reference is bid for sell and ask for buy.

Desired quantity is fixed by bar intent. Buy quantity is capped at quote time to
cash including rounded fees and post-fill max_target exposure measured at quote
midpoint. Choose largest whole quantity_step not exceeding original intent for
which cash_after>=0 and units_after*mid<=max_target*(cash_after+units_after*mid).
Monotonic integer binary search avoids approximate divisions. Sell quantity<=held
units; if net proceeds would require unavailable cash because of extreme costs,
refuse NUMERIC_INVALID. If no buy quantity feasible, clear intent without fill.
Fees and basis change once only. Mark at midpoint after quote, carry open position.
After fill, update equity/peak and apply same drawdown stop. Quotes do not create
another strategy decision. A later quote without pending refuses, preventing reuse.

## Refusals and test requirements

Argument/schema/config errors: INPUT_INVALID; inconsistent state: STATE_INVALID;
reusing a stopped state: RUN_STOPPED; timestamp/session mismatch: CLOCK_INVALID;
bar ordering: SEQUENCE_INVALID; bar with intent: PENDING_INTENT;
quote without intent: NO_PENDING_INTENT; unsupported target: TARGET_INVALID;
arithmetic outside supported domain: NUMERIC_INVALID.
No scientific P&L pass/fail threshold. `stop` accepts only OPERATOR_STOP and preserves
pending/state. State is never resumed in this version.

Required tests: exact buy/sell/fee/basis examples; quote received before intent;
missing/late quote; warmup and target bounds; state continuity and no boundary
liquidation; extreme cost cap; duplicate fill; no input mutation; context-independent
arithmetic; gross realized and cash reconciliation; terminal risk behavior.

This is not WP6-2 completion: durable coordinator, strategy-state serialization,
event payload schemas, clock/feed provenance and anchor trust remain separate work.
