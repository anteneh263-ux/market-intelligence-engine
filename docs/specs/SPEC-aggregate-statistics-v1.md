# Form B independently recomputed aggregate v1

Implements the remaining R3 WP5-3 Form B alternative without changing the Form A
kernel or existing reports. New read-only `aggregate_statistics` module owns no
store, accepts explicit existing registry/anchor paths, and never reads market
data or writes trial outcomes. No migration, production routing or H025 access.

The caller must supply an independently frozen derivation method hash. The pin
covers this module's source and the Python statistics implementation/runtime;
there is no fallback to whatever happens to be installed. A helper exposes the
runtime hash for preregistration, but evaluation never chooses its own pin.

`derive_aggregate` uses the verified v2 ledger prefix and every completed-valid
result artifact (same authority as Form A). It independently derives each Sharpe
with stdlib mean/stdev and population variance with pvariance, compares the
result to the Form A variance to absolute tolerance 1e-12, and emits the six-field
R3 aggregate plus a hash-bound verification record. Reserved/invalid/crashed
trials still count in N_total. Trial IDs retain reservation sequence order.

`evaluate_aggregate_statistics` reruns that independent derivation against the
CURRENT verified ledger prefix and content-bound results. It compares the entire
verifier record, trial-ID hash, ledger hash, K and method pin; it never trusts a
caller-supplied 'verified' boolean or an unverified scalar. Supplied aggregate
variance must be finite and differ from recomputation by at most 1e-12. The
reported/used variance and DSR always come from recomputed Form A mathematics;
Form B changes provenance, never the frozen estimator or threshold. Output is
new `wp5_3.aggregate_report.v1`, with FORM_B convention and explicit verifier.

The independent verifier is a second numerical implementation, not a claim of
external human signoff, cryptographic authorship or independent source data.
The ledger remains the only trial authority. Tampering with both code and its
preauthorized pin is outside the cooperative local-interpreter threat model.

Required tests: real ledger A/B parity, deterministic replay, stale prefix,
missing result, wrong ID/K/pin/record, nonfinite or mismatching variance, single
trial refusal, reserved/crashed trials counted. Dedicated review before closure.
