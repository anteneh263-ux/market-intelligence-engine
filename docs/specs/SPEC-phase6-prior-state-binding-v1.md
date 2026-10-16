# WP6-2R — local prior-state continuity binding v1

2026-10-16. Optional bind_prior_state(prior_artifacts,*,params,state,strategy_state)
requires actual bind_intake, event count2 BAR_ACCEPTED, no prior binding or strategy
call. Pure verify_prior_state exact request is manifest,intake,prior,params,state,
strategy_state. Bounded detached snapshot, validated intake and manifest, canonical
params/strategy and reducer state precede publication. No inherited anchor trust.

Genesis mode requires exact p6.genesis.v1 schema and values derived from reducer
initial(plan config), strategy.initial(params), current run/plan. Hash must match
manifest.prior_checkpoint_hash; first segment seq1/zero predecessor required. No
noninitial mutable state, holdings or future mark can enter genesis.

Checkpoint mode requires full exact bundle accepted by verify_checkpoint, and its
K hash equals current manifest prior reference. Current/prior manifests agree on
run,plan,params,strategy code,engine version and engine code hash. Full reducer and
strategy input bytes equal prior artifacts, config equals plan, stopped or pending
states refuse. Current segment follows previous seq and hash; previous segment in
current validated intake matches prior manifest segment. Hash of current ordered
prefix excluding final segment equals K.accepted_prefix_hash. Prior last receipt
sequence points inside supplied current chain and precedes current accepted receipt;
its timestamps cannot exceed prior checkpoint event. That scalar/time check does
NOT authenticate the prior receipt ancestry; prior bound intake/quote artifacts are
not supplied. Result explicitly keeps prior_receipt_ancestry_verified=false.

Reducer last bar close equals current segment start. Current receipt session equals
prior state clock session; monotonic strictly after prior checkpoint event, UTC
nondecreasing; current manifest persistence is no earlier than prior event. No clock
reset, crash recovery or same-run restart authorization. External current stream,
source clock and independent prior anchors still require full admission controls.

Three durable publications: detached package, validation result, linking
p6.prior_state_binding.v1 {run_id,session_id,intake_binding_hash,package_hash,
validation_hash,prior_hash,bar_event_hash}. Final root/lifecycle checks precede
private acknowledgement; journal head/count unchanged. Failures terminal INCOMPLETE.

Bound run_diagnostic requires exact params and strategy input; v3 descriptor extends
intake v2 with prior_state_binding_hash. Bound run_bar requires the complete exact
reducer input state. Unbound prior-state paths preserve earlier local behavior;
this is not yet mandatory runtime admission. No arithmetic changes. Result remains
LOCAL_ONLY/runtime_accepted=false. Pure verifier also reports anchor_verified=false.

Tests use authentic temporary local checkpoint publications for a second synthetic
segment plus exact genesis initialization, input/identity/time/substitution failures,
three uncertain publication windows, ownership/reentry and final root checks. No
real evidence, historical records, H025, provider or service is changed.
