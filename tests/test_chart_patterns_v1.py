import numpy as np
import pandas as pd
import pytest
from app.strategy_research.chart_patterns_v1 import (
    Pivot, Signal, geometry, signals, simulate, metrics, holm, NAMES, STEP,
)

def frame(rows):
    d=pd.DataFrame(rows,columns=['open','high','low','close'])
    d['open_time']=np.arange(len(d))*STEP;return d

def funding(events=()):
    return pd.DataFrame(events,columns=['fundingTime','fundingRate','markPrice'])

def test_geometry_all_five_and_no_break():
    history=np.full(100,110.);history[30]=100.
    db=[Pivot(30,'L',90),Pivot(40,'H',100),Pivot(50,'L',90.2)]
    ih=[Pivot(30,'L',90),Pivot(40,'H',100),Pivot(50,'L',85),Pivot(60,'H',100),Pivot(70,'L',90.2)]
    for name,p,t in [('double_bottom',db,55),('inverse_head_shoulders',ih,75)]:
        s=geometry(name,p,t,101,99,2,history);assert s and s.side==1
        assert geometry(name,p,t,100,99,2,history) is None
        mirrored=[Pivot(x.i,'H' if x.kind=='L' else 'L',200-x.price) for x in p]
        bear='double_top' if name=='double_bottom' else 'head_shoulders'
        s=geometry(bear,mirrored,t,99,101,2,200-history);assert s and s.side==-1
        assert geometry(bear,mirrored,t,100,101,2,200-history) is None
    tri=[Pivot(30,'H',120),Pivot(36,'L',80),Pivot(42,'H',116),Pivot(48,'L',84),Pivot(54,'H',112),Pivot(60,'L',88)]
    assert geometry('symmetric_triangle',tri,65,110,108,2,history).side==1
    assert geometry('symmetric_triangle',tri,65,88,90,2,history).side==-1
    assert geometry('symmetric_triangle',tri,65,100,100,2,history) is None

def test_geometry_rejects_bad_shoulders_trend_height_and_expiry():
    history=np.full(120,110.);history[30]=100.
    p=[Pivot(30,'L',90),Pivot(40,'H',100),Pivot(50,'L',93)]
    assert geometry('double_bottom',p,55,101,99,2,history) is None
    p[-1]=Pivot(50,'L',90)
    assert geometry('double_bottom',p,81,101,99,2,history) is None
    assert geometry('double_bottom',p,55,101,99,2,np.full(120,100.)) is None
    assert geometry('double_bottom',p,55,101,99,20,history) is None

@pytest.mark.parametrize('name',NAMES)
def test_no_future_signals(name):
    rng=np.random.default_rng(42);c=100*np.exp(np.cumsum(rng.normal(0,.03,600)))
    d=pd.DataFrame({'open':c,'close':c,'high':c*1.01,'low':c*.99,'open_time':np.arange(len(c))*STEP})
    full=signals(d,name)
    for n in (51,120,303,500):
        assert signals(d.iloc[:n],name)=={k:v for k,v in full.items() if k<n}
        changed=d.copy();changed.loc[n:,['open','close','high','low']]*=10
        assert {k:v for k,v in signals(changed,name).items() if k<n}=={k:v for k,v in full.items() if k<n}
    for s in full.values(): assert max(s.key)+5<=s.i

def test_next_open_not_signal_close_and_final_fees():
    d=frame([(100,101,99,100),(110,111,109,110),(120,121,119,120)])
    m,tr,eq=simulate(d,funding(),{0:Signal(0,1,80,())},fee_bps=10,slip_bps=0)
    assert tr[0]['entry_i']==1 and tr[0]['entry_price']==110
    q=10000/110
    assert eq[-1]==pytest.approx(10000+q*10-10-q*120*.001)
    assert tr[0]['exit_reason']=='final_close'

@pytest.mark.parametrize('side',[1,-1])
@pytest.mark.parametrize('rate',[.01,-.01])
def test_funding_sign_and_no_charge_at_new_entry(side,rate):
    d=frame([(100,101,99,100)]*4)
    f=funding([(STEP,rate,100),(2*STEP,rate,100)])
    stop=80 if side==1 else 120
    m,tr,eq=simulate(d,f,{0:Signal(0,side,stop,())},fee_bps=0,slip_bps=0)
    assert m['funding']==pytest.approx(-side*10000*rate)
    assert eq[-1]==pytest.approx(10000-side*10000*rate)

def test_funding_before_gap_exit():
    d=frame([(100,101,99,100),(100,101,99,100),(80,81,79,80)])
    m,tr,_=simulate(d,funding([(2*STEP,.01,80)]),{0:Signal(0,1,90,())},fee_bps=0,slip_bps=0)
    assert tr[0]['exit_reason']=='gap_stop' and tr[0]['exit_price']==80
    assert m['funding']==-80

@pytest.mark.parametrize('side',[1,-1])
def test_stop_wins_when_both_touched(side):
    d=frame([(100,101,99,100),(100,125,75,100),(100,101,99,100)])
    stop=90 if side==1 else 110
    _,tr,eq=simulate(d,funding(),{0:Signal(0,side,stop,())},fee_bps=0,slip_bps=0)
    assert tr[0]['exit_reason']=='stop';assert eq[-1]==9000

def test_cancel_gap_beyond_stop():
    d=frame([(100,101,99,100),(80,81,79,80)])
    m,tr,_=simulate(d,funding(),{0:Signal(0,1,90,())})
    assert not tr and m['total_return']==0

def test_short_price_pnl_and_adverse_slippage():
    d=frame([(100,101,99,100),(100,101,99,100),(95,96,94,95)])
    m,tr,eq=simulate(d,funding(),{0:Signal(0,-1,120,())},fee_bps=10,slip_bps=5)
    t=tr[0]; assert t['entry_price']<100 and t['exit_price']>95
    assert eq[-1]==pytest.approx(10000+t['quantity']*(t['exit_price']-t['entry_price'])-t['fees'])

def test_time_exit_and_initial_drawdown():
    d=frame([(100,101,99,100)]*46)
    _,tr,_=simulate(d,funding(),{0:Signal(0,1,80,())},fee_bps=0,slip_bps=0)
    assert tr[0]['exit_i']==43 and tr[0]['exit_reason']=='time'
    assert metrics(np.array([9000.,9500.]),np.array([0,STEP]))['max_drawdown']==pytest.approx(.1)

def test_holm_family():
    assert holm([.01,.02,.03,.2,.9])==pytest.approx([.05,.08,.09,.4,.9])
