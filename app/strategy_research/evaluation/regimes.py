"""Ex-ante regime labelling — trend / range / crisis.

Labels are a property of the PRICE SERIES ONLY, computed from backward-looking
rolling statistics. They never see strategy returns or any outcome, so
regime-conditional performance cannot be gamed. This is the discipline from the
experiment engine: regimes are labelled by an independent rule, never by result.

Rule (frozen):
  vol      = rolling std of log returns (window w)
  trendiness = |rolling mean return| / rolling std  (directional persistence)
  crisis   : vol in the top `crisis_q` quantile of the series
  trend    : not crisis AND trendiness >= its median
  range    : everything else
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def label_regimes(df: pd.DataFrame, window: int = 20, crisis_q: float = 0.80) -> pd.Series:
    close = df["close"]
    ret = np.log(close / close.shift(1))
    vol = ret.rolling(window, min_periods=window).std()
    mean_r = ret.rolling(window, min_periods=window).mean()
    trendiness = (mean_r.abs() / vol.replace(0.0, np.nan))

    labels = pd.Series("range", index=df.index, dtype=object)
    valid = vol.notna()
    if valid.sum() == 0:
        return labels

    crisis_thr = vol[valid].quantile(crisis_q)
    trend_thr = trendiness[valid].median()

    is_crisis = valid & (vol >= crisis_thr)
    is_trend = valid & (~is_crisis) & (trendiness >= trend_thr)
    labels[is_crisis] = "crisis"
    labels[is_trend] = "trend"
    labels[valid & ~is_crisis & ~is_trend] = "range"
    labels[~valid] = "range"
    return labels
