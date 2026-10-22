# Attempt006 operational package

Local candidate, 22 October 2026. The runtime root and 45-minute operation are not
authorized by this document. The human approval document and exact source manifest
are kept in the existing `mie-planning` directory, outside runtime evidence.

## Scope and identities

One attempt to prepare existing receipt instance `i-0c5a2a75ca70db228` using the
preserved medium-v1 host kit. Account `989879505232`, region `eu-north-1`, exact
`mie-p6-qualification-001` stack. Receipt may change from `t4g.small` to
`t4g.medium`; source `i-08e89ad2e3feedb8a` stays stopped and `t4g.small`.
The original 18 resources, disks, AMI, subnet, profiles and IAM scope remain bound.

No policy installation, kit upload, new resource, reservation, account upgrade,
secret-value installation, listener, trading or pilot is included. An installed
host is not an operating receipt service or a completed Phase 6.

## Before approval

The complete local package consists of the v3 context/procedure/guest/progress/
watch/execute files plus `checks.py`, `create-root.py` and `baseline005.json`.
The frozen baseline records the actual 005 source paths and hashes, including the
later final-stack read and earlier security-group read. It is configuration
reference data, not a fresh snapshot. Current preflight must independently read
all values again. The baseline deadline is `at(2026-10-21T15:17:43)`.

All source is committed and pinned before approval. The approval review names the
source commit, source-manifest hash, exact new root, account/instance, costs and
one-window scope. Root creation is deferred until that approval actually arrives.
Do not derive approval from these files or the `--execute-approved` flag.

## Approved sequence

Use the existing virtualenv Python, normal interpreter mode (never `-O`),
`PYTHONDONTWRITEBYTECODE=1`, and the prepared dependency directory named in
`context.ENV`. Each operation is invoked separately and its actual result checked
before continuing. Do not place the sequence in a blind shell command chain.

1. **Initialize after explicit approval.** `create-root.py` requires the exact
   review file/hash, source-manifest file/hash and the user's actual approval text.
   It validates those and a clean matching Git commit before making the private
   root, empty `operator/`, authorization and exact script copies. It never calls
   AWS. Existing/partial roots are refused and must not be removed or repaired.
2. **Preflight.** `checks.py preflight` sequentially records 14 read calls, including
   final stack/pair confirmations. It checks exact account/root principal,
   stopped-small pair, 18 resource IDs, original template, disks/credits/profiles,
   exact installed stackpolicy, IAM role/policy identities, scheduler target and
   old005 deadline, and security-group ingress/egress. Any refusal ends forward
   work. It writes the `preflight-summary.json` and `preflight2-*` names consumed
   by the actual procedure. API output is not reused from the historical baseline.
3. **Existing protection and kit.** `procedure.py verify_policy`, then `reuse_kit`.
   Exact policy must already exist; absence/difference is refused, never installed.
   GET only the existing exact S3 object version and verify its complete bytes.
4. **Freeze and observe.** Run `procedure.py watch` as its own monitored process.
   It writes one immutable contract for 45 minutes and executes the pinned v3
   watch-only operator. Require ARMED and a fresh verified observation. Save the
   process/session handle and keep observation active through settlement and stop.
   `watch` must never be repeated if startup is incomplete or ambiguous.
5. **Schedule.** Run `procedure.py schedule` once. Immediately before the request,
   require 2520 seconds remaining; the bounded waiter requires 2510. This removes
   the inverted threshold, but cannot guarantee the API finishes within the margin.
   One previous-template update changes only the stop expression. Raw and observer
   cleanup states may be waited through; only verified complete allows continuation.
   Stop, without a new window, if any deadline/identity/status check fails.
6. **Preview and execute.** `procedure.py create_preview` creates only
   `mie-p6-receipt-medium-v2-006`. Inspect its exact saved four-resource preview,
   resolved references and no broader permissions, then `procedure.py execute`
   invokes the v3 entry and unchanged v2 exact-once guard. It requires the same
   healthy observer, stopped pair and 40–45 minutes remaining. Never run old004/005
   plans. Never retry ExecuteChangeSet after missing/unknown output. AWS may start
   receipt while changing its type; the observer and external stop must already run.
