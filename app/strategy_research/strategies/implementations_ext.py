"""The remaining 19 TradingView built-in strategies (H-003…H-024 minus the four
already implemented). Each is implemented from its canonical built-in logic and
plugs into the SAME `Strategy` interface — no change to the evaluation engine.

Every rule is causal (uses only closed/prior bars) and returns a carried-forward
target position in {-1,0,+1}; the backtester shifts it one bar, so look-ahead is
structurally impossible. The implementation IS the frozen, auditable spec.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from app.features.indicators import ema, rsi as wilder_rsi
from .base import Strategy


# --------------------------------------------------------------------------- #
# Extra indicators (pure pandas/numpy, causal)
# --------------------------------------------------------------------------- #
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rolling_std(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).std(ddof=0)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                      (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def stochastic(df: pd.DataFrame, k: int, d: int):
    ll = df["low"].rolling(k, min_periods=k).min()
    hh = df["high"].rolling(k, min_periods=k).max()
    pk = 100.0 * (df["close"] - ll) / (hh - ll).replace(0.0, np.nan)
    pk = pk.fillna(50.0)
    return pk, pk.rolling(d, min_periods=d).mean()


def adx(df: pd.DataFrame, n: int) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(df)
    atr_ = tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    plus_di = 100.0 * pd.Series(plus_dm, index=df.index).ewm(alpha=1.0/n, adjust=False, min_periods=n).mean() / atr_
    minus_di = 100.0 * pd.Series(minus_dm, index=df.index).ewm(alpha=1.0/n, adjust=False, min_periods=n).mean() / atr_
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return dx.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().fillna(0.0)


def parabolic_sar(df: pd.DataFrame, af0: float = 0.02, af_step: float = 0.02,
                  af_max: float = 0.2) -> pd.Series:
    high = df["high"].to_numpy(); low = df["low"].to_numpy(); n = len(df)
    sar = np.zeros(n); trend = np.ones(n)  # 1 up, -1 down
    if n == 0:
        return pd.Series(sar, index=df.index)
    sar[0] = low[0]; ep = high[0]; af = af0; up = True
    for i in range(1, n):
        prev = sar[i - 1]
        sar[i] = prev + af * (ep - prev)
        if up:
            sar[i] = min(sar[i], low[i - 1], low[max(i - 2, 0)])
            if high[i] > ep:
                ep = high[i]; af = min(af + af_step, af_max)
            if low[i] < sar[i]:
                up = False; sar[i] = ep; ep = low[i]; af = af0
        else:
            sar[i] = max(sar[i], high[i - 1], high[max(i - 2, 0)])
            if low[i] < ep:
                ep = low[i]; af = min(af + af_step, af_max)
            if high[i] > sar[i]:
                up = True; sar[i] = ep; ep = high[i]; af = af0
        trend[i] = 1 if up else -1
    return pd.Series(trend, index=df.index)  # +1 long-side, -1 short-side


def supertrend(df: pd.DataFrame, period: int, mult: float) -> pd.Series:
    hl2 = (df["high"] + df["low"]) / 2.0
    a = atr(df, period)
    upper = (hl2 + mult * a).to_numpy().copy()
    lower = (hl2 - mult * a).to_numpy().copy()
    close = df["close"].to_numpy(); n = len(df)
    dir_ = np.ones(n)
    for i in range(1, n):
        if close[i] > upper[i - 1]:
            dir_[i] = 1
        elif close[i] < lower[i - 1]:
            dir_[i] = -1
        else:
            dir_[i] = dir_[i - 1]
            if dir_[i] == 1:
                lower[i] = max(lower[i], lower[i - 1])
            else:
                upper[i] = min(upper[i], upper[i - 1])
    return pd.Series(dir_, index=df.index)


# --------------------------------------------------------------------------- #
# Strategy implementations
# --------------------------------------------------------------------------- #
class BollingerBandsStrategy(Strategy):
    """H-003 Bollinger Bands (mean-reversion): basis=SMA(20), bands ±mult·stdev.
    Long when close crosses **up through** the lower band (bounce); short when it
    crosses **down through** the upper band."""
    strategy_id="H-003"; name="Bollinger Bands Strategy"; strategy_class="mean-reversion-bands"
    direction="long_short"; default_params={"length":20,"mult":2.0}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}; c=df["close"]
        basis=sma(c,p["length"]); dev=p["mult"]*rolling_std(c,p["length"])
        upper=basis+dev; lower=basis-dev; pc=c.shift(1)
        sig=pd.Series(np.nan,index=df.index)
        sig[(c>lower)&(pc<=lower.shift(1))]=1.0
        sig[(c<upper)&(pc>=upper.shift(1))]=-1.0
        return self._carry_forward(sig)
    def param_grid(self): return self._grid(length=[20],mult=[2.0,2.5])


class BollingerBandsDirectedStrategy(Strategy):
    """H-004 Bollinger Bands directed (breakout variant): trade in the *breakout*
    direction — long when close breaks **above** the upper band, short when it
    breaks **below** the lower band."""
    strategy_id="H-004"; name="Bollinger Bands Strategy directed"; strategy_class="mean-reversion-bands"
    direction="long_short"; default_params={"length":20,"mult":2.0}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}; c=df["close"]
        basis=sma(c,p["length"]); dev=p["mult"]*rolling_std(c,p["length"])
        upper=basis+dev; lower=basis-dev; pc=c.shift(1)
        sig=pd.Series(np.nan,index=df.index)
        sig[(c>upper)&(pc<=upper.shift(1))]=1.0
        sig[(c<lower)&(pc>=lower.shift(1))]=-1.0
        return self._carry_forward(sig)
    def param_grid(self): return self._grid(length=[20],mult=[2.0,2.5])


class ConsecutiveUpDownStrategy(Strategy):
    """H-006 Consecutive Up/Down: long after N consecutive up closes, short after
    N consecutive down closes (momentum)."""
    strategy_id="H-006"; name="Consecutive Up/Down Strategy"; strategy_class="price-action-momentum"
    direction="long_short"; default_params={"n":3}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}; n=int(p["n"]); c=df["close"]
        up=c>c.shift(1); dn=c<c.shift(1)
        up_run=up.rolling(n,min_periods=n).sum()==n
        dn_run=dn.rolling(n,min_periods=n).sum()==n
        sig=pd.Series(np.nan,index=df.index); sig[up_run]=1.0; sig[dn_run]=-1.0
        return self._carry_forward(sig)
    def param_grid(self): return self._grid(n=[2,3])


class GreedyStrategy(Strategy):
    """H-007 Greedy: follow the bar body — long on an up-body bar (close>open),
    short on a down-body bar (close<open)."""
    strategy_id="H-007"; name="Greedy Strategy"; strategy_class="price-action-momentum"
    direction="long_short"; default_params={}
    def positions(self, df, params=None):
        sig=pd.Series(np.nan,index=df.index)
        sig[df["close"]>df["open"]]=1.0; sig[df["close"]<df["open"]]=-1.0
        return self._carry_forward(sig)


class InSideBarStrategy(Strategy):
    """H-008 Inside Bar: an inside bar (high<high[1] and low>low[1]) marks
    compression; trade its break — long when a later close exceeds the mother
    bar's high, short when it breaks the low."""
    strategy_id="H-008"; name="InSide Bar Strategy"; strategy_class="bar-pattern"
    direction="long_short"; default_params={}
    def positions(self, df, params=None):
        inside=(df["high"]<df["high"].shift(1))&(df["low"]>df["low"].shift(1))
        mh=df["high"].shift(1).where(inside).ffill()
        ml=df["low"].shift(1).where(inside).ffill()
        sig=pd.Series(np.nan,index=df.index)
        sig[df["close"]>mh]=1.0; sig[df["close"]<ml]=-1.0
        return self._carry_forward(sig)


