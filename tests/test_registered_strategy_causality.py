"""Finite synthetic causal-input checks; never a forward-test/evidence workflow.

Registry-derived built-in coverage, all defaults/grids, two OHLCV fixtures.
H-025 is only a source-level unit under test here: no manifest, journal, registry,
market dataset, evaluation verdict or prospective artifact is read/written.
"""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from app.strategy_research.strategies import available_strategies, get_strategy, register_strategy
from app.strategy_research.strategies import registry
from app.strategy_research.strategies.base import Strategy
from app.strategy_research.evaluation.backtest import run_backtest

# Derived from the actual registry at collection, not a hand-maintained ID list.
REGISTERED_AT_COLLECTION = tuple(available_strategies())


def fixture(seed, n=850):
    rng=np.random.Generator(np.random.PCG64(seed))
    t=np.arange(n)
    close=100*np.exp(np.cumsum(.001*np.sin(t/19)+rng.normal(0,.012,n)))
    opening=close*np.exp(rng.normal(0,.004,n))
    width=rng.uniform(.001,.025,n)
    return pd.DataFrame({'open_time':1577836800000+t*86400000,
                         'open':opening,'high':np.maximum(opening,close)*(1+width),
                         'low':np.minimum(opening,close)*(1-width),'close':close,
                         'volume':rng.uniform(100,10000,n)})


def cuts(df):
    dates=pd.to_datetime(df['open_time'],unit='ms')
    months=dates.dt.month.to_numpy()
    boundaries=np.flatnonzero(months[1:]!=months[:-1])+1
    chosen=[boundaries[9],boundaries[24]]
    return sorted({1,2,5,10,21,199,200,252,399,400,600,len(df)-1,
                   *(int(k)+delta for k in chosen for delta in (-1,0,1))})


def perturb_future(df, cut):
    changed=df.copy(deep=True)
    k=np.arange(len(df)-cut)
    # Valid but very different future OHLCV; timestamps and prefix unchanged.
    close=25+3*np.sin(k/2)+k*.02
    opening=close+np.cos(k/3)
    changed.loc[changed.index[cut:],'close']=close
    changed.loc[changed.index[cut:],'open']=opening
    changed.loc[changed.index[cut:],'high']=np.maximum(close,opening)+2
    changed.loc[changed.index[cut:],'low']=np.minimum(close,opening)-2
    changed.loc[changed.index[cut:],'volume']=90000+k*17
    return changed


def assert_same_prefix(full, other, cut, context):
    a=np.asarray(full)[:cut];b=np.asarray(other)[:cut]
    assert len(a)==len(b)==cut, context
    assert not np.isinf(a).any() and not np.isinf(b).any(), context
    np.testing.assert_array_equal(np.isnan(a),np.isnan(b),err_msg=context+' missing mask')
    finite=np.isfinite(a)
    np.testing.assert_array_equal(a[finite],b[finite],err_msg=context)


def check_strategy(strategy, df, selected_cuts):
    configs=[None]+list(deepcopy(strategy).param_grid())
    for params in configs:
        # Fresh isolated object and copied input for each call (no cache/state sharing).
        def positions(data):
            signal=deepcopy(strategy).positions(data.copy(deep=True),deepcopy(params))
            pd.testing.assert_index_equal(signal.index,data.index)
            assert len(signal)==len(data)
            values=signal.to_numpy(dtype=float)
            assert set(values[np.isfinite(values)]) <= {-1.,0.,1.}
            return values
        full=positions(df)
        for cut in selected_cuts:
            context=f'{strategy.strategy_id} params={params} cut={cut}'
            assert_same_prefix(full,positions(df.iloc[:cut]),cut,context+' truncation')
            assert_same_prefix(full,positions(perturb_future(df,cut)),cut,context+' future mutation')


@pytest.mark.parametrize('sid',REGISTERED_AT_COLLECTION)
@pytest.mark.parametrize('seed',[7,29])
def test_registered_strategy_defaults_and_every_grid_are_causal(sid,seed):
    df=fixture(seed)
    check_strategy(get_strategy(sid),df,cuts(df))


def test_registry_coverage_is_not_a_fixed_shortlist():
    # Any permanent registry addition is automatically parametrized above.
    builtins={sid for sid in available_strategies() if sid.startswith('H-')}
    assert builtins <= set(REGISTERED_AT_COLLECTION)
    assert 'H-025' in REGISTERED_AT_COLLECTION
    assert 'H-029' in REGISTERED_AT_COLLECTION


def test_new_runtime_registration_can_use_the_same_checks(monkeypatch):
    from app.discovery.genome import Recipe, materialize
    monkeypatch.setattr(registry,'_REGISTRY',dict(registry._REGISTRY))
    sid='TEST-DYNAMIC-CAUSAL'
    register_strategy(materialize(Recipe('VOTE',('ma_cross','rsi_rev')),sid))
    assert sid in available_strategies()
    df=fixture(7)
    check_strategy(get_strategy(sid),df,[200,400,600])


class FutureReader(Strategy):
    strategy_id='TEST-FUTURE-READER'
    name='Intentional negative control'
    def positions(self,df,params=None):
        return np.sign(df['close'].shift(-1)-df['close']).fillna(0.)


def test_causality_harness_detects_future_reading_signal():
    with pytest.raises(AssertionError,match='TEST-FUTURE-READER'):
        check_strategy(FutureReader(),fixture(7),[200,400])


def test_future_perturbation_detects_leak_without_truncation():
    df=fixture(7)
    original=FutureReader().positions(df)
    mutated=perturb_future(df,200)
    # Force an opposite next-bar move: merely changing a future price can leave
    # its direction unchanged, so that alone is not a valid negative control.
    previous=df.loc[199,'close']
    price=previous*(.5 if df.loc[200,'close']>previous else 2.)
    mutated.loc[200,['open','close']]=price
    mutated.loc[200,'high']=price*1.01
    mutated.loc[200,'low']=price*.99
    changed=FutureReader().positions(mutated)
    with pytest.raises(AssertionError):
        assert_same_prefix(original,changed,200,'future mutation negative control')


def test_one_bar_execution_lag_does_not_validate_signals():
    df=fixture(7)
    result=run_backtest(df,FutureReader().positions(df),'1d',0.,0.)
    # After lag, the future reader holds exactly the sign of each priced return.
    assert (result.net_returns[1:]>0).all()
    assert (result.net_returns[1:]<0).sum()==0
    assert len(result.net_returns[1:])==849
