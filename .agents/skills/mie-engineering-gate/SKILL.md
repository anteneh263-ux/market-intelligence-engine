---
name: mie-engineering-gate
description: Manually invoked engineering gate for the Market Intelligence Engine. Enforces PLAN, NEGATIVE TEST, IMPLEMENT, INDEPENDENT REVIEW, VERIFY with security checks inside review and verification. Requires an explicit approved work package or locked task contract.
disable-model-invocation: true
---

# MIE Engineering Gate

Manual gate for engineering work in the Market Intelligence Engine.

Sequence, in order, no skipping and no reordering:

`PLAN → NEGATIVE TEST → IMPLEMENT → INDEPENDENT REVIEW → VERIFY`

Security checks are part of INDEPENDENT REVIEW and VERIFY. They are not a
separate optional pass and may not be deferred.

## Authority

1. This skill orchestrates existing MIE rules. It does not replace, weaken,
   reinterpret, or restate them.
2. Precedence, highest first: locked contracts; approved work-package
   instructions; existing MIE governance files; repository fail-closed rules;
   this skill.
3. On any conflict between this skill and a higher authority, the higher
   authority wins and the run stops with `BLOCKED`.
4. This skill defines no canonical document hierarchy and names no governance
   file. The authoritative documents for a run are exactly the ones the active
   work package names.

## Invocation contract

Do not start without an explicit approved work package or locked task contract
supplied by the operator at invocation. Then read, in full, only the documents
that work package names as authoritative, plus the files it places on the
allowlist. Do not substitute, infer, or widen that set.

Required at invocation:

- work-package or contract identifier, and the path where it lives
- objective and definition of done
- exact allowlist of paths that may be created or modified
- exact denylist of paths that must not be touched
- protected or immutable artifacts, including any RED contract tests, with the
  documented expected state of each
- test commands to run, with expected exit codes
- whether final hashes are required, and over which paths
- environment class: `research`, `paper`, or `live`
- for `live` only: the separate explicit authorization and the locked contract
  reference that permit this exact action

Any item missing, unreadable, or ambiguous: stop with `BLOCKED`.

## Environment separation

`research`, `paper`, and `live` are strictly separated. A run operates in one
class only. Never read from, write to, or promote across classes inside a
single run.

Live trading is prohibited unless a separate explicit authorization and a
locked contract both permit that exact action. If either is absent, stale, or
does not name the action, stop with `BLOCKED`. Nothing in this skill authorizes
live execution.

## Stage 1 — PLAN

No writes in this stage.

- Restate the objective and the definition of done in your own words.
- Record the baseline: `git status --short`, `git status --porcelain -uall`,
  `git branch --show-current`, `git rev-parse HEAD`.
- Record every pre-existing modified or untracked path as protected baseline
  state, with a deterministic SHA-256 manifest over every regular file in it.
  Do not clean, stash, stage, commit, ignore, move, rename, or modify any of it.
- Restate the allowlist and denylist as exact paths. Globs are not allowlists.
- Name the deterministic behaviour and fail-closed semantics the change must
  preserve, and the mechanism that preserves each.
- List the negative tests to be written in Stage 2.
- Stop with `BLOCKED` if the plan cannot be stated without inventing authority.

## Stage 2 — NEGATIVE TEST

Write adversarial and negative tests before any production change.

- Each test must have exactly one valid outcome. Reject any test that can pass
  or fail for more than one reason, or whose outcome depends on wall-clock
  time, network state, ordering, locale, or unseeded randomness.
- Cover at least: invalid input, missing input, boundary conditions, the
  failure path the change is meant to close, and the fail-closed default.
- Run them and record the output verbatim, with exact commands and exit codes.

Pre-implementation outcomes are governed by these rules and nothing else:

- A newly written negative test is **expected to fail before implementation**.
  That expected pre-fix failure is required evidence of a real gap. It is a
  normal, passing state of Stage 2 and must never produce `BLOCKED` or
  `REJECTED`.
- If a newly written negative test **passes** before implementation, the test
  is invalid: it does not exercise the gap. Correct or rewrite it, re-run it,
  and record the corrected failure before entering Stage 3. Do not proceed on
  an unexplained pre-fix pass.
- Protected RED contract tests stay in their separately documented expected
  failing or setup-error state. Do not fix, skip, deselect, exclude, move, or
  rewrite them, and do not count their failure against the run.

## Stage 3 — IMPLEMENT

- Touch only allowlist paths. If the work needs a path that is not on the
  allowlist, stop with `BLOCKED` and request an amended work package.
- Make the smallest change that satisfies the objective.
- Preserve deterministic behaviour and fail-closed defaults. Any new failure
  mode defaults to refusal, never to a permissive fallback.
- No new dependencies, plugins, network calls, credentials, or background
  processes unless the work package names them.
- Do not repair, reformat, or opportunistically clean anything outside the
  objective.
- Record every path actually written.

