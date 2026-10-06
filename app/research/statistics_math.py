"""WP5-3 numerical kernel only; inputs require independent ledger binding."""
import math
from statistics import NormalDist

__all__ = ['calculate_statistics']
_GAMMA = 0.5772156649015329
_DISCLOSURE = ('The deflation benchmark assumes independent trials. MIE trials may be correlated; '
              'N_effective equals N_total without reduction. DSR is an approximation whose error '
              'is not quantified here, not an exact multiple-testing correction.')


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def calculate_statistics(returns, *, n_total, sharpe_values):
    """Compute frozen float64 mathematics, not an authoritative statistics gate.

    Caller must separately prove complete trial/result bindings and threshold
    policy. No annualization, inferred trial count, or N-effective reduction.
    Default frozen minimum T=30, benchmark Sharpe=0, threshold=.95.
    """
    result = dict(psr=None, dsr=None, sr_star0=None, N_total=n_total, N_effective=n_total,
                  K=None, variance_convention=dict(estimator='population', ddof=0, source='FORM_A'),
                  var_sr_trials_recomputed=None, sr_observed=None, moments=None,
                  decision_input=dict(dsr_pass=False), independence_assumption_disclosure=_DISCLOSURE,
                  stats_status='INVALID', reason_code=None, ledger_binding='NOT_PERFORMED')

    def fail(reason):
        result['reason_code'] = reason
        result['dsr'] = None
        result['decision_input']['dsr_pass'] = False
        return result

    if type(returns) is not list or not all(_finite(v) for v in returns):
        return fail('RETURN_SERIES_INVALID')
    if len(returns) < 30:
        return fail('INSUFFICIENT_SAMPLE')
    try:
        n = len(returns)
        mean = math.fsum(returns) / n
        centered = [v - mean for v in returns]
        population_variance = math.fsum(v*v for v in centered) / n
        if not math.isfinite(population_variance) or population_variance <= 0:
            return fail('MOMENTS_INVALID')
        sd = math.sqrt(population_variance)
        scaled = [v/sd for v in centered]
        skew = math.fsum(v**3 for v in scaled)/n
        kurt = math.fsum(v**4 for v in scaled)/n
        sr = mean / math.sqrt(population_variance * n/(n-1))
        denominator_squared = 1 - skew*sr + (kurt-1)*sr*sr/4
        if not all(math.isfinite(v) for v in (skew,kurt,sr,denominator_squared)) or denominator_squared <= 0:
            return fail('MOMENTS_INVALID')
        scale = math.sqrt(n-1)/math.sqrt(denominator_squared)
        result.update(sr_observed=sr,moments=dict(skew=skew,kurt_raw=kurt,T=n))
        if type(n_total) is not int or n_total < 1:
            return fail('TRIAL_COUNTER_INVALID')
        result['psr'] = .5 * math.erfc(-sr*scale/math.sqrt(2))
        if n_total == 1:
            return fail('INSUFFICIENT_TRIALS_FOR_DSR')
        if type(sharpe_values) is not list:
            return fail('SHARPE_VECTOR_LENGTH_MISMATCH')
        result['K'] = len(sharpe_values)
        if not 2 <= len(sharpe_values) <= n_total:
            return fail('SHARPE_VECTOR_LENGTH_MISMATCH')
        if not all(_finite(v) for v in sharpe_values):
            return fail('SHARPE_VECTOR_NONFINITE')
        average = math.fsum(sharpe_values)/len(sharpe_values)
        variance = math.fsum((v-average)**2 for v in sharpe_values)/len(sharpe_values)
        if not math.isfinite(variance) or variance <= 0:
            return fail('DEFLATION_BENCHMARK_INVALID')
        result['var_sr_trials_recomputed'] = variance
        normal = NormalDist()
        benchmark = math.sqrt(variance) * ((1-_GAMMA)*normal.inv_cdf(1-1/n_total)
                     + _GAMMA*normal.inv_cdf(1-1/(n_total*math.e)))
        if not math.isfinite(benchmark):
            return fail('DEFLATION_BENCHMARK_INVALID')
        result['sr_star0'] = benchmark
        result['dsr'] = .5 * math.erfc(-(sr-benchmark)*scale/math.sqrt(2))
        result['decision_input']['dsr_pass'] = result['dsr'] >= .95
        result['stats_status'] = 'VALID'
        return result
    except (OverflowError, ValueError, ArithmeticError):
        return fail('DEFLATION_BENCHMARK_INVALID' if result['moments'] else 'MOMENTS_INVALID')