class KeltnerChannelsStrategy(Strategy):
    """H-009 Keltner Channels (breakout): basis=EMA(20), band=mult·ATR(10).
    Long when close breaks above the upper channel, short below the lower."""
    strategy_id="H-009"; name="Keltner Channels Strategy"; strategy_class="mean-reversion-bands"
    direction="long_short"; default_params={"length":20,"atr_len":10,"mult":2.0}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}; c=df["close"]
        basis=ema(c,p["length"]); band=p["mult"]*atr(df,p["atr_len"])
        upper=basis+band; lower=basis-band; pc=c.shift(1)
        sig=pd.Series(np.nan,index=df.index)
        sig[(c>upper)&(pc<=upper.shift(1))]=1.0
        sig[(c<lower)&(pc>=lower.shift(1))]=-1.0
        return self._carry_forward(sig)
    def param_grid(self): return self._grid(length=[20],atr_len=[10],mult=[1.5,2.0])


class MomentumStrategy(Strategy):
    """H-011 Momentum: mom = close − close[len]. Long when mom crosses above 0,
    short when it crosses below."""
    strategy_id="H-011"; name="Momentum Strategy"; strategy_class="price-action-momentum"
    direction="long_short"; default_params={"length":12}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}
        mom=df["close"]-df["close"].shift(p["length"]); pm=mom.shift(1)
        sig=pd.Series(np.nan,index=df.index)
        sig[(mom>0)&(pm<=0)]=1.0; sig[(mom<0)&(pm>=0)]=-1.0
        return self._carry_forward(sig)
    def param_grid(self): return self._grid(length=[10,12,20])


