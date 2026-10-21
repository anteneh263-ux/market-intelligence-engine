# Receipt update-state control v3

This local candidate corrects the orchestration refusal observed in closed
attempt005. Its raw CloudFormation reads showed `UPDATE_IN_PROGRESS`,
`UPDATE_IN_PROGRESS`, `UPDATE_COMPLETE_CLEANUP_IN_PROGRESS`, then `UPDATE_COMPLETE`.
The old waiter rejected the third response. An AST-isolated replay of that actual
preserved waiter reproduced the failure without AWS calls.

## State contract

| AWS state | Observation and historical chain | Admission to the next action |
| --- | --- | --- |
| `UPDATE_IN_PROGRESS` | Wait within the existing deadline | Refuse |
| `UPDATE_COMPLETE_CLEANUP_IN_PROGRESS` | Wait within the existing deadline | Refuse |
| `UPDATE_COMPLETE` | Accept subject to identity, clock and ownership checks | Eligible for the remaining action-specific checks |
| Failure, rollback, unknown or any other status | Refuse; observer enters protective stop | Refuse |

`progress.py` is the shared, exact allowlist. Its waiter is read-only, at most 36
polls with five-second intervals, with verified health checks before and after
each read. Both the fresh AWS response and the verified observer must report
`UPDATE_COMPLETE` before it returns. A transient state never renews the frozen
deadline or authorizes a retry. The observer does not treat cleanup as settled
when confirming shutdown.

## Implementation and candidate call sites

- `watch-operator.py` is a separately versioned watch-only operator. It cannot
  update a template or start an instance. It initially requires the exact stack
  to be settled and the exact source/receipt pair stopped. It observes the three
  allowed statuses, protects the source-off boundary, and retains protective
  owned-instance stopping and unknown-outcome/no-repeat handling.
- `execute-live.py` pins the unchanged v2 execution adapter and guard. Its v3
  Supervisor verifies the new process command, v3 event schema, control version,
  complete hash chain, contract, ownership, source state and continuous clock.
  Historical cleanup is permitted; latest cleanup is refused for execution.
  Execution still requires both hosts stopped and 40–45 minutes remaining.
- `context.py:health` calls that same Supervisor for procedural health, permitting
  receipt state transitions while requiring the source stopped. Its default is
  latest settled; only bounded polling explicitly requests `settled=False`.
- `procedure.py:schedule` and `after_resize` call `wait_stack`, which delegates to
  the shared waiter. The actual candidate `watch` and `execute` process commands
  use the v3 files. `execute` retains `os.execve`, avoiding an outer timeout that
  could orphan an AWS child. `guest.py` uses the same context health before the
  preserved medium-v1 preparation commands.
- The candidate verifies the existing exact stack policy; it has no policy-install
  call. It reuses the existing exact S3 kit version, with no upload. Pair readbacks
  are sequential to avoid concurrent credential-refresh bursts; no mutation retry
  was added.
- Protective interruption remains available if preparation pins fail. It matches
  the complete operator command and waits for a fresh idle observation. Malformed
  unrelated process rows are skipped. SIGINT is a protective-stop request, never
  proof that the instance stopped.

The candidate script command surfaces are individually invoked operations, not an
unattended whole-phase runner. Do not invoke them on a closed attempt or invent
missing preflight/approval files. A future operation still requires its complete
reviewed preflight and closure procedure, source pins, separate approval and root.

## Version and preservation boundary

This is operational control version `receipt-update-states.v3`, emitting
`p6.qualification_operator_event.v3`. Original operator, v1 and v2 guards/adapters,
kit, templates, scientific evaluation versions and historical results are unchanged.
The v2 exact-ARN execution intent and immutable intent file semantics remain v2.
The prospective candidate change-set name is `mie-p6-receipt-medium-v2-006`, because
it uses that unchanged v2 intent protocol; it is not the closed004 or005 change set.
H-025 is excluded and untouched.

The candidate root is explicitly named in `context.py` as
`/Users/antenehhailu/.codex/visualizations/2026/10/01/01a0f6c9-d66b-7d51-85fa-da98aabc6ae7/mie-planning/phase6-host-preparation-run-006`.
It has NOT been created or authorized by this local change. Importing these modules
creates no root and makes no AWS call. CLI preparation paths require an existing
owner-only (0700) root, separately recorded authorization, reviewed-document/source-pin
hashes, a clean pinned commit and immutable preflight artifacts. The procedural
watch contract is a single 45-minute window; Supervisor enforces that exact span.
There is no pilot, runtime receipt service, trading or phase-completion authority.

## Validation and its limits

`tests/test_phase6_update_states_v3.py` includes:

- byte/hash-verified replay of the four actual005 reads (provenance beside fixtures);
- cleanup-to-complete and failure/rollback/unknown/expiry/finite-wait cases;
- rejection of latest cleanup before any execution API call;
- real ObjectWriter/watch chain through v3 Supervisor and live main to the preserved
  exact-once v2 executor, with synthetic AWS responses;
- actual schedule call site through general health, verified chain and shared waiter;
- source-off, receipt-transition, schema/process binding and protective-stop cases;
- malformed unrelated process and dirty-worktree protection regressions;
- refusal before AWS/root creation when candidate authorization/root is absent.

All integration tests substitute AWS wire responses and process effects. They prove
local control wiring, not AWS capacity, policy enforcement, deployed clock/auth,
independent retention, profitability or a completed Phase 6 pilot. Raw005 fixture
copies remain historical observations; synthetic results are never substituted
for the closed attempt. No AWS call was made while implementing this change.

Run with the prepared local test environment and no bytecode/cache writes:

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/private/tmp/mie-phase6-qualification-test-20261018 \
/Users/antenehhailu/Desktop/market-intelligence-engine/.venv/bin/python \
-m pytest -q -p no:cacheprovider -o addopts='' tests/test_phase6_update_states_v3.py
```

Dedicated independent review approved the six candidate source files after the two
protective-interruption findings were corrected. The local completion record in
`mie-planning/phase6-receipt-recovery-20261021` contains review hashes, final combined
test output, original-version preservation and closed-attempt inventory checks.
