"""Dedicated pre-result execution review checks, separate from registered source."""
import numpy as np
import pandas as pd
import pytest
from app.strategy_research.chart_patterns_v1 import signals,simulate,random_schedule,STEP,NAMES

def path(nodes):
    x,y=zip(*nodes);c=np.interp(np.arange(x[-1]+1),x,y)
    return pd.DataFrame({'open':c,'high':c+.1,'low':c-.1,'close':c,'open_time':np.arange(len(c))*STEP})

@pytest.mark.parametrize('name',NAMES)
def test_complete_detector_positive_paths(name):
    if name=='symmetric_triangle':
        nodes=[(0,100),(20,100),(30,120),(36,80),(42,116),(48,84),(54,112),(60,88),(65,106),(66,111),(70,112)]
        expected=66
    elif 'shoulders' in name:
        nodes=[(0,110),(10,110),(30,90),(40,100),(50,85),(60,100),(70,90),(75,99),(76,101),(80,102)]
        expected=76
    else:
        nodes=[(0,110),(10,110),(30,90),(40,100),(50,90),(55,99),(56,101),(60,102)]
        expected=56
    d=path(nodes)
    if name in ('double_top','head_shoulders'):
        old=d.copy();d['open']=200-old.open;d['close']=200-old.close
        d['high']=200-old.low;d['low']=200-old.high
    ss=signals(d,name)
    assert expected in ss,(name,ss)
    assert ss[expected].side==(-1 if name in ('double_top','head_shoulders') else 1)
    assert not signals(d.iloc[:expected],name)
    assert signals(d.iloc[:expected+1],name)[expected]==ss[expected]

def test_synthetic_account_reconciliation_and_timing_controls():
    c=100+np.sin(np.arange(200)*.08)*5
    d=pd.DataFrame({'open_time':np.arange(200)*STEP,'open':c,'close':c+1,'high':c+2,'low':c-2})
    f=pd.DataFrame({'fundingTime':np.arange(0,200,2)*STEP,'fundingRate':.0001,'markPrice':c[::2]})
    schedule={10:(1,20),40:(-1,15),75:(1,25)}
    m,tr,eq=simulate(d,f,{},schedule=schedule)
    assert len(tr)==3
    assert eq[-1]==pytest.approx(10000+sum(x['price_pnl']-x['fees']+x['funding'] for x in tr))
    for seed in range(10):
        r=random_schedule(tr,0,len(d),seed)
        mc,tc,ec=simulate(d,f,{},schedule=r)
        assert len(tc)==3
        assert sorted((t['side'],t['duration']) for t in tc)==sorted((t['side'],t['duration']) for t in tr)
        assert np.isfinite(ec).all()

def test_capital_exhaustion_preserves_liability_and_has_no_sharpe():
    d=pd.DataFrame({'open_time':np.arange(4)*STEP,'open':[100.,100.,30.,30.],
                    'high':[101.,101.,31.,31.],'low':[99.,99.,29.,29.],
                    'close':[100.,100.,30.,30.]})
    f=pd.DataFrame({'fundingTime':[STEP],'fundingRate':[.6],'markPrice':[100.]})
    m,tr,eq=simulate(d,f,{},fee_bps=0,slip_bps=0,schedule={0:(1,4)})
    assert m['bankrupt'] and m['sharpe'] is None
    assert eq[-1]==-3000 and m['total_return']==pytest.approx(-1.3)
    assert eq[-1]==pytest.approx(10000+sum(t['net_pnl'] for t in tr))
    assert tr[0]['exit_reason']=='bankrupt' and tr[0]['exit_i']==2