class MovingAvg2LineCrossStrategy(Strategy):
    """H-012 MovingAvg 2-Line Cross: fast SMA vs slow SMA. Long on fast-over-slow,
    short on fast-under-slow (dual-MA crossover)."""
    strategy_id="H-012"; name="MovingAvg2Line Cross"; strategy_class="trend-crossover"
    direction="long_short"; default_params={"fast":9,"slow":18}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}; c=df["close"]
        f=sma(c,p["fast"]); s=sma(c,p["slow"]); d=f-s; pd_=d.shift(1)
        sig=pd.Series(np.nan,index=df.index)
        sig[(d>0)&(pd_<=0)]=1.0; sig[(d<0)&(pd_>=0)]=-1.0
        return self._carry_forward(sig)
    def param_grid(self): return self._grid(fast=[5,9],slow=[18,30])


class MovingAvgCrossStrategy(Strategy):
    """H-013 MovingAvg Cross: price vs a single SMA. Long when close crosses above
    the MA, short when below."""
    strategy_id="H-013"; name="MovingAvg Cross"; strategy_class="trend-crossover"
    direction="long_short"; default_params={"length":9}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}; c=df["close"]
        m=sma(c,p["length"]); d=c-m; pd_=d.shift(1)
        sig=pd.Series(np.nan,index=df.index)
        sig[(d>0)&(pd_<=0)]=1.0; sig[(d<0)&(pd_>=0)]=-1.0
        return self._carry_forward(sig)
    def param_grid(self): return self._grid(length=[9,20,50])


class OutSideBarStrategy(Strategy):
    """H-014 Outside Bar: an outside/engulfing bar (high>high[1] and low<low[1]);
    trade its direction — long if it closed up, short if it closed down."""
    strategy_id="H-014"; name="OutSide Bar Strategy"; strategy_class="bar-pattern"
    direction="long_short"; default_params={}
    def positions(self, df, params=None):
        outside=(df["high"]>df["high"].shift(1))&(df["low"]<df["low"].shift(1))
        up=df["close"]>df["close"].shift(1)
        sig=pd.Series(np.nan,index=df.index)
        sig[outside&up]=1.0; sig[outside&(~up)]=-1.0
        return self._carry_forward(sig)


class ParabolicSARStrategy(Strategy):
    """H-015 Parabolic SAR: long while price is above the SAR (up-trend), short
    while below (down-trend); flips on the SAR reversal."""
    strategy_id="H-015"; name="Parabolic SAR Strategy"; strategy_class="trend-following"
    direction="long_short"; default_params={"af0":0.02,"step":0.02,"max":0.2}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}
        return self._carry_forward(parabolic_sar(df,p["af0"],p["step"],p["max"]))


class PivotExtensionStrategy(Strategy):
    """H-016 Pivot Extension: break beyond the recent confirmed pivot high → long,
    below the recent pivot low → short (breakout past swing extremes)."""
    strategy_id="H-016"; name="Pivot Extension Strategy"; strategy_class="bar-pattern"
    direction="long_short"; default_params={"left":5,"right":5}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}; L=p["left"]; R=p["right"]; w=L+R+1
        # Confirmed pivot high/low, shifted by R bars so it is only known after confirmation.
        ph=df["high"].rolling(w,min_periods=w).max().shift(R)
        pl=df["low"].rolling(w,min_periods=w).min().shift(R)
        c=df["close"]
        sig=pd.Series(np.nan,index=df.index)
        sig[c>ph]=1.0; sig[c<pl]=-1.0
        return self._carry_forward(sig)
    def param_grid(self): return self._grid(left=[5],right=[5])


