# WP5-4 pinned CPCV adapter v1

Implementation under the user's renewed full-Phase5 request. skfolio 1.0.0 is
installed ONLY in /private/tmp/mie-phase5-cpcv-runtime for isolated verification;
project .venv and historical engines remain unchanged. Reproducible wheel hashes
are recorded in requirements-phase5-cpcv-macos-py314.lock. Production deployment
requires an explicitly selected environment and same trusted pin, not implicit
installation. This supersedes the historical round's 'not installed' development
scope, not its scientific restrictions.

MIE accepts exact request experiment_id, cpcv_applicability,
applicability_justification, sample_index, folds_policy, pinned_lib.
Caller must supply the preregistered applicability_hash from a durable outer
validation plan. The pure adapter explicitly reports UPSTREAM_REQUIRED; a hash
alone does not prove chronology. Applicability hash includes all configuration
and justification. NOT_APPLICABLE requires nonblank justification and empty
samples/null policy/null dependency; it never accesses/imports the library.

Required/optional-used policy: n_folds>=3, 2<=n_test_folds<n_folds, integer
purged_size/embargo_size/embargo_ms>=0, at most10000 combinations. Metadata samples
follow WP5-1 and strict feature order. Equal base-size contiguous partitions put
remainder in the last block. skfolio receives zero-valued placeholders only.
Its positional purge/embargo output must match a separately implemented native
index oracle exactly. Three 12-row hand splits are checked before each actual
split. MIE then additionally purges every training label overlapping any test
label, and removes training feature timestamps in each test-label-end embargo.
No adapter identity is accepted: MIE hashes experiment, complete samples, policy,
and normalized index sets. Empty training folds refuse.

Pin covers version and all recorded Python/native binary and METADATA file
contents of the19 runtime distributions. Verify before/after splitting. Trusted
build/operator generates a pin independently; evaluator never accepts current
runtime automatically. Cooperative interpreter/local filesystem boundary:
not protection against hostile monkeypatching, metadata forgery plus a rewritten
trusted pin, or an adversarial dependency loader. Pin+fixtures fail closed;
no fallback split on missing dependency, mismatch or extra/missing fold.

Architecture: no durable store and no execution authority added. Adapter produces
fold diagnostics only. Outer plan freezing and final gate integration remain
separate. Numerical validation must run against the actual isolated runtime;
base environment missing-library tests do not count as external parity tests.

Primary references:
- https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html
- https://github.com/skfolio/skfolio/blob/main/src/skfolio/model_selection/_combinatorial.py

Non-applicable branch marks configuration/dependency/parity/normalization checks
PASS as vacuous under the explicitly justified skip, retaining the closed check
status vocabulary PASS/FAIL/NOT_EVALUATED. No library was evaluated on that branch.
