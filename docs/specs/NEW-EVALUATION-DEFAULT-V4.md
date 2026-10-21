# V4 defaults for new evaluations

## Scope and activation boundary

This revision adopts the already defined `srp-matrix-v4` for new historical
single/batch evaluations. It does not define a new scientific method or matrix.
The change takes effect in applications running the revision containing this PR;
older checkouts or running processes retain their previous defaults. It is not an
engine retirement, evidence migration, Phase 6 completion or deployment action.

PR14 and PR15 introduced v3/v4 separately. A subsequent fixed comparison of six
preselected non-H-025 strategies on BTC/USD and SPY repository CSVs (18 evaluations)
found zero final verdict changes, one changed random-benchmark criterion (H-029),
and only reporting/identity changes from v3 to v4. It does not establish outcomes
for other strategies, vendor provenance, profitability or prospective validity.

## Selection contract

| Entry point | Omitted matrix in this revision | Explicit legacy selection |
|---|---|---|
| Standalone strategy-research CLI | v4 | `--matrix v1`, `v2`, `v3`, `v4` |
| `mie evaluate` and `mie batch` | v4 | same flags |
| Research Core `Orchestrator.run`, including `run_batch` | v4 | `matrix_version="v2"`, etc. |
| Core `resolve(None)` and `resolve("latest")` | v4 | aliases and canonical IDs retained |
| `/api/engine/evaluate` | v4 | JSON `"matrix": "v2"`, etc. |
| Direct `pipeline.evaluate`, `EvalRequest`, `DEFAULT_MATRIX` | v1, unchanged | pass a matching request and matrix explicitly |
| `/api/strategy-research/evaluate` | v1, unchanged | legacy route has no matrix selector |
| Discovery and Pine research workflow, CLI and Python | v2, unchanged | existing selectors retained |
| Safety and forward workflows | v2, unchanged | existing selectors retained |
| Standalone dossier-generator helper without a matrix | v2, unchanged | pass the matrix explicitly |

Core reports/catalogue mark v4 active. CLI and API defaults use Core's
`ACTIVE_VERSION`; this removes their duplicate literal v2 defaults. Other
workflows keep their explicit pins. A full Orchestrator evaluation passes its
resolved matrix to the dossier generator even though the standalone helper's
compatibility default stays v2.

`latest` is a convenience selector for new work, not a reproducibility pin. Use
an explicit canonical version together with its hash, request and data identity
when reproducing a historical result.

## Result-schema compatibility

V4 removes `headline.sharpe_ci95` and supplies `sharpe_asset_range`,
`sharpe_asset_count` and `sharpe_uncertainty` (`status="not_estimated"`). A client
that omits `matrix` must handle the v4 schema; to retain the old schema, send
`"matrix": "v2"` explicitly. No confidence interval is newly estimated.

All existing matrix hashes remain fixed:

| Matrix | Hash |
|---|---|
| v1 | `a740f3acbf6572d6` |
| v2 | `4c2aa61eccfd276c` |
| v3 | `3b838b31e151d7d1` |
| v4 | `0ee7bc22dac18f76` |

The evaluator, thresholds, strategy code, matrix definitions and explicit-version
outputs are unchanged. The adoption chooses an existing version rather than
mutating v1/v2 behavior under the same version identifier.

## Storage and preserved history

Changing defaults writes no results. Existing write switches remain unchanged:
Core, unified evaluate/batch and the modern API default to writing; standalone
CLI requires `--write`, and the legacy API defaults to no write. Use
`mie evaluate ... --no-write`, Core `write=False`, or API `"write": false` for
inspection without report writes. There is no new automatic corpus batch.

When explicitly running a writer, the existing per-version archive and latest
pointer rules apply. A new v4 archive is separate from v1-v3 archives. Latest
pointers and current INDEX/meta summaries can change, and repeated writes to the
same version can replace its current file: this is not an append-only evidence-store upgrade. Existing dossier
creation does not overwrite an already present dossier by default.

Tests write only test-owned temporary directories, under `TEST-UNCERTAINTY`.
No actual corpus evaluation, discovery/research workflow, scientific registry,
forward workflow, H-025 artifact or AWS service is run or modified by adoption.
Existing historical reports and the separate completed comparison stay intact.

## Verification

Before implementation, tests expecting the new defaults failed on the prior v2
behavior, including the missing v4 archive from a default Core evaluation.
Entrypoint tests exercise actual argument parsing, the Core adapter and ASGI
routing while intercepting evaluation. They cover omitted/default and explicit
v1-v4 choices, catalogue output, low-level/legacy v1 compatibility and retained
v2 pins without starting those workflows.

The persistence test executes the real evaluator and Core → Knowledge Base writer
with deterministic synthetic fixture data in a temporary directory. It verifies
that omitted matrix now writes the v4 fields/archive and leaves the existing
v1-v3 archive bytes intact. Existing legacy fixture, benchmark, uncertainty and
causality regression tests remain applicable. These are bounded implementation
checks, not a new strategy-validation experiment.


Executed candidate validation: the seven relevant regression suites passed
**204 tests, with 1 deliberately deselected older duplicate H-025 causality test**
(0 failures, 0 skipped). This includes the registry-wide synthetic causality
coverage already established in PR15. A separate reviewer independently passed
110 scoped tests; these overlap with the 204 and must not be added together.
Fresh-process CLI catalogue and matrix-list smoke checks confirmed v4 selection
and explicit v2 compatibility without evaluating a strategy. No full-repository,
real-market or deployed-service validation is claimed.