class PivotReversalStrategy(Strategy):
    """H-017 Pivot Reversal: a confirmed swing low → go long (reversal up); a
    confirmed swing high → go short. Confirmation lags by `right` bars (causal)."""
    strategy_id="H-017"; name="Pivot Reversal Strategy"; strategy_class="bar-pattern"
    direction="long_short"; default_params={"left":4,"right":2}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}; L=p["left"]; R=p["right"]; w=L+R+1
        centered_high=df["high"].rolling(w,min_periods=w).max()
        centered_low=df["low"].rolling(w,min_periods=w).min()
        is_ph=(df["high"].shift(R)==centered_high)
        is_pl=(df["low"].shift(R)==centered_low)
        sig=pd.Series(np.nan,index=df.index)
        sig[is_pl]=1.0; sig[is_ph]=-1.0
        return self._carry_forward(sig)
    def param_grid(self): return self._grid(left=[4],right=[2])


class PriceChannelStrategy(Strategy):
    """H-018 Price Channel (Donchian): long when close breaks above the prior
    N-bar high, short below the prior N-bar low."""
    strategy_id="H-018"; name="Price Channel Strategy"; strategy_class="breakout"
    direction="long_short"; default_params={"length":20}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}; n=int(p["length"])
        up=df["high"].rolling(n,min_periods=n).max().shift(1)
        dn=df["low"].rolling(n,min_periods=n).min().shift(1)
        c=df["close"]
        sig=pd.Series(np.nan,index=df.index); sig[c>up]=1.0; sig[c<dn]=-1.0
        return self._carry_forward(sig)
    def param_grid(self): return self._grid(length=[20,50])


class ADXBreakoutStrategy(Strategy):
    """H-019 Rob Booker ADX Breakout: only take breakouts when ADX confirms a
    trending market — long on an N-bar-high breakout with ADX>threshold, short on
    an N-bar-low breakout with ADX>threshold."""
    strategy_id="H-019"; name="Rob Booker - ADX Breakout"; strategy_class="breakout"
    direction="long_short"; default_params={"adx_len":14,"thr":20.0,"length":10}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}; n=int(p["length"])
        a=adx(df,p["adx_len"]); strong=a>p["thr"]
        up=df["high"].rolling(n,min_periods=n).max().shift(1)
        dn=df["low"].rolling(n,min_periods=n).min().shift(1)
        c=df["close"]
        sig=pd.Series(np.nan,index=df.index)
        sig[strong&(c>up)]=1.0; sig[strong&(c<dn)]=-1.0
        return self._carry_forward(sig)
    def param_grid(self): return self._grid(adx_len=[14],thr=[20.0,25.0],length=[10])


class StochasticSlowStrategy(Strategy):
    """H-021 Stochastic Slow: long when %K crosses above %D from oversold (<20),
    short when %K crosses below %D from overbought (>80)."""
    strategy_id="H-021"; name="Stochastic Slow Strategy"; strategy_class="mean-reversion-oscillator"
    direction="long_short"; default_params={"k":14,"d":3,"os":20.0,"ob":80.0}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}
        pk,pd_=stochastic(df,p["k"],p["d"]); prev=pk-pd_; pprev=prev.shift(1)
        sig=pd.Series(np.nan,index=df.index)
        sig[(prev>0)&(pprev<=0)&(pd_<p["os"]+20)]=1.0
        sig[(prev<0)&(pprev>=0)&(pd_>p["ob"]-20)]=-1.0
        return self._carry_forward(sig)
    def param_grid(self): return self._grid(k=[14],d=[3],os=[20.0],ob=[80.0])


class SupertrendStrategy(Strategy):
    """H-022 Supertrend: ATR-band trend filter. Long while price is on the bullish
    side of the Supertrend line, short on the bearish side; flips on crossover."""
    strategy_id="H-022"; name="Supertrend Strategy"; strategy_class="trend-following"
    direction="long_short"; default_params={"period":10,"mult":3.0}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}
        return self._carry_forward(supertrend(df,int(p["period"]),p["mult"]))
    def param_grid(self): return self._grid(period=[10],mult=[2.0,3.0])


