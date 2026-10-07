# Ordered R3 statistics envelope v1

New read-only evaluate_statistics_report derives actual inputs from the registry
and committed result artifacts, then emits R3 numerical fields, ordered checks,
reason_code, independence disclosure and canonical stats_hash. References program,
trial, verified ledger prefix and bound report hash. No store ownership, market
data reads, migrations, trial writes or legacy method changes.

Exact check order: RETURN_SERIES, MOMENTS, TRIAL_COUNTER, N_DOMAIN,
SHARPE_VECTOR_BINDING, CROSS_TRIAL_VARIANCE, PSR_COMPUTE, DEFLATION_BENCHMARK,
DSR_COMPUTE, INDEPENDENCE_DISCLOSURE, THRESHOLD_POLICY, STATS_HASH. First failure
sets reason_code; later checks remain NOT_EVALUATED. Hash seals invalid reports
too without representing their later STATS_HASH check as evaluated. N1 reports
PSR but fails N_DOMAIN and can never yield DSR/pass.

Input API is intentionally authoritative: explicit registry/anchor, program/trial,
complete hash-keyed result artifacts and threshold policy. It DERIVES counter and
Form A vector rather than accepting caller-provided N or SR values. Form B is an
optional aggregate bundle plus independently frozen expected method pin. An
incomplete/mixed Form B input refuses. This is not a byte-identical implementation
of the R3 draft's caller-supplied-input API. Numerical fields and ordered checks
follow its contract. Direct moment-only inputs are not accepted by this API;
actual hash-bound return artifacts remain required to prove their provenance.

Preliminary moments from candidate artifact bytes are never evidence of binding:
ledger_binding remains NOT_PERFORMED until the actual complete result verification.
Read actual registry state at TRIAL_COUNTER; N_effective always equals N_total.
Recheck prefix equality when binding complete Form A and optional Form B. A concurrent
ledger advance refuses instead of mixing counts/observations. Both forms use the
same frozen numerical kernel. Form B independently recomputes the aggregate and
verifier record. Variance mismatch fails CROSS_TRIAL_VARIANCE; zero variance fails
DEFLATION_BENCHMARK. Supplied variance is never used to change DSR.

The .95 threshold is frozen. All policy changes (including self-declared approval
strings) refuse; an authorized future threshold-change protocol needs a separate
version. An additional stricter holdout threshold in a validation plan is a
holdout prerequisite, not a modified DSR statistics threshold. Invalid binding,
N1, invalid math or invalid policy clears DSR/pass.

Historical pipeline calls this actual verifier, persists STATISTICS.json and
rechecks it at holdout prereqs. Existing Form A/B reports are unchanged. No external
human signoff, exact multiple-testing correction, profitability or production claim.
Dedicated review and real-ledger synthetic tests required.
