# WP6-2C — durable reducer transitions v1

Bounded additive integration under ADR-MIE-004. app.research owns publication;
app.paper.session.shadow_reducer_v1 owns simulation arithmetic. This is an internal
building block for the future coordinator, not a start command or authorization
boundary. Tests use synthetic observations and isolated temporary roots. No root,
registry, market adapter, strategy callback or external service is created here.
No legacy migration or numerical methodology change.

`TransitionSession(writer, config, *, context_hash)` takes exclusive ownership of
a fresh, open, unclaimed ObjectWriter with no previous blobs. ObjectWriter.claim()
returns a unique in-process token once; later put/close require that token. Direct
calls through retained writer references and duplicate claims refuse
WRITER_OWNERSHIP before I/O. This is cooperative in-process API ownership, not a
security boundary against code reading private Python attributes. Invalid ownership
or parameters before a successful claim leave the writer untouched. All initialization
failures after claim close using the token. The writer's canonical header supplies run_id;
add a read-only `header` property returning a detached copy. context_hash is a
caller-supplied commitment to the future enclosing governed contract; its external
resolution/authorization is UPSTREAM_REQUIRED. Passing a hash is not authorization.
No loaded state, existing transition or resume parameter is accepted. Freeze a
copy of config; validate/initialize it with the pure reducer initial(config) before
claiming the writer. Invalid config leaves the fresh writer usable.

Canonical records use WP0 C/H. New internal schemas, never substitute them for
WP0 session_manifest/checkpoint or anchor records:
- Genesis: schema_version=p6.transition_genesis.v1, run_id, context_hash,
  method=p6.shadow.reducer.v1, state_hash.
- Input: schema_version=p6.transition_input.v1, kind=BAR/QUOTE/STOP, payload.
  BAR payload exactly observation,target; QUOTE exactly observation;
  STOP exactly reason=OPERATOR_STOP. Observations follow reducer-v1 schemas.
- Manifest: schema_version=p6.transition_manifest.v1, run_id, context_hash, method,
  transition_seq (starts1), kind, input_hash, prior_state_hash, prior_commit_hash.
  First prior_commit_hash is genesis hash. Later value is preceding commit hash.
- Commit: schema_version=p6.transition_commit.v1, run_id, transition_seq,
  manifest_hash, state_hash. No circular hash; next manifest commits this record.
State blobs use the existing exact reducer schema and canonical bytes.

Initialization publishes and read-verifies initial state, then genesis. apply(kind,
payload) copies its input, durably publishes input and manifest, then invokes the
existing reducer exactly once, persists resulting state, then publishes commit.
Only after commit succeeds advance in-memory state/sequence/head and return the
commit hash. All publications use ObjectWriter.put (exclusive create, file/dir fsync,
readback). Identical state bytes may deduplicate, but each manifest/commit remains
bound to a unique transition sequence. No application of a segment boundary reset.

The reducer is the only callback in this package. Strategy decisions, actual clock
collection, receipts/finality, manifest code pins, callback timing and intent
publication barriers remain the enclosing coordinator's responsibility. Supplied
observation timestamps are not proof of publication time. In particular, this
helper alone cannot admit a prospective quote or claim the first quote after a
durable order intent. Do not use these commits as independently anchored evidence.
No second arithmetic implementation; same reducer inputs must yield equal states.

Writer/session use is restricted to creating process/thread. Same-thread reentrant
apply during a transition is rejected before I/O and poisons the session. Every
exception during an admitted apply (including bad payload and interrupted writes)
leaves session terminal INCOMPLETE and closes its writer, preserving partial files.
Unknown outcomes cannot be retried or resumed. Exceptions propagate with original
closed reducer/storage reason where available; unexpected exceptions are not made
into stable reason codes. Initialization publication failure also attempts to close the writer. Cleanup
failure never masks the initiating exception. A standalone close failure propagates
and leaves the session INCOMPLETE; no further transition is permitted. A reducer
terminal state gives STOPPED; explicit close gives CLOSED unless already INCOMPLETE
or STOPPED. Neither state permits another transition. Foreign process/thread calls
refuse SESSION_STOPPED without changing the owner's session or closing its writer.

snapshot() returns detached schema_version=p6.transition_status.v1, run_id,
context_hash, lifecycle (OPEN/STOPPED/CLOSED/INCOMPLETE), transition_count,
genesis_hash, commit_hash, state_hash, state, objects (hash->canonical byte length),
technical_verification=LOCAL_ONLY, runtime_accepted=false. The map covers only blobs
published by this session, not the ObjectWriter header. On failure it lists only
successfully returned put operations; partial files may additionally exist. State
is last committed state, never an uncertain computed state. Snapshot is local
bookkeeping, not proof of completeness. No VERIFIED/COMPLETED/scientific status.
The object store inspector can check this inventory only after the writer closes.

Tests: multi-transition buy/sell equals pure reducer; actual canonical manifest exists
before reducer invocation; exact genesis/input/state/manifest/commit hash links;
copy isolation; one reducer call per admitted transition; every publication failure
window before/after write and before/after reducer result; no retries/capital reset;
terminal stop; root cannot reopen; direct writer inspection remains LOCAL_ONLY;
foreign process/thread and reentrant calls cannot allow another transition.