7. **Resize and guest preparation.** `procedure.py after_resize` verifies settled
   medium hardware and unchanged bindings, then explicitly starts receipt at most
   once if it remained stopped. Use `guest.py admission`, `poll receipt-admission`,
   `timer`, `poll receipt-timer`, `prepare`, and `poll receipt-preparation`, respecting
   each actual terminal result and existing time gates. Polling is read-only; it
   never resends a command. Source remains off. One guest timer uses the same
   absolute deadline. No rearming or extending the window is permitted.
8. **Collect bounded logs.** Only after a terminal preparation result, use
   `guest.py collect`, poll its inventory command, collect/poll each advertised
   chunk (maximum 44), then `guest.py decode`. The package limits individual logs,
   combined data and chunks. Verify byte hashes; preserve failed/partial results.
   If there is insufficient time, stop and record incomplete evidence.
9. **Stop and reconcile.** On completion, refusal, error or uncertain result,
   cease onward mutations. `procedure.py interrupt` targets only the exact idle
   observer process and requests its existing protective-stop path. Preparation
   pin failure must not block this action. SIGINT is not a stopped-host result.
   Watch for its actual result; retain external/guest deadlines. `checks.py closure`
   then records a new sequential read batch, confirms stopped hosts and reconciles
   actual requests, deadline, policy, identities, template, observer chain and logs.
10. **Independent outcome review and close.** Review the raw batch and complete
    trace, not only the summary. Record any missing observation, failed call or
    unresolved preparation explicitly. Only then append the operation closure and
    byte/hash inventory. No later writes into the closed root. No retry, rearming,
    root repair or new attempt is authorized by this package.

## Failure and evidence rules

- Closed001–005 remain untouched. No command here can resume005.
- A failed preflight may have no launch contract or observer. Closure reports
  `NOT_STARTED`, separate from fresh stopped-host readbacks.
- A recorded update with unknown reply may leave either the exact old005 deadline
  or the frozen006 deadline. Fresh stack and schedule must agree. Do not manufacture
  an accepted/verified marker. An unrelated deadline is unresolved.
- Any resize/start/guest request requires the new deadline at closure. A medium
  receipt without a recorded execute request is a conflict, not an inferred success.
- Request counts bind service and operation and cap mutations; they describe this
  attempt's record, not all account activity or AWS's own internal operations.
- A missing/broken observer chain yields unverified history even when fresh reads
  show both hosts stopped. `source_stopped_in_all_observations` describes recorded
  samples only; gaps are explicit and continuous source state is never inferred.
- Missing preparation results remain unresolved. Reported success without matched
  command identity and hash-verified log export remains unverified/incomplete.
- `ATTEMPT006_STOPPED_PENDING_REVIEW` establishes only the validated stopped
  configuration in that sequential batch. It is not host/service/pilot acceptance.
- Closure reads do not rely on launch pins remaining healthy; they never issue
  mutations. Each batch uses new names and records start/end times. No batch is
  described as atomic. Failed reads and earlier batches are preserved.
- Actual resource drift, active hosts, replacement, unsupported status or failed
  reconciliation must remain unresolved and needs attention. Never rewrite evidence
  to force a green result. Stop mechanisms can fail; 45 minutes is the locked
  requested window, not a billing cap or a guarantee of AWS response timing.

## Review and validation

The package is locally integration-tested with synthetic AWS responses and actual
entry points. Tests cover stale004 deadline refusal, installed-policy requirement,
role/security boundaries, no-write failure paths, early abort, unknown replies,
observer gaps, command binding, incomplete exports and exclusive root creation.
The additional pre-schedule threshold test proves zero update calls below 2520
seconds. Separate review also checks the preserved v3 integration tests.

Final combined test output, reviewer hashes and source-manifest identity are in
the existing preparation directory. None is evidence of a live 006 run. The next
phase gate remains actual reviewed host preparation, followed by runtime clock/
authentication, independent retention, whole-chain faults/capacity and a separately
approved prospective simulated-money pilot. H-025 is excluded throughout.