class TechnicalRatingsStrategy(Strategy):
    """H-023 Technical Ratings: a composite 'rating' from several indicators
    (price vs EMA20/EMA50, RSI vs 50, MACD histogram sign, 12-bar momentum sign).
    Long when the net rating is bullish, short when bearish."""
    strategy_id="H-023"; name="Technical Ratings Strategy"; strategy_class="composite"
    direction="long_short"; default_params={}
    def positions(self, df, params=None):
        c=df["close"]
        votes=(np.sign(c-ema(c,20))+np.sign(c-ema(c,50))
               +np.sign(wilder_rsi(c,14)-50.0)
               +np.sign((ema(c,12)-ema(c,26))-sma(ema(c,12)-ema(c,26),9))
               +np.sign(c-c.shift(12)))
        sig=pd.Series(np.nan,index=df.index)
        sig[votes>0]=1.0; sig[votes<0]=-1.0
        return self._carry_forward(sig)


class VoltyExpanCloseStrategy(Strategy):
    """H-024 Volty Expan Close: volatility-expansion breakout — long when close
    exceeds the prior close by more than mult·ATR, short when it falls by more
    than mult·ATR (a classic 'volatility expansion' entry)."""
    strategy_id="H-024"; name="Volty Expan Close Strategy"; strategy_class="breakout"
    direction="long_short"; default_params={"atr_len":10,"mult":0.75}
    def positions(self, df, params=None):
        p={**self.default_params,**(params or {})}
        a=atr(df,p["atr_len"]); pc=df["close"].shift(1); c=df["close"]
        sig=pd.Series(np.nan,index=df.index)
        sig[c>pc+p["mult"]*a]=1.0; sig[c<pc-p["mult"]*a]=-1.0
        return self._carry_forward(sig)
    def param_grid(self): return self._grid(atr_len=[10],mult=[0.5,0.75,1.0])


class AssetClassTrendFollowingStrategy(Strategy):
    """H-025 Asset Class Trend Following — single-asset core of the QuantConnect /
    Meb Faber tactical-allocation rule (documented rules re-implemented, not copied).

    Documented rule: hold the asset while close > its 10-month SMA, otherwise move to
    cash; equal weight across the ETFs that pass. Single-asset translation used here:
    **long-only** timing — position = +1 when close > SMA(length), else 0 (cash).
    length=200 daily bars ≈ a 10-month SMA. NOT represented (out of engine scope):
    the equal-weight allocation across 5 asset-class ETFs, and monthly rebalancing
    (this checks the rule daily, so turnover is somewhat higher than the original)."""

    strategy_id = "H-025"
    name = "Asset Class Trend Following"
    strategy_class = "trend-following"
    direction = "long_only"
    default_params = {"length": 200}

    def positions(self, df: pd.DataFrame, params: Dict | None = None) -> pd.Series:
        p = {**self.default_params, **(params or {})}
        m = sma(df["close"], int(p["length"]))
        # +1 while above the SMA, 0 (cash) otherwise; warm-up (SMA=NaN) => 0. Every
        # bar is a full state, so no carry-forward is needed. Backtester shifts the
        # position forward one bar => no look-ahead.
        return (df["close"] > m).astype(float)

    def param_grid(self) -> List[Dict]:
        # Faber's canonical is a fixed 10-month (~200d) SMA; we also probe stability
        # to the lookback choice. Data-snooping budget = 3, priced into deflated Sharpe.
        return self._grid(length=[150, 200, 250])


