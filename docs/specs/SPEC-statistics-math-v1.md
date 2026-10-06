# WP5-3 numerical kernel v1

Internal calculate_statistics(returns, n_total=..., sharpe_values=...). Implements
master R3 §3B float64 definitions: sample Sharpe ddof1; biased standardized moments
using population deviation; RAW kurtosis; PSR benchmark0; DSR expected maximum using
population variance over supplied valid-trial Sharpes, N_effective=N_total. T>=30,
no annualization, zero variance/nonfinite/nonpositive denominator fail closed.
N=1 reports PSR, null DSR/benchmark and false pass. K>=2 and K<=N; failed/reserved
trials remain included in supplied N. Fixed default threshold .95 only.

Explicitly NOT the full authoritative WP5-3 evaluator: ledger_binding is always
NOT_PERFORMED. Caller must separately verify complete ordered trial/result vector
and policy authorization; this kernel cannot prove provenance and must never be
used alone to open holdout. No Form B or production integration claim. No stats_hash
until full bound report composition; mathematical status is scoped to arithmetic.

Independent oracle: 30 alternating -1,+1 observations imply mean0, sample
variance30/29, skew0, raw kurtosis1 and PSR=.5. Trial Sharpes[0,2] have population
variance1. N=2 gives q=Phi^-1(1-1/(2e))=0.90045259663779034114615151421376255;
benchmark=0.51975534428059392454489126406228;
DSR=0.00256330939348034853464624070078.
Oracle independently evaluated with 60-digit Decimal, erf power series and 200
bisection steps; implementation uses float64 NormalDist inverse CDF and math.erfc.
No external dependencies installed. Independent review required before integration.