After implementation, every required new test must pass with the exit code the
work package specifies. A post-implementation failure of a required test is
executed evidence that failed: the run ends `REJECTED`. Protected RED contract
tests remain in their documented expected state and are excluded from this rule.

## Stage 4 — INDEPENDENT REVIEW

Review must be genuinely independent. **The implementation session may not
review its own work and may not impersonate, simulate, or role-play a
fresh-context reviewer.** Review evidence is only valid if it comes from a
separate fresh Codex session, or from another explicitly authorized read-only
reviewer, supplied with nothing but the work package, the diff, and the test
evidence. Review evidence produced any other way is missing evidence: stop with
`BLOCKED`.

The reviewer is read-only. It must not edit, create, delete, or move any file,
must not run tests that mutate state, and must not fix what it finds. It
reports findings only. Fixes go back through Stage 3.

The reviewer checks:

- the diff matches the allowlist exactly, and nothing else changed
- the objective is met, and nothing beyond the objective was done
- determinism and fail-closed semantics are preserved
- negative tests are unambiguous and actually exercise the failure paths
- protected artifacts and baseline state are untouched
- research, paper, and live separation is intact
- security: no secrets, keys, tokens, or credentials added or logged; no
  loosened file permissions or executable bits; no new network or shell
  execution paths; no unsafe deserialization, injection, or path traversal; no
  disabled safety check, assertion, or validation

Reviewer verdict: `PASS` or `FINDINGS`. Findings block progression to Stage 5.

## Stage 5 — VERIFY

Collect evidence. Every item is required; a missing item is a failure, not a
gap to explain away.

1. Exact changed paths, compared against the allowlist. Any unexpected path
   fails the run — see the verdict rules for which verdict applies.
2. Test commands and their exit codes, quoted verbatim, for both the pre-fix
   Stage 2 run and the post-implementation run.
3. `git diff --check` clean.
4. Contract compliance: each work-package requirement mapped to its evidence.
5. Unexpected side effects: new, moved, or deleted files; changed permissions;
   modified baseline or protected artifacts; touched configuration.
6. Security: secrets scan over the changed content, permission and
   executable-bit review, and confirmation that no safety control was weakened.
7. Final SHA-256 hashes when the work package requires them, over the paths it
   names.
8. Protected-baseline manifests recomputed and compared byte-for-byte against
   the Stage 1 manifests.

Report evidence as observed output. Do not paraphrase exit codes and do not
report a command you did not run.

### Newly created untracked files

`git diff --check` does **not** prove the contents of an untracked file. It
inspects tracked changes only, and returns clean for a file Git is not
tracking. Treating it as content evidence for a new file is a verification
failure.

For every newly created untracked allowlist path, all of the following are
required:

- exact path proof from `git status --short` (and `git status --porcelain -uall`
  for the file-level enumeration)
- complete read-only display of the file's contents
- a no-index diff against `/dev/null`, or an equivalent byte-level inspection
- the file's SHA-256
- confirmation that it is the only addition relative to the recorded baseline

Do not stage a file merely to make Git display its diff. Staging is an
index-changing action and requires explicit work-package authorization.

## Stop conditions — `BLOCKED`

Stop immediately with `BLOCKED`, before completion, on any of:

- conflict with a contract, governance file, or higher authority
- unclear, missing, stale, or self-contradictory authority
- allowlist, denylist, or environment class that cannot be established exactly
- baseline state that cannot be captured or cannot be verified read-only
- missing evidence, review evidence that is not genuinely independent, or
  evidence that cannot be reproduced
- an unexpected path whose origin cannot be established, or that indicates
  concurrent modification by something outside this run
- any request to act outside the allowlist

Expected pre-implementation negative-test failures and documented protected RED
test states are never stop conditions.

## Prohibited unless the active work package explicitly authorizes that exact action

Committing, pushing, tagging, branching, merging, rebasing, staging, stashing,
deleting, rolling back, force-writing, overwriting an existing file, editing
`.git` internals, changing permissions or settings, installing anything, and
writing outside the repository.

An action implied, adjacent, or convenient is not authorized. Only the exact
named action is.

## Verdicts

End every run with exactly one:

- `BLOCKED` — authority, scope, environment, baseline, or evidence could not be
  established safely, before completion. Nothing was proven either way. State
  the exact blocker and stop without making changes.
- `REJECTED` — implementation, review, security, or verification evidence was
  executed and failed. This includes a post-implementation failure of a
  required test, a review finding, a security finding, and an unexpected path
  that this run caused. State which item failed, and why.
- `VERIFIED` — every required stage and every required evidence item passed.

The dividing line: `BLOCKED` means the run could not be evaluated; `REJECTED`
means it was evaluated and failed. For an unexpected path, ask what caused it —
caused by this run is `REJECTED`; origin unknown or concurrent modification is
`BLOCKED`.

`VERIFIED` means the engineering evidence for this work package passed. It says
nothing about whether a trading strategy is profitable, robust, or approved for
live execution. It is not a research result and it is not a trading
authorization.
