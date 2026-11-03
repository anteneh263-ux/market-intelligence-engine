"""Experimental next-open/funding evaluator. Contract: docs/BTC_CHART_PATTERNS_V1_PLAN.md.

No changes to existing frozen matrices, registries, or evidence stores.
"""
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd

VERSION = 'mie-chart-patterns-v1.1'
NAMES = ('double_bottom', 'double_top', 'inverse_head_shoulders',
         'head_shoulders', 'symmetric_triangle')
STEP = 4*3600*1000

@dataclass(frozen=True)
class Pivot:
    i: int
    kind: str
    price: float

@dataclass(frozen=True)
class Signal:
    i: int
    side: int
    stop: float
    key: tuple

def line(a, b, t):
    return a.price+(b.price-a.price)*(t-a.i)/(b.i-a.i)

def geometry(name, pivots, t, close, prev, atr, history):
    """Pure geometry check on ALREADY CONFIRMED alternating pivots."""
    n = 6 if name == 'symmetric_triangle' else 5 if 'shoulders' in name else 3
    if len(pivots) < n or not np.isfinite(atr) or atr <= 0:
        return None
    p = pivots[-n:]
    if not 12 <= p[-1].i-p[0].i <= 120 or t-p[-1].i > 30:
        return None
    key = tuple(x.i for x in p)
    if name == 'symmetric_triangle':
        hi = [x for x in p if x.kind == 'H']; lo = [x for x in p if x.kind == 'L']
        if len(hi)!=3 or len(lo)!=3:
            return None
        if not (hi[0].price>hi[1].price>hi[2].price and lo[0].price<lo[1].price<lo[2].price):
            return None
        if abs(hi[1].price-line(hi[0],hi[2],hi[1].i))>.5*atr or abs(lo[1].price-line(lo[0],lo[2],lo[1].i))>.5*atr:
            return None
        up, down = line(hi[0],hi[2],t), line(lo[0],lo[2],t)
        initial = line(hi[0],hi[2],p[0].i)-line(lo[0],lo[2],p[0].i)
        if initial < 2*atr or not 0 < up-down <= .75*initial:
            return None
        if prev<=line(hi[0],hi[2],t-1) and close>up:
            return Signal(t,1,down-.1*atr,key)
        if prev>=line(lo[0],lo[2],t-1) and close<down:
            return Signal(t,-1,up+.1*atr,key)
        return None
    bull = name in ('double_bottom','inverse_head_shoulders')
    side = 1 if bull else -1
    want = ('LHLHL' if bull else 'HLHLH') if n==5 else ('LHL' if bull else 'HLH')
    if ''.join(x.kind for x in p)!=want or p[0].i<20:
        return None
    if side*(history[p[0].i]-history[p[0].i-20])>=0:
        return None
    if abs(p[0].price-p[-1].price)>.5*atr:
        return None
    if n==3:
        neck = prevneck = p[1].price
        extreme = min(p[0].price,p[-1].price) if bull else max(p[0].price,p[-1].price)
        height = side*(neck-extreme)
    else:
        if min(side*(p[0].price-p[2].price),side*(p[-1].price-p[2].price))<atr:
            return None
        neck, prevneck = line(p[1],p[3],t), line(p[1],p[3],t-1)
        extreme = p[2].price
        height = side*(line(p[1],p[3],p[2].i)-extreme)
    if height<2*atr or not (side*(prev-prevneck)<=0 and side*(close-neck)>0):
        return None
    return Signal(t,side,extreme-side*.1*atr,key)

def signals(df, name, width=5):
    assert name in NAMES
    h,l,c = (df[k].to_numpy(float) for k in ('high','low','close'))
    prev=np.r_[c[0],c[:-1]]
    atr=pd.Series(np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))).rolling(14).mean().to_numpy()
    pivots=[]; out={}; used=set()
    for t in range(2*width,len(df)):
        i=t-width
        high=h[i]>max(np.max(h[i-width:i]),np.max(h[i+1:t+1]))
        low=l[i]<min(np.min(l[i-width:i]),np.min(l[i+1:t+1]))
        if high != low:
            q=Pivot(i,'H' if high else 'L',float(h[i] if high else l[i]))
            if pivots and pivots[-1].kind==q.kind:
                if (q.price-pivots[-1].price)*(1 if high else -1)>0: pivots[-1]=q
            else: pivots.append(q)
            pivots=pivots[-6:]
        s=geometry(name,pivots,t,c[t],c[t-1],atr[t],c)
        if s and s.key not in used:
            used.add(s.key); out[t]=s
    return out

def holm(pvals):
    order=np.argsort(pvals); out=np.zeros(len(pvals)); last=0.
    for rank,i in enumerate(order):
        last=max(last,min(1.,float(pvals[i])*(len(pvals)-rank)));out[i]=last
    return out.tolist()

def metrics(equity, times, start_equity=10000.):
    eq=np.r_[start_equity,equity]
    dd=float(np.max(1-eq/np.maximum.accumulate(eq)))
    d=pd.Series(equity,index=pd.to_datetime(times,unit='ms',utc=True)).resample('1D').last()
    daily=np.diff(np.r_[start_equity,d.to_numpy()])/np.r_[start_equity,d.to_numpy()[:-1]]
    std=float(np.std(daily,ddof=1)) if len(daily)>1 else 0.
    return {'total_return':float(equity[-1]/start_equity-1), 'max_drawdown':dd,
            'sharpe':float(np.mean(daily)/std*np.sqrt(365)) if std>0 else 0.,
            'daily_returns':daily.tolist()}

