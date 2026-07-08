"""Strategy interface.

A Strategy maps an OHLCV DataFrame to a target-position series in {-1, 0, +1},
decided using information available up to and including each bar's CLOSE. The
backtester shifts positions forward one bar before applying returns, so no
strategy can peek at the bar it trades on (look-ahead is structurally impossible).

Each strategy also declares:
  * `strategy_id`, `name`, `strategy_class`, `direction`
  * `default_params`: the canonical TradingView defaults
  * `param_grid()`: the PRE-REGISTERED search grid. Its size is the per-strategy
    data-snooping budget consumed by the deflated-Sharpe correction.

The implementation here IS the frozen, auditable specification of the strategy.
"""
from __future__ import annotations

import abc
import itertools
from typing import Dict, List

import numpy as np
import pandas as pd


class Strategy(abc.ABC):
    strategy_id: str = "H-000"
    name: str = "base"
    strategy_class: str = "base"
    direction: str = "long_short"          # "long_only" | "long_short"
    default_params: Dict = {}

    @abc.abstractmethod
    def positions(self, df: pd.DataFrame, params: Dict | None = None) -> pd.Series:
        """Target position in {-1,0,+1} per bar (decided at that bar's close)."""

    def param_grid(self) -> List[Dict]:
        """Pre-registered parameter grid. Default: just the canonical defaults."""
        return [dict(self.default_params)]

    # -- helpers shared by concrete strategies ------------------------------- #
    @staticmethod
    def _carry_forward(sig: pd.Series) -> pd.Series:
        """Turn sparse entry signals (+1/-1/NaN) into a held position series.
        Mirrors TradingView strategy.entry reversing/holding until the opposite
        signal. Starts flat (0) until the first signal."""
        pos = sig.replace(0.0, np.nan).ffill().fillna(0.0)
        return pos.astype(float)

    @classmethod
    def _grid(cls, **choices) -> List[Dict]:
        keys = list(choices)
        combos = itertools.product(*[choices[k] for k in keys])
        return [dict(zip(keys, c)) for c in combos]
