"""Building blocks — reusable indicator signals the Discovery Engine composes.

Each block maps an OHLCV frame to a RAW directional signal series in {-1,0,+1}
(0 = neutral), using only closed/prior bars (causal). Composition (genome.py)
carries the combined signal forward; the backtester shifts it, so a generated
strategy can never look ahead.

Each block declares traits (family, turnover, regime affinity) — the vocabulary
the trait-success memory learns over.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict

import numpy as np
import pandas as pd

from app.features.indicators import ema, rsi as wilder_rsi
from app.strategy_research.strategies.implementations_ext import (
    adx, atr, sma, stochastic, supertrend,
)


@dataclass(frozen=True)
class Block:
    name: str
    family: str          # trend | mean-reversion | breakout | volatility | regime-gate
    turnover: str        # low | med | high
    regime_affinity: str # trend | range | crisis | none
    fn: Callable         # (df, params) -> pd.Series in {-1,0,1}
    default_params: Dict = field(default_factory=dict)
    is_gate: bool = False  # gates (ADX) permit/deny rather than pick a direction

    def signal(self, df: pd.DataFrame, params: Dict | None = None) -> pd.Series:
        p = {**self.default_params, **(params or {})}
        return self.fn(df, p).reindex(df.index).fillna(0.0)


def _dir(cond_long, cond_short, index):
    s = pd.Series(0.0, index=index)
    s[cond_long] = 1.0
    s[cond_short] = -1.0
    return s


# --- directional blocks ----------------------------------------------------- #
def _ma_cross(df, p):
    f, s = sma(df["close"], p["fast"]), sma(df["close"], p["slow"])
    return _dir(f > s, f < s, df.index)


def _price_ma(df, p):
    m = sma(df["close"], p["length"])
    return _dir(df["close"] > m, df["close"] < m, df.index)


def _macd(df, p):
    macd = ema(df["close"], p["fast"]) - ema(df["close"], p["slow"])
    sig = sma(macd, p["signal"])
    return _dir(macd > sig, macd < sig, df.index)


def _rsi_rev(df, p):
    r = wilder_rsi(df["close"], p["length"])
    return _dir(r < p["os"], r > p["ob"], df.index)


def _bb_rev(df, p):
    c = df["close"]; basis = sma(c, p["length"])
    dev = p["mult"] * c.rolling(p["length"], min_periods=p["length"]).std(ddof=0)
    return _dir(c < basis - dev, c > basis + dev, df.index)


def _donchian(df, p):
    up = df["high"].rolling(p["length"], min_periods=p["length"]).max().shift(1)
    dn = df["low"].rolling(p["length"], min_periods=p["length"]).min().shift(1)
    return _dir(df["close"] > up, df["close"] < dn, df.index)


def _momentum(df, p):
    mom = df["close"] - df["close"].shift(p["length"])
    return _dir(mom > 0, mom < 0, df.index)


def _keltner(df, p):
    basis = ema(df["close"], p["length"]); band = p["mult"] * atr(df, p["atr_len"])
    return _dir(df["close"] > basis + band, df["close"] < basis - band, df.index)


def _atr_expand(df, p):
    a = atr(df, p["atr_len"]); pc = df["close"].shift(1)
    return _dir(df["close"] > pc + p["mult"] * a, df["close"] < pc - p["mult"] * a, df.index)


def _stoch_rev(df, p):
    k, _ = stochastic(df, p["k"], p["d"])
    return _dir(k < p["os"], k > p["ob"], df.index)


def _supertrend(df, p):
    return supertrend(df, p["period"], p["mult"])


def _adx_gate(df, p):
    a = adx(df, p["length"])
    return pd.Series(np.where(a > p["thr"], 1.0, 0.0), index=df.index)  # 1=permit, 0=deny


BLOCKS: Dict[str, Block] = {b.name: b for b in [
    Block("ma_cross", "trend", "med", "trend", _ma_cross, {"fast": 9, "slow": 21}),
    Block("price_ma", "trend", "med", "trend", _price_ma, {"length": 20}),
    Block("macd", "trend", "med", "trend", _macd, {"fast": 12, "slow": 26, "signal": 9}),
    Block("rsi_rev", "mean-reversion", "med", "range", _rsi_rev, {"length": 14, "os": 30, "ob": 70}),
    Block("bb_rev", "mean-reversion", "med", "range", _bb_rev, {"length": 20, "mult": 2.0}),
    Block("donchian", "breakout", "low", "trend", _donchian, {"length": 20}),
    Block("momentum", "trend", "med", "trend", _momentum, {"length": 12}),
    Block("keltner", "breakout", "med", "trend", _keltner, {"length": 20, "atr_len": 10, "mult": 2.0}),
    Block("atr_expand", "volatility", "high", "crisis", _atr_expand, {"atr_len": 10, "mult": 0.75}),
    Block("stoch_rev", "mean-reversion", "high", "range", _stoch_rev, {"k": 14, "d": 3, "os": 20, "ob": 80}),
    Block("supertrend", "trend", "low", "trend", _supertrend, {"period": 10, "mult": 3.0}),
    Block("adx_gate", "regime-gate", "low", "trend", _adx_gate, {"length": 14, "thr": 20.0}, True),
]}

DIRECTIONAL = [n for n, b in BLOCKS.items() if not b.is_gate]
GATES = [n for n, b in BLOCKS.items() if b.is_gate]