def simulate(df, funding, sigs, start=0, fee_bps=5., slip_bps=5., schedule=None):
    """Fixed quantity cash + signed mark-to-market. schedule is timing-control only.

    Funding at candle OPEN settles to old holdings before any open execution.
    Stops/targets tested after the open. Funding arrays must align to bar opens.
    schedule maps entry indices -> (side, duration in bars), bypasses pattern exits.
    """
    a=df[['open_time','open','high','low','close']].to_numpy(float)
    rates={int(t):(float(r),float(m)) for t,r,m in funding[['fundingTime','fundingRate','markPrice']].itertuples(index=False,name=None)}
    cash=10000.; q=0.; trade=None; ledger=[]; equities=[]; exposed=0; bankrupt=False
    fee=fee_bps/10000; slip=slip_bps/10000; total_fees=0.; total_funding=0.

    def exit_trade(i, raw, reason, slippage=True):
        nonlocal cash,q,trade,total_fees
        side=1 if q>0 else -1
        fill=raw*(1-side*slip) if slippage else raw
        charge=abs(q)*fill*fee
        pnl=q*(fill-trade['entry_price'])
        cash += pnl-charge; total_fees+=charge
        trade.update(exit_i=i,exit_price=fill,exit_reason=reason,
                     price_pnl=pnl,fees=trade['entry_fee']+charge,
                     net_pnl=pnl-trade['entry_fee']-charge+trade['funding'],
                     duration=i-trade['entry_i']+(reason=='final_close'),equity_after=cash)
        ledger.append(trade);q=0.;trade=None

    for i in range(start,len(a)):
        ts,op,hi,lo,cl=a[i]; exited=False
        if int(ts) in rates and q:
            rate,mark=rates[int(ts)]; payment=-q*mark*rate
            cash+=payment;total_funding+=payment;trade['funding']+=payment
        if q:
            side=1 if q>0 else -1
            if schedule is not None:
                if i>=trade['planned_exit']: exit_trade(i,op,'scheduled');exited=True
            elif side*(op-trade['stop'])<=0:
                exit_trade(i,op,'gap_stop');exited=True
            elif side*(op-trade['target'])>=0:
                exit_trade(i,trade['target'],'gap_target',False);exited=True
            elif i-trade['entry_i']>=42:
                exit_trade(i,op,'time');exited=True
        if not q and not exited and cash>0:
            s=sigs.get(i-1) if i>start else None
            order=schedule.get(i) if schedule is not None else None
            if schedule is not None: s=Signal(i-1,order[0],0.,()) if order else None
            if s and (schedule is not None or s.side*(op-s.stop)>0):
                fill=op*(1+s.side*slip)
                q=s.side*cash/fill
                charge=abs(q)*fill*fee;cash-=charge;total_fees+=charge
                risk=s.side*(fill-s.stop)
                trade={'entry_i':i,'signal_i':i-1,'side':s.side,'quantity':q,
                       'entry_price':fill,'entry_fee':charge,'funding':0.,
                       'stop':s.stop,'target':fill+s.side*2*risk,
                       'planned_exit':i+order[1] if order else None,'pattern':s.key}
        if q:
            exposed+=1
            if schedule is None:
                stop_hit=lo<=trade['stop'] if q>0 else hi>=trade['stop']
                target_hit=hi>=trade['target'] if q>0 else lo<=trade['target']
                if stop_hit: exit_trade(i,trade['stop'],'stop')
                elif target_hit: exit_trade(i,trade['target'],'target',False)
        if q and i==len(a)-1: exit_trade(i,cl,'final_close')
        equity=cash+(q*(cl-trade['entry_price']) if q else 0.)
        if equity<=0:
            bankrupt=True
            if q: exit_trade(i,cl,'bankrupt')
            # Preserve debt/negative cash. Do not invent limited liability by
            # clamping to zero, and never manufacture a zero-Sharpe benchmark.
            equities.append(cash);equities.extend([cash]*(len(a)-i-1));break
        equities.append(equity)
    eq=np.asarray(equities)
    assert abs(float(eq[-1])-10000.-sum(t['net_pnl'] for t in ledger))<1e-6
    result=metrics(eq,a[start:,0].astype('int64')) if not bankrupt else {
        'total_return':float(eq[-1]/10000.-1),
        'max_drawdown':float(np.max(1-np.r_[10000.,eq]/np.maximum.accumulate(np.r_[10000.,eq]))),
        'sharpe':None,'daily_returns':[]}
    result.update(trades=len(ledger),fees=total_fees,funding=total_funding,
                  exposed_bars=exposed,bankrupt=bankrupt,
                  mean_holding_hours=float(np.mean([t['duration']*4 for t in ledger])) if ledger else 0.)
    return result,ledger,eq

def random_schedule(ledger, start, stop, seed):
    """Permute realised signed durations and distribute flat bars at random.

    Intrabar exits are rounded UP to a complete bar in this diagnostic. One cash
    bar between trades accommodates the simulator's no-same-bar-reentry rule.
    """
    rng=np.random.default_rng(seed)
    durations=[(t['side'],max(1,t['exit_i']-t['entry_i']+(t['exit_reason'] not in ('time','gap_stop','gap_target','scheduled')))) for t in ledger]
    rng.shuffle(durations)
    spare=(stop-start)-sum(d for _,d in durations)-max(0,len(durations)-1)
    if spare<0: raise ValueError('Timing control cannot fit realised durations')
    gaps=rng.multinomial(spare,np.full(len(durations)+1,1/(len(durations)+1)))
    out={};cursor=start+int(gaps[0])
    for j,(side,duration) in enumerate(durations):
        out[cursor]=(side,duration)
        cursor+=duration+1+int(gaps[j+1])
    return out