class AssetClassTrendFollowingMonthlyStrategy(Strategy):
    """H-026 Asset Class Trend Following (Monthly) — a faithful single-asset
    translation of the QuantConnect / Meb Faber rule: a genuine **10-month** SMA of
    **monthly** closes, evaluated **only at month boundaries**. Long while the prior
    month-end close is above the 10-month SMA, else cash; the position is held for the
    whole month. Re-implemented from the documented rules (code not copied).

    Look-ahead-free by construction: the decision at each new month uses only closed
    monthly data from *completed* months (via `shift(1)`, never `shift(-1)`), and the
    backtester shifts positions forward one bar. Still single-asset (no equal-weight
    allocation across the 5 ETFs — out of engine scope)."""

    strategy_id = "H-026"
    name = "Asset Class Trend Following (Monthly)"
    strategy_class = "trend-following"
    direction = "long_only"
    default_params = {"months": 10}

    def positions(self, df: pd.DataFrame, params: Dict | None = None) -> pd.Series:
        p = {**self.default_params, **(params or {})}
        m = int(p["months"])
        t = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        ym = (t.dt.year * 12 + t.dt.month)
        # First bar of each new calendar month (causal: compares to the PRIOR bar).
        is_month_start = ym != ym.shift(1)
        # The prior month's last close, known at the month-start bar (shift(1) = past only).
        prev_close = df["close"].shift(1)
        mc = prev_close[is_month_start]                 # monthly-close series (completed months)
        sma_m = mc.rolling(m, min_periods=m).mean()     # true 10-month SMA of monthly closes
        sig = (mc > sma_m).astype(float)                # 1 long / 0 cash; warm-up (SMA=NaN) -> 0
        full = pd.Series(np.nan, index=df.index)
        full[is_month_start] = sig
        # Hold the month-start decision through the month; leading pre-signal bars -> cash.
        return full.ffill().fillna(0.0)

    def param_grid(self) -> List[Dict]:
        return [dict(self.default_params)]              # fixed 10-month; data-snoop budget = 1


class ConnorsRSI2Strategy(Strategy):
    """H-027 Connors RSI-2 — Larry Connors' 2-period-RSI mean-reversion rule with a
    200-period SMA regime filter. Rules re-implemented from the publicly documented
    method (StockCharts ChartSchool; corroborated by open-source TradingView/GitHub
    implementations); NO third-party code copied.

    Documented rules (long/short):
      * Long : close > SMA(200) AND RSI(2) < 5  -> enter long; exit to CASH when close > SMA(5)
      * Short: close < SMA(200) AND RSI(2) > 95 -> enter short; exit to CASH when close < SMA(5)
    The 200-SMA is a TREND FILTER (regime gate); RSI(2) is the ENTRY oscillator; the
    5-SMA cross is the fast mean-reversion EXIT. Connors uses no protective stop.

    Look-ahead-free by construction: RSI(2), SMA(200) and SMA(5) use only closed bars,
    and the position is built by a purely causal state machine (each bar's state depends
    only on data up to that bar's close and the prior state). The backtester shifts
    positions forward one bar, so the traded bar can never be peeked at."""

    strategy_id = "H-027"
    name = "Connors RSI-2 Strategy"
    strategy_class = "mean-reversion-oscillator"
    direction = "long_short"
    default_params = {"rsi_len": 2, "trend_len": 200, "exit_len": 5,
                      "buy_below": 5.0, "sell_above": 95.0}

    def positions(self, df: pd.DataFrame, params: Dict | None = None) -> pd.Series:
        p = {**self.default_params, **(params or {})}
        c = df["close"]
        trend = sma(c, int(p["trend_len"]))          # 200-SMA regime filter
        exit_ma = sma(c, int(p["exit_len"]))         # 5-SMA fast exit
        r = wilder_rsi(c, int(p["rsi_len"]))         # 2-period RSI entry oscillator
        long_entry = ((c > trend) & (r < p["buy_below"])).to_numpy()
        short_entry = ((c < trend) & (r > p["sell_above"])).to_numpy()
        long_exit = (c > exit_ma).to_numpy()
        short_exit = (c < exit_ma).to_numpy()
        ready = (trend.notna() & exit_ma.notna()).to_numpy()   # warm-up guard -> flat
        n = len(df)
        out = np.zeros(n, dtype=float)
        state = 0.0
        for i in range(n):
            if not ready[i]:
                state = 0.0; out[i] = 0.0; continue
            if state == 0.0:
                if long_entry[i]:
                    state = 1.0
                elif short_entry[i]:
                    state = -1.0
            elif state == 1.0:
                if long_exit[i]:
                    state = 0.0
            else:  # state == -1.0
                if short_exit[i]:
                    state = 0.0
            out[i] = state
        return pd.Series(out, index=df.index)

    def param_grid(self) -> List[Dict]:
        # Connors tested both the 5/95 and 10/90 extremes; probe that small neighbourhood.
        # Grid size (4) is the data-snooping budget priced into the deflated Sharpe.
        return self._grid(rsi_len=[2], trend_len=[200], exit_len=[5],
                          buy_below=[5.0, 10.0], sell_above=[95.0, 90.0])


