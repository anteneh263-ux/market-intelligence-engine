# WP6-2J — durable diagnostic strategy invocation v1

Date:2026-10-15. Additive EventJournal method under ADR-MIE-004. app.research owns
publication ordering; fixed app.paper.session.diagnostic_strategy_v1 owns strategy
calculation. Reuses existing immutable writer, no new store, migration or actual run.

run_diagnostic(plan,params,state,*,segment_seq,clock) requires an OPEN journal with
exactly two acknowledged events, MANIFEST_VERIFIED then BAR_ACCEPTED. That typed
BAR_ACCEPTED only establishes local bindings: receipt/bar/prefix authenticity and
prior strategy-state provenance remain upstream. No bar prices enter this fixed
25/75 diagnostic strategy. No arbitrary callback parameter exists.

Inputs are detached bounded primitives. P must pass WP6-1 PLAN validation;
H(P)=M.plan_hash; H(params)=P.params_hash=M.params_hash;
P.strategy_code_hash=M.strategy_code_hash; params.warmup_bars=P.warmup_bars;
P.max_target>=.75. Params/state use exact WP6-2H serialization. State params hash
matches; segment_seq positive signed64=count+1; M.segment_id nonzero and differs
from prior last_segment_hash. The manifest/plan code hash equality is not source
byte authentication or method deployment approval. No full P/M/admission claim.

Publish canonical P, params, input state, then exact invocation descriptor:
schema_version=p6.diagnostic_invocation.v1, run_id, session_id, plan_hash,
params_hash, input_state_hash, segment_seq, segment_hash, prior_event_hash.
Only after descriptor publication/readback and current root/lifecycle checks sample
start clock and call fixed advance once on copied params/state. No callback after
failed descriptor publication; no callback retries on any failure. No hidden state.

clock is an explicit callable returning exact {utc_ms,monotonic_ns,clock_session_id}.
Times nonnegative signed64; session ASCII token. Read start/end/event samples.
All share session; UTC nondecreasing; monotonic end>=start and event>=end; start
UTC>=last event UTC and start monotonic>last event monotonic. Check lifecycle and
root after each callback seam before proceeding. J lacks clock session: continuity
with earlier journal samples and real clock trust remain upstream. Delay bounds
from actual receipt time are not established by these local samples.

Freeze output and canonical argument snapshots immediately on callback return,
before the finished-clock callback or other external callback can mutate them.
Finished-clock sampling follows this bounded copy; it is an observed upper bound
on return time, not a zero-overhead exact return timestamp. Refuse callback input
mutation (canonical byte comparison distinguishes bool/int) and any non-exact
returned state/kind/target. Verify
fixed postconditions independently without invoking advance twice. Output state
has count=segment_seq,last_segment_hash=M.segment_id, same params hash; warmup
null target else .25/.75 by warmup-relative parity. Copy exact primitives before
checks, never caller-defined hooks. Publish output state, then WP6-2H typed
WARMUP/DECISION payload and WP0 event using supplied observed callback times.
Finally publish commit {schema_version:p6.diagnostic_call_commit.v1,
invocation_hash,event_hash,output_state_hash}. This additive commit links the
invocation and event, not a WP0 checkpoint. Acknowledge head/count only after all
publications and final root/lifecycle check; count becomes3.

Return exact p6.diagnostic_call_result.v1: state,target,event_hash,invocation_hash,
commit_hash,technical_verification=LOCAL_ONLY,runtime_accepted=false,
external_reference_verification=UPSTREAM_REQUIRED. Result and snapshots detached.
All admitted errors (including invalid arguments, clock, strategy mutation/exception,
publication uncertainty, reentry) stop journal INCOMPLETE, retain last acknowledged
head and preserve partial bytes. No same-instance second invocation, restart,
automatic fresh identity or repair. Foreign ownership refusal leaves owner intact.

This implements a real local manifest/input-before-strategy call site and durable
strategy output/event, not full governed execution. No reducer/fill invocation,
checkpoint, actual intent-publication barrier, reference resolver, external anchors,
service/clock authentication, CLI or prospective pilot. Scientific acceptance stays
UNRESOLVED. Test-only roots, fixed synthetic clocks and fault injection; no H-025.

Negative-first tests: missing method; causal file existence at strategy call;
exactly once and no call after failed prepublication; all eight publication windows;
callback exception/mutation/invalid output; clocks/reentry; binding mismatch;
warmup/parity; final root substitution and result detachment. Independent review.
