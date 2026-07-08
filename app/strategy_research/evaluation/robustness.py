"""Robustness / stability / generalization scoring — NOT profit.

These three scores summarise the frozen matrix's output into comparable numbers:

  robustness      fraction of (asset × cost/slippage) conditions where the OOS
                  edge stays positive AND credible (DSR over threshold).
  stability       1 − dispersion of OOS Sharpe across parameter perturbations and
                  cost levels (a stable edge doesn't collapse when EMA-12→EMA-8 or
                  costs rise). In [0,1].
  generalization  fraction of assets (out of those tested) with positive OOS
                  Sharpe, times the in-sample→OOS retention (penalises curves that
                  only look good in-sample).

All are bounded [0,1] and reported per strategy so strategies are directly
comparable regardless of their absolute returns.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np


def robustness_score(condition_rows: List[Dict], dsr_threshold: float) -> float:
    if not condition_rows:
        return 0.0
    ok = [1.0 if (r["oos_sharpe"] > 0 and r["dsr"] >= dsr_threshold) else 0.0
          for r in condition_rows]
    return float(np.mean(ok))


def stability_score(oos_sharpes: List[float]) -> float:
    a = np.asarray([s for s in oos_sharpes if np.isfinite(s)], dtype=float)
    if a.size < 2:
        return 0.0
    spread = float(np.std(a))
    scale = float(np.mean(np.abs(a))) + 1e-9
    # High mean-relative dispersion => low stability. Squashed to [0,1].
    return float(max(0.0, 1.0 - spread / (scale + spread)))


def generalization_score(per_asset_oos_sharpe: Dict[str, float],
                         in_sample_sharpe: float, oos_sharpe: float) -> float:
    if not per_asset_oos_sharpe:
        return 0.0
    frac_pos = float(np.mean([1.0 if s > 0 else 0.0
                              for s in per_asset_oos_sharpe.values()]))
    if in_sample_sharpe <= 0:
        retention = 1.0 if oos_sharpe > 0 else 0.0
    else:
        retention = float(np.clip(oos_sharpe / in_sample_sharpe, 0.0, 1.0))
    return float(frac_pos * retention)


def confidence_score(dsr: float, robustness: float, generalization: float,
                     n_bars: int, min_bars: int = 200) -> float:
    """Calibrated confidence in the verdict: strong only when the corrected edge,
    breadth, and sample size all agree. Bounded [0,1]."""
    data_factor = float(np.clip(n_bars / max(min_bars, 1), 0.0, 1.0))
    return float(np.clip(dsr, 0, 1) * (0.5 + 0.5 * robustness)
                 * (0.5 + 0.5 * generalization) * data_factor)
