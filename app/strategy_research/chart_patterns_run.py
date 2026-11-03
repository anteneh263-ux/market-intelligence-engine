"""CLI for additive mie-chart-patterns-v1. No existing MIE records are modified."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import pandas as pd
import requests
from .chart_patterns_v1 import VERSION,NAMES,STEP,signals,simulate,random_schedule,holm

BASE='https://fapi.binance.com'
START=int(pd.Timestamp('2020-02-20',tz='UTC').timestamp()*1000)
END=int(pd.Timestamp('2025-01-01',tz='UTC').timestamp()*1000)
TEST=int(pd.Timestamp('2021-01-01',tz='UTC').timestamp()*1000)
SOURCE=Path(__file__).resolve()
REPO=SOURCE.parents[2]

def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(path,obj):
    with Path(path).open('x') as f: json.dump(obj,f,indent=2,allow_nan=False)

def hashes():
    paths=[SOURCE,SOURCE.with_name('chart_patterns_v1.py'),
           REPO/'tests/test_chart_patterns_v1.py',REPO/'docs/BTC_CHART_PATTERNS_V1_PLAN.md']
    return {str(p.relative_to(REPO)):digest(p) for p in paths}

def get(session,path,params):
    for retry in range(5):
        r=session.get(BASE+path,params=params,timeout=30)
        if r.status_code in (429,500,502,503,504): time.sleep(2**retry);continue
        r.raise_for_status();return r.json()
    raise RuntimeError('API retries exhausted')

def fetch(root):
    root.mkdir(parents=True,exist_ok=True)
    save(root/'REGISTRATION.json',{'version':VERSION,'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
         'source_hashes':hashes(),'start':START,'end_exclusive':END,'test_start':TEST,
         'rules':list(NAMES),'trials':5,'status':'registered_before_data_and_returns'})
    session=requests.Session();cursor=START;rows=[]
    while cursor<END:
        part=get(session,'/fapi/v1/klines',{'symbol':'BTCUSDT','interval':'4h','startTime':cursor,'endTime':END-1,'limit':1500})
        if not part: raise ValueError('Empty candle page')
        rows+=part;nxt=int(part[-1][0])+STEP
        assert nxt>cursor;cursor=nxt
    save(root/'raw_klines.json',rows)
    d=pd.DataFrame([[int(x[0])]+[float(y) for y in x[1:6]] for x in rows],
                   columns=['open_time','open','high','low','close','volume'])
    print('candles',len(d),flush=True)
    cursor=START;rows=[]
    while cursor<END:
        part=get(session,'/fapi/v1/fundingRate',{'symbol':'BTCUSDT','startTime':cursor,'endTime':END-1,'limit':1000})
        if not part: break
        rows+=part;nxt=int(part[-1]['fundingTime'])+1
        assert nxt>cursor;cursor=nxt
    save(root/'raw_funding.json',rows)
    f=pd.DataFrame(rows)[['fundingTime','fundingRate','markPrice']].astype({'fundingTime':'int64','fundingRate':float,'markPrice':float})
    validate(d,f)
    d.to_csv(root/'BTCUSDT_PERP_4h.csv',index=False);f.to_csv(root/'funding.csv',index=False)
    save(root/'DATA_MANIFEST.json',{'source':BASE,'retrieved_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
         'bars':len(d),'funding_events':len(f),'gaps':0,
         'hashes':{p.name:digest(p) for p in root.glob('*.csv')}})
    print('data validated',len(d),len(f),flush=True)

def validate(d,f):
    if d.open_time.tolist()!=list(range(START,END,STEP)):
        missing=sorted(set(range(START,END,STEP))-set(d.open_time))
        raise ValueError(f'Invalid candles; missing={missing[:20]}')
    assert np.isfinite(d.to_numpy()).all()
    assert (d[['open','high','low','close']]>0).all().all()
    assert (d.high>=d[['open','low','close']].max(axis=1)).all()
    assert (d.low<=d[['open','high','close']].min(axis=1)).all()
    assert (d.volume>=0).all()
    expected=list(range(START,END,2*STEP))
    if f.fundingTime.tolist()!=expected:
        missing=sorted(set(expected)-set(f.fundingTime));extra=sorted(set(f.fundingTime)-set(expected))
        raise ValueError(f'Funding timestamp mismatch; missing={missing[:10]},extra={extra[:10]}')
    assert np.isfinite(f.to_numpy()).all() and (f.markPrice>0).all()

def prepare(root):
    amendment=REPO/'docs/BTC_CHART_PATTERNS_V1_DATA_AMENDMENT.md'
    save(root/'DATA_AMENDMENT_REGISTRATION.json',{'source_hashes':hashes(),
        'amendment_hash':digest(amendment),'time':dt.datetime.now(dt.timezone.utc).isoformat(),
        'original_registration_hash':digest(root/'REGISTRATION.json'),
        'reason':'Missing funding marks and timestamp reporting jitter; no results viewed'})
    rows=json.loads((root/'raw_klines.json').read_text())
    d=pd.DataFrame([[int(x[0])]+[float(y) for y in x[1:6]] for x in rows],
        columns=['open_time','open','high','low','close','volume'])
    raw=json.loads((root/'raw_funding.json').read_text())
    session=requests.Session();cursor=START;marks=[]
    while cursor<END:
        part=get(session,'/fapi/v1/markPriceKlines',{'symbol':'BTCUSDT','interval':'4h',
            'startTime':cursor,'endTime':END-1,'limit':1500})
        if not part: raise ValueError('Empty mark-price page')
        marks+=part;nxt=int(part[-1][0])+STEP
        assert nxt>cursor;cursor=nxt
    save(root/'raw_mark_klines.json',marks)
    assert [int(x[0]) for x in marks]==list(range(START,END,STEP))
    mark_map={int(x[0]):float(x[1]) for x in marks}
    clean=[];proxy=0;normalized=0
    for row in raw:
        ts=int(row['fundingTime']);base=ts//(2*STEP)*(2*STEP)
        assert 0<=ts-base<=1000
        normalized+=int(ts!=base)
        missing=not row.get('markPrice');proxy+=int(missing)
        clean.append({'fundingTime':base,'fundingRate':float(row['fundingRate']),
            'markPrice':mark_map[base] if missing else float(row['markPrice']),
            'sourceFundingTime':ts,'markProxy':int(missing)})
    f=pd.DataFrame(clean);validate(d,f)
    d.to_csv(root/'BTCUSDT_PERP_4h.csv',index=False);f.to_csv(root/'funding.csv',index=False)
    save(root/'DATA_MANIFEST.json',{'source':BASE,'bars':len(d),'funding_events':len(f),
        'proxy_mark_events':proxy,'normalized_timestamp_events':normalized,
        'max_reported_offset_ms':max(int(x['fundingTime'])%(2*STEP) for x in raw),
        'hashes':{p.name:digest(p) for p in root.glob('*.csv')},
        'raw_hashes':{p.name:digest(p) for p in root.glob('raw_*.json')}})
    print('Validated',len(d),'bars',len(f),'funding events;',proxy,'historical mark-open proxies',flush=True)

def run(root):
    reg=json.loads((root/'DATA_AMENDMENT_REGISTRATION.json').read_text())
    assert hashes()==reg['source_hashes'],'Source changed after registration'
    assert digest(REPO/'docs/BTC_CHART_PATTERNS_V1_DATA_AMENDMENT.md')==reg['amendment_hash']
    manifest=json.loads((root/'DATA_MANIFEST.json').read_text())
    for name,h in manifest['hashes'].items(): assert digest(root/name)==h
    d=pd.read_csv(root/'BTCUSDT_PERP_4h.csv');f=pd.read_csv(root/'funding.csv');validate(d,f)
    save(root/'RUN_STARTED.json',{'version':VERSION,'time':dt.datetime.now(dt.timezone.utc).isoformat(),
                               'registration_hash':digest(root/'REGISTRATION.json')})
    start=int(np.flatnonzero(d.open_time.to_numpy()>=TEST)[0]);stop=len(d)
    bh,bhledger,bheq=simulate(d,f,{},start,schedule={start:(1,stop-start)})
    all_results={};pvals=[]
    for name in NAMES:
        ss=signals(d,name)
        for n in (start+100,len(d)//2,len(d)-1):
            assert signals(d.iloc[:n],name)=={i:s for i,s in ss.items() if i<n}
        result,tr,eq=simulate(d,f,ss,start)
        assert abs(eq[-1]-10000-sum(t['net_pnl'] for t in tr))<1e-6
        ladder=[]
        for fee in (0.,5.,10.):
            for slip in (0.,5.,10.):
                met,_,_=simulate(d,f,ss,start,fee,slip)
                ladder.append({'fee_bps':fee,'slippage_bps':slip,**{k:v for k,v in met.items() if k!='daily_returns'}})
        controls=[]
        if tr:
            for seed in range(73001,73200):
                schedule=random_schedule(tr,start,stop,seed)
                met,ctl,_=simulate(d,f,{},start,schedule=schedule)
                assert len(ctl)==len(tr),'Control lost trades'
                controls.append(met['sharpe'])
        p=(1+sum(x is not None and x>=result['sharpe'] for x in controls))/(1+len(controls)) if controls and result['sharpe'] is not None else 1.
        pvals.append(p)
        year={};prior=10000.
        timestamps=pd.to_datetime(d.open_time.iloc[start:],unit='ms',utc=True)
        for y in sorted(set(timestamps.dt.year)):
            last=float(eq[(timestamps.dt.year==y).to_numpy()][-1]);year[str(y)]=last/prior-1;prior=last
        item={'name':name,'signals_after_test_start':sum(i>=start for i in ss),
              'metrics':result,'year_returns':year,'cost_ladder':ladder,
              'random_timing_sharpes':controls,'timing_p_raw':p,
              'net_positive_cost_cells':sum(x['total_return']>0 for x in ladder)}
        all_results[name]=item
        save(root/(name+'_trades.json'),tr)
        pd.DataFrame({'open_time':d.open_time.iloc[start:].to_numpy(),'equity':eq}).to_csv(root/(name+'_equity.csv'),index=False)
        print(name,{k:v for k,v in result.items() if k!='daily_returns'},'p=',p,flush=True)
    adj=holm(pvals)
    for name,p in zip(NAMES,adj):
        a=all_results[name];m=a['metrics'];a['timing_p_holm']=p
        criteria={'enough_trades':m['trades']>=30,'positive_net':m['total_return']>0,
                  'drawdown_under_60pct':m['max_drawdown']<=.6,
                  'beats_buy_hold_sharpe':m['sharpe'] is not None and bh['sharpe'] is not None and m['sharpe']>bh['sharpe'],
                  'holm_timing':p<=.05,'positive_majority_costs':a['net_positive_cost_cells']>=5}
        a['criteria']=criteria
        a['screen_status']='qualified_for_further_research' if all(criteria.values()) else 'underpowered' if m['trades']<30 else 'not_qualified'
    save(root/'RESULTS.json',{'version':VERSION,'buy_hold':bh,'rules':all_results})
    save(root/'buy_hold_trades.json',bhledger)
    pd.DataFrame({'open_time':d.open_time.iloc[start:].to_numpy(),'equity':bheq}).to_csv(root/'buy_hold_equity.csv',index=False)
    report(root,bh,all_results)

def report(root,bh,results):
    lines=[f'# BTC chart patterns — {VERSION}','',
      'Historical BTCUSDT perpetuals, 4H. Evaluation 2021–2024; warmup from 2020-02-20.',
      'Five fixed rules, no tuning; next-open fills, 1x entry notional, 2R target, 7-day timeout, actual funding.',
      'Headline fees 5 bps + slippage 5 bps per side. Intrabar ambiguity resolved stop-first.',
      'This is a new exploratory evaluator, not srp-matrix-v4 and not a live/promotion verdict.','',
      '| Pattern | Net return | Sharpe | Max drawdown | Trades | Funding USD | Holm timing p | Screen |',
      '|---|---:|---:|---:|---:|---:|---:|---|']
    for name,a in results.items():
        m=a['metrics'];sr='undefined' if m['sharpe'] is None else f"{m['sharpe']:.2f}"
        lines.append(f"| {name} | {m['total_return']:.1%} | {sr} | {m['max_drawdown']:.1%} | {m['trades']} | {m['funding']:.2f} | {a['timing_p_holm']:.3f} | {a['screen_status']} |")
    bh_sr='undefined (capital exhausted)' if bh['sharpe'] is None else f"{bh['sharpe']:.2f}"
    lines+=['',f"Buy-and-hold SAME perpetual: {bh['total_return']:.1%} net, Sharpe {bh_sr}, max drawdown {bh['max_drawdown']:.1%}.",
      'A capital-exhausted benchmark cannot establish Sharpe superiority. Funding drains collateral in this fixed-quantity model; this is NOT a spot buy-and-hold result or a validated exchange-liquidation simulation.','',
      '## Limits','',
      '- Previously available and partly seen history; no pristine holdout or prospective evidence.',
      '- One exact operational definition per shape; results do not reject all human/chart-pattern interpretations.',
      '- Funding and fill accounting differ from existing MIE matrices. No historical results overwritten.',
      '- 4,048 older funding marks use historical mark-candle open at the scheduled instant; reported timestamp jitter up to 47 ms normalized to the 8h boundary. These are explicit proxies/conventions, not exact millisecond settlement reconstruction.',
      '- Timing controls condition on realised trade durations (intrabar exits rounded to bars), do not replay pattern stops/targets and use only 199 samples. Holm correction covers these five, not all previous research.',
      '- No leverage beyond initial 1x notional; no exchange liquidation, order book, outage or capacity model. Stop-first is a conservative convention, not reconstructed intrabar execution.',
      '- Funding, fees and realised price P&L reconciled to equity for each account. Dedicated local source review; no independent agent review or production verification.',
      '- Sources/parameters are hashed in REGISTRATION.json, data in DATA_MANIFEST.json. See docs/BTC_CHART_PATTERNS_V1_PLAN.md and per-trade ledgers.','']
    (root/'REPORT.md').write_text('\n'.join(lines))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['fetch','prepare','run']);p.add_argument('--output',required=True)
    a=p.parse_args();root=Path(a.output).resolve()
    {'fetch':fetch,'prepare':prepare,'run':run}[a.action](root)
