"""Statistics for strategy evaluation (pure numpy, deterministic).

`bootstrap_ci` and `benjamini_hochberg` are ported verbatim from the golden-tested
experiment engine (experiment/mem_experiment/analysis/stats.py) so the trading
platform inherits the same audited primitives. `deflated_sharpe` implements the
Bailey & López de Prado (2014) correction — the load-bearing defence against the
data-snooping that the H-001 literature review (Sullivan–Timmermann–White) warns of.
"""
from __future__ import annotations

import math
from typing import Callable, List, Sequence, Tuple

import numpy as np

_SQRT2 = math.sqrt(2.0)
_EULER = 0.5772156649015329  # Euler–Mascheroni


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / _SQRT2))


def _inv_phi(p: float) -> float:
    """Inverse normal CDF (Acklam's approximation)."""
    p = min(max(p, 1e-12), 1 - 1e-12)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    dd = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
          3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((dd[0]*q+dd[1])*q+dd[2])*q+dd[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((dd[0]*q+dd[1])*q+dd[2])*q+dd[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def sharpe_per_period(returns: Sequence[float]) -> float:
    """Non-annualised Sharpe of a per-bar return series (mean/std)."""
    a = np.asarray(returns, dtype=float)
    a = a[np.isfinite(a)]
    if a.size < 2:
        return 0.0
    sd = a.std(ddof=1)
    return float(a.mean() / sd) if sd > 0 else 0.0


def annualise_sharpe(sr_per_period: float, periods_per_year: float) -> float:
    return sr_per_period * math.sqrt(periods_per_year)


def skew_kurt(returns: Sequence[float]) -> Tuple[float, float]:
    """Sample skewness and (non-excess) kurtosis. Normal => (0, 3)."""
    a = np.asarray(returns, dtype=float)
    a = a[np.isfinite(a)]
    n = a.size
    if n < 4:
        return 0.0, 3.0
    m = a.mean()
    s = a.std(ddof=0)
    if s == 0:
        return 0.0, 3.0
    g1 = float(np.mean(((a - m) / s) ** 3))
    g2 = float(np.mean(((a - m) / s) ** 4))
    return g1, g2


def probabilistic_sharpe(sr: float, sr_benchmark: float, n: int,
                         skew: float, kurt: float) -> float:
    """PSR: probability the true (per-period) Sharpe exceeds `sr_benchmark`.
    Bailey & López de Prado (2012). All Sharpes are per-period."""
    if n < 2:
        return 0.5
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr))
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / denom
    return _phi(z)


def expected_max_sharpe(sr_trials: Sequence[float], n_trials: int,
                        fallback_var: float) -> float:
    """Expected maximum per-period Sharpe under the null across `n_trials`
    independent trials (Bailey & López de Prado 2014). Variance of trial Sharpes
    is estimated from `sr_trials` when >=2 are supplied, else `fallback_var`."""
    n = max(int(n_trials), 1)
    if n <= 1:
        return 0.0
    arr = np.asarray(list(sr_trials), dtype=float)
    v = float(arr.var(ddof=1)) if arr.size >= 2 else float(fallback_var)
    v = max(v, 1e-12)
    a = _inv_phi(1.0 - 1.0 / n)
    b = _inv_phi(1.0 - 1.0 / (n * math.e))
    return math.sqrt(v) * ((1.0 - _EULER) * a + _EULER * b)


def deflated_sharpe(returns: Sequence[float], sr_trials: Sequence[float],
                    n_trials: int) -> dict:
    """Deflated Sharpe Ratio. Returns the probability the strategy's edge is real
    AFTER accounting for `n_trials` configurations tried (the data-snooping budget).
    DSR > 0.95 is the pre-registered bar for a credible edge.

    All Sharpes are per-period; annualisation is a monotone rescale and does not
    change the probability."""
    a = np.asarray(returns, dtype=float)
    a = a[np.isfinite(a)]
    n = a.size
    sr = sharpe_per_period(a)
    skew, kurt = skew_kurt(a)
    # Analytic fallback variance of the Sharpe estimator (Lo 2002).
    fallback_var = (1.0 + 0.5 * sr * sr) / max(n - 1, 1)
    sr0 = expected_max_sharpe(sr_trials, n_trials, fallback_var)
    dsr = probabilistic_sharpe(sr, sr0, n, skew, kurt)
    return {
        "sr_per_period": sr, "n": int(n), "skew": skew, "kurt": kurt,
        "n_trials": int(max(n_trials, 1)), "expected_max_sr0": sr0,
        "dsr": float(dsr),
    }


# --------------------------------------------------------------------------- #
# Ported verbatim from experiment/mem_experiment/analysis/stats.py (golden-tested)
# --------------------------------------------------------------------------- #
def bootstrap_ci(compute: Callable[[np.ndarray], float], n: int, B: int = 2000,
                 seed: int = 0, alpha: float = 0.05) -> Tuple[float, float]:
    """Percentile bootstrap CI. Deterministic given seed."""
    rng = np.random.default_rng(seed)
    stats = np.empty(B)
    for b in range(B):
        idx = rng.integers(0, n, n)
        stats[b] = compute(idx)
    lo = float(np.percentile(stats, 100 * alpha / 2))
    hi = float(np.percentile(stats, 100 * (1 - alpha / 2)))
    return lo, hi


def benjamini_hochberg(pvals, q: float = 0.05):
    """BH-FDR. Returns (rejected: bool array, adjusted p-values)."""
    p = np.asarray(pvals, dtype=float)
    m = len(p)
    if m == 0:
        return np.array([], dtype=bool), np.array([])
    order = np.argsort(p)
    ranked = p[order]
    thresh = q * np.arange(1, m + 1) / m
    below = ranked <= thresh
    rejected = np.zeros(m, dtype=bool)
    if below.any():
        kmax = int(np.max(np.where(below)[0]))
        rejected[order[: kmax + 1]] = True
    adj = np.empty(m)
    prev = 1.0
    for i in range(m - 1, -1, -1):
        val = ranked[i] * m / (i + 1)
        prev = min(prev, val)
        adj[order[i]] = min(prev, 1.0)
    return rejected, adj


def bootstrap_sharpe_ci(returns: Sequence[float], B: int = 2000, seed: int = 0,
                        alpha: float = 0.05) -> Tuple[float, float]:
    a = np.asarray(returns, dtype=float)
    a = a[np.isfinite(a)]
    if a.size < 2:
        return 0.0, 0.0
    return bootstrap_ci(lambda idx: sharpe_per_period(a[idx]), a.size, B, seed, alpha)