def wma(s: pd.Series, n: int) -> pd.Series:
    """Linearly-weighted moving average (weights 1..n, most recent heaviest). Trailing/causal."""
    x = s.to_numpy(dtype=float); w = np.arange(1, n + 1, dtype=float); w = w / w.sum()
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = np.convolve(x, w[::-1], mode="valid")
    return pd.Series(out, index=s.index)


def hma(s: pd.Series, n: int) -> pd.Series:
    """Hull Moving Average (Alan Hull, 2005) - FROZEN formula:
    half=floor(n/2), sqrt_p=round(sqrt(n)); HMA = WMA(2*WMA(close,half) - WMA(close,n), sqrt_p).
    Fully causal (WMA is trailing); NaN through warm-up."""
    half = int(np.floor(n / 2)); sqrt_p = int(round(np.sqrt(n)))
    raw = 2.0 * wma(s, half) - wma(s, n)
    return wma(raw, sqrt_p)


class HullMASlopeTrendStrategy(Strategy):
    """H-028 Hull MA Slope Trend - GRAVEYARD (documented, tested & REJECTED hypothesis; NOT an
    active strategy, not for paper trading or live execution). Trades the direction of the Hull
    MA slope. Public indicator (Alan Hull, 2005); surfaced via a free MQL5 market indicator
    (compiled, no source, no stated license) - rules re-implemented from the public formula,
    NO MT5 code used or decompiled.

    Pre-registered primary: length n=55, deadband=0, long/short stop-and-reverse. Long +1 when
    the HMA is rising (HMA_t > HMA_{t-1}), short -1 when falling. Decision on the completed
    candle; the backtester shifts the position forward one bar -> causal, no look-ahead, no
    repaint. Verdict Graveyard: OOS max drawdown 64% (> 60% gate), beats neither buy-and-hold nor
    matched random-timing, and at fixed n=55 the Hull construction is WEAKER than plain SMA/EMA/WMA
    slope - reduced MA lag increased whipsaw and cost instead of capturing trend (see the H-028
    dossier + stress-test report). Contributes to M-001."""

    strategy_id = "H-028"
    name = "Hull MA Slope Trend"
    strategy_class = "ma-slope-trend"
    direction = "long_short"
    default_params = {"length": 55}

    def positions(self, df: pd.DataFrame, params: Dict | None = None) -> pd.Series:
        p = {**self.default_params, **(params or {})}
        n = int(p["length"])
        hv = hma(df["close"], n).to_numpy()
        N = len(hv)
        slope = np.full(N, np.nan)
        slope[1:] = hv[1:] - hv[:-1]
        warm = n + int(round(np.sqrt(n)))            # no signal until the HMA is ready
        out = np.zeros(N); cur = 0.0                 # explicit causal carry (version-independent)
        for i in range(N):
            if i < warm or np.isnan(hv[i]) or np.isnan(slope[i]):
                out[i] = 0.0; cur = 0.0; continue
            if slope[i] > 0.0:                        # deadband = 0: sign of the HMA slope
                cur = 1.0
            elif slope[i] < 0.0:
                cur = -1.0                            # flat slope -> hold previous
            out[i] = cur
        return pd.Series(out, index=df.index)

    def param_grid(self) -> List[Dict]:
        # FROZEN single config: n=55 in every walk-forward fold (no per-fold re-selection).
        # {20,34,55,89,100} periods and {0.05%,0.10%} deadbands are SENSITIVITY ONLY.
        return [dict(self.default_params)]


EXT_STRATEGIES: List[type] = [
    BollingerBandsStrategy, BollingerBandsDirectedStrategy, ConsecutiveUpDownStrategy,
    GreedyStrategy, InSideBarStrategy, KeltnerChannelsStrategy, MomentumStrategy,
    MovingAvg2LineCrossStrategy, MovingAvgCrossStrategy, OutSideBarStrategy,
    ParabolicSARStrategy, PivotExtensionStrategy, PivotReversalStrategy,
    PriceChannelStrategy, ADXBreakoutStrategy, StochasticSlowStrategy,
    SupertrendStrategy, TechnicalRatingsStrategy, VoltyExpanCloseStrategy,
    AssetClassTrendFollowingStrategy, AssetClassTrendFollowingMonthlyStrategy,
    ConnorsRSI2Strategy,
    HullMASlopeTrendStrategy,
]
