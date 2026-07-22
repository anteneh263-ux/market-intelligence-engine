"""The four reference strategies, implemented from canonical TradingView
built-in logic. Each class docstring states the exact rule it encodes.

These four are the gold-standard templates; the remaining 19 subclass the same
`Strategy` interface with no change to the evaluation engine.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from app.features.indicators import ema, rsi as wilder_rsi
from .base import Strategy


def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


class BarUpDnStrategy(Strategy):
    """H-002 BarUpDn. Canonical built-in:
        up  bar: close > close[1]  -> go long
        down bar: close < close[1] -> go short
    Always in the market once started; flips on each bar's direction. This is
    effectively a 1-bar momentum/noise rule and a natural null-ish baseline."""

    strategy_id = "H-002"
    declared_inputs = {"close"}
    name = "BarUpDn Strategy"
    strategy_class = "price-action-momentum"
    direction = "long_short"
    default_params: Dict = {}

    def positions(self, df: pd.DataFrame, params: Dict | None = None) -> pd.Series:
        c = df["close"]
        up = c > c.shift(1)
        dn = c < c.shift(1)
        sig = pd.Series(np.nan, index=df.index)
        sig[up] = 1.0
        sig[dn] = -1.0
        return self._carry_forward(sig)


class MACDStrategy(Strategy):
    """H-010 MACD Strategy. Canonical built-in:
        macd   = ema(close,fast) - ema(close,slow)
        signal = sma(macd, signal_len)
        hist   = macd - signal
        hist crosses ABOVE 0 -> long ; crosses BELOW 0 -> short
    (i.e. MACD line crossing its signal line). Long/short, always in market."""

    strategy_id = "H-010"
    declared_inputs = {"close"}
    name = "MACD Strategy"
    strategy_class = "trend-crossover"
    direction = "long_short"
    default_params = {"fast": 12, "slow": 26, "signal": 9}

    def positions(self, df: pd.DataFrame, params: Dict | None = None) -> pd.Series:
        p = {**self.default_params, **(params or {})}
        c = df["close"]
        macd = ema(c, p["fast"]) - ema(c, p["slow"])
        signal = _sma(macd, p["signal"])
        hist = macd - signal
        prev = hist.shift(1)
        sig = pd.Series(np.nan, index=df.index)
        sig[(hist > 0) & (prev <= 0)] = 1.0
        sig[(hist < 0) & (prev >= 0)] = -1.0
        return self._carry_forward(sig)

    def param_grid(self) -> List[Dict]:
        return self._grid(fast=[8, 12], slow=[21, 26], signal=[9])


class RSIStrategy(Strategy):
    """H-020 RSI Strategy. Canonical built-in:
        rsi crosses ABOVE oversold  -> long
        rsi crosses BELOW overbought -> short
    A classic mean-reversion oscillator rule. Long/short, held until opposite."""

    strategy_id = "H-020"
    declared_inputs = {"close"}
    name = "RSI Strategy"
    strategy_class = "mean-reversion-oscillator"
    direction = "long_short"
    default_params = {"length": 14, "oversold": 30, "overbought": 70}

    def positions(self, df: pd.DataFrame, params: Dict | None = None) -> pd.Series:
        p = {**self.default_params, **(params or {})}
        r = wilder_rsi(df["close"], p["length"])
        prev = r.shift(1)
        sig = pd.Series(np.nan, index=df.index)
        sig[(r > p["oversold"]) & (prev <= p["oversold"])] = 1.0
        sig[(r < p["overbought"]) & (prev >= p["overbought"])] = -1.0
        return self._carry_forward(sig)

    def param_grid(self) -> List[Dict]:
        return self._grid(length=[7, 14, 21], oversold=[30], overbought=[70])


class ChannelBreakOutStrategy(Strategy):
    """H-005 ChannelBreakOutStrategy. Canonical built-in (Donchian):
        upBound   = highest(high, length)   (prior window, excl. current bar)
        downBound = lowest(low,  length)
        close breaks above upBound   -> long
        close breaks below downBound -> short
    Stop-and-reverse breakout. Long/short."""

    strategy_id = "H-005"
    declared_inputs = {"high", "low", "close"}
    name = "ChannelBreakOutStrategy"
    strategy_class = "breakout"
    direction = "long_short"
    default_params = {"length": 5}

    def positions(self, df: pd.DataFrame, params: Dict | None = None) -> pd.Series:
        p = {**self.default_params, **(params or {})}
        n = int(p["length"])
        # Prior-window channel: shift(1) so the current bar is excluded (no look-ahead).
        up = df["high"].rolling(n, min_periods=n).max().shift(1)
        dn = df["low"].rolling(n, min_periods=n).min().shift(1)
        c = df["close"]
        sig = pd.Series(np.nan, index=df.index)
        sig[c > up] = 1.0
        sig[c < dn] = -1.0
        return self._carry_forward(sig)

    def param_grid(self) -> List[Dict]:
        return self._grid(length=[5, 10, 20])
