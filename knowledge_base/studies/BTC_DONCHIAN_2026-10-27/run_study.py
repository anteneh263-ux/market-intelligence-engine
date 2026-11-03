"""Isolated, fixed-rule study using MIE v4. See PLAN.md before running."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
import hashlib
import json
import datetime
import subprocess
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
ENGINE = Path('/Users/antenehhailu/MIE_CALIBRATION_WORKTREE')
sys.path.insert(0, str(ENGINE))
from app.strategy_research.strategies.base import Strategy
from app.strategy_research.strategies.registry import register_strategy
from app.strategy_research.evaluation.frozen import MATRIX_V4, EvalRequest
from app.strategy_research.evaluation.pipeline import evaluate
from app.strategy_research.evaluation.walkforward import walk_forward

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def save(name, obj):
    path = ROOT / name
    with path.open('x') as f:
        json.dump(obj, f, indent=2, default=str, allow_nan=False)

class Donchian(Strategy):
    strategy_id = 'STUDY-BTC-DONCHIAN-20261027'
    name = 'BTC Donchian 20/10 long-cash (not full Turtle)'
    strategy_class = 'breakout'
    direction = 'long_only'
    declared_inputs = {'high', 'low', 'close'}
    default_params = {'entry': 20, 'exit': 10}

    def positions(self, df, params=None):
        p = params or self.default_params
        upper = df.high.shift(1).rolling(p['entry']).max().to_numpy()
        lower = df.low.shift(1).rolling(p['exit']).min().to_numpy()
        pos, held = [], 0.0
        for c, hi, lo in zip(df.close, upper, lower):
            if held == 0 and c > hi:
                held = 1.0
            elif held == 1 and c < lo:
                held = 0.0
            pos.append(held)
        return pd.Series(pos, index=df.index, dtype=float)

def checks():
    s = Donchian()
    d = pd.DataFrame({'close': [10.]*20 + [11., 10., 8., 10.],
                      'high': [10.]*20 + [12., 11., 10., 11.],
                      'low': [9.]*20 + [10., 9., 7., 9.]})
    assert s.positions(d).tolist() == [0.]*20 + [1., 1., 0., 0.]
    equality = d.copy(); equality.loc[20, 'close'] = 10.
    assert s.positions(equality).iloc[20] == 0
    # Current high can exceed close: including it would suppress this valid signal.
    assert s.positions(d).iloc[20] == 1
    rng = np.random.default_rng(17)
    c = 100*np.exp(np.cumsum(rng.normal(0, .03, 800)))
    frame = pd.DataFrame({'close':c, 'high':c*1.01, 'low':c*.99})
    whole = s.positions(frame)
    for n in (25, 99, 200, 500):
        pd.testing.assert_series_equal(whole.iloc[:n], s.positions(frame.iloc[:n]))
        changed = frame.copy(); changed.loc[n:, ['high','low','close']] *= 9
        pd.testing.assert_series_equal(whole.iloc[:n], s.positions(changed).iloc[:n])
    return {'transition_test':True, 'strict_boundary':True,
            'prior_bar_channel':True, 'prefix_and_future_checks':8}

def fetch():
    assert not (ROOT/'BTCUSDT_4h.csv').exists(), 'Refuse overwrite'
    start = int(pd.Timestamp('2018-01-01', tz='UTC').timestamp()*1000)
    end = int(pd.Timestamp('2025-01-01', tz='UTC').timestamp()*1000)
    step = 14400000
    cursor, rows = start, []
    session = requests.Session()
    while cursor < end:
        r = session.get('https://api.binance.com/api/v3/klines', params={
            'symbol':'BTCUSDT','interval':'4h','startTime':cursor,
            'endTime':end-1,'limit':1000}, timeout=30)
        r.raise_for_status()
        chunk = r.json()
        if not isinstance(chunk, list) or not chunk:
            raise ValueError('Missing market data')
        rows.extend(chunk)
        nxt = int(chunk[-1][0])+step
        assert nxt > cursor
        cursor = nxt
    df = pd.DataFrame([[int(k[0])]+[float(x) for x in k[1:6]] for k in rows],
                      columns=['open_time','open','high','low','close','volume'])
    df.to_csv(ROOT/'RAW_BTCUSDT_4h.csv', index=False)
    missing = sorted(set(range(start,end,step))-set(df.open_time))
    print('Data quality:', {'rows':len(df), 'duplicates':int(df.open_time.duplicated().sum()),
          'missing_utc':[str(pd.to_datetime(t,unit='ms',utc=True)) for t in missing]}, flush=True)
    # Halt rather than silently fill missing candles or use synthetic prices.
    assert df.open_time.tolist() == list(range(start,end,step)), 'Missing/duplicate candles'
    assert np.isfinite(df.to_numpy()).all()
    assert (df[['open','high','low','close']] > 0).all().all()
    assert (df.high >= df[['open','close','low']].max(axis=1)).all()
    assert (df.low <= df[['open','close','high']].min(axis=1)).all()
    assert (df.volume >= 0).all()
    df.to_csv(ROOT/'BTCUSDT_4h.csv', index=False)
    daily = df.groupby(df.open_time//86400000, sort=True).agg(
        open_time=('open_time','first'), open=('open','first'), high=('high','max'),
        low=('low','min'), close=('close','last'), volume=('volume','sum'))
    assert (df.groupby(df.open_time//86400000).size() == 6).all()
    daily.to_csv(ROOT/'BTCUSDT_1d.csv', index=False)
    save('DATA_MANIFEST.json', {'source':'https://api.binance.com/api/v3/klines',
        'retrieved_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'start_inclusive':'2018-01-01', 'end_exclusive':'2025-01-01',
        'bars_4h':len(df),'bars_1d':len(daily),'gaps':0,
        'hashes':{p.name:sha(p) for p in ROOT.glob('BTCUSDT_*.csv')}})

def prepare_complete_suffix():
    df = pd.read_csv(ROOT/'RAW_BTCUSDT_4h.csv')
    start = int(pd.Timestamp('2020-02-20',tz='UTC').timestamp()*1000)
    end = int(pd.Timestamp('2025-01-01',tz='UTC').timestamp()*1000)
    df = df[df.open_time >= start].reset_index(drop=True)
    assert df.open_time.tolist() == list(range(start,end,14400000))
    assert np.isfinite(df.to_numpy()).all()
    assert (df[['open','high','low','close']] > 0).all().all()
    assert (df.high >= df[['open','close','low']].max(axis=1)).all()
    assert (df.low <= df[['open','close','high']].min(axis=1)).all()
    assert (df.volume >= 0).all()
    assert (df.groupby(df.open_time//86400000).size() == 6).all()
    assert not (ROOT/'BTCUSDT_4h.csv').exists()
    df.to_csv(ROOT/'BTCUSDT_4h.csv',index=False)
    daily = df.groupby(df.open_time//86400000,sort=True).agg(
        open_time=('open_time','first'),open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last'),volume=('volume','sum'))
    daily.to_csv(ROOT/'BTCUSDT_1d.csv',index=False)
    save('DATA_MANIFEST.json',{'source':'https://api.binance.com/api/v3/klines',
        'prepared_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'start_inclusive':'2020-02-20','end_exclusive':'2025-01-01',
        'bars_4h':len(df),'bars_1d':len(daily),'gaps':0,
        'raw_sha256':sha(ROOT/'RAW_BTCUSDT_4h.csv'),
        'amendment_sha256':sha(ROOT/'DATA_AMENDMENT.md'),
        'hashes':{p.name:sha(p) for p in ROOT.glob('BTCUSDT_*.csv')}})

def run():
    assert not (ROOT/'SUMMARY.json').exists(), 'Refuse rerun over results'
    validation = checks()
    manifest = json.loads((ROOT/'DATA_MANIFEST.json').read_text())
    for name,h in manifest['hashes'].items():
        assert sha(ROOT/name) == h
    commit = subprocess.check_output(['git','-C',str(ENGINE),'rev-parse','HEAD'],text=True).strip()
    assert commit == 'c41afe43fc70d50140754558fda62a2deea48761'
    save('RUN_MANIFEST.json', {'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'engine_root':str(ENGINE),'engine_commit':commit,'matrix_hash':MATRIX_V4.hash(),
        'plan_hash':sha(ROOT/'PLAN.md'),'runner_hash':sha(Path(__file__)),
        'data_manifest_hash':sha(ROOT/'DATA_MANIFEST.json'),'checks':validation,
        'engine_source_hashes':{str(p.relative_to(ENGINE)):sha(p)
         for p in sorted((ENGINE/'app/strategy_research').rglob('*.py'))}})
    register_strategy(Donchian)
    summary = {}
    for tf in ('4h','1d'):
        req = EvalRequest(Donchian.strategy_id,'csv',['BTC/USDT'],tf,
                          matrix_version=MATRIX_V4.version)
        report = evaluate(req,MATRIX_V4,provider_kwargs={'root':str(ROOT)})
        save('REPORT_'+tf+'.json',report.to_dict())
        df = pd.read_csv(ROOT/('BTCUSDT_'+tf+'.csv'))
        wf = walk_forward(df,Donchian(),tf,5,5,MATRIX_V4.n_splits,MATRIX_V4.min_train)
        pd.DataFrame({'open_time':wf.oos_open_time,'net_return':wf.oos_net,
                      'signal_position':wf.oos_pos}).to_csv(ROOT/('OOS_'+tf+'.csv'),index=False)
        bh = df.close.pct_change().fillna(0).to_numpy()[wf.oos_index]
        eq = np.cumprod(1+bh)
        summary[tf] = {'verdict':report.verdict,'reasons':report.verdict_reasons,
                       'headline':report.headline,'robustness':report.robustness_score,
                       'effective_test_folds':len(wf.fold_boundaries),
                       'oos_start':str(pd.to_datetime(wf.oos_open_time[0],unit='ms',utc=True)),
                       'oos_end':str(pd.to_datetime(wf.oos_open_time[-1],unit='ms',utc=True)),
                       'buy_hold_total_return_gross':float(eq[-1]-1),
                       'buy_hold_max_drawdown':float(np.max(1-eq/np.maximum.accumulate(eq)))}
        print(tf,json.dumps(summary[tf]),flush=True)
    save('SUMMARY.json',summary)

if __name__ == '__main__':
    if sys.argv[1] == 'check':
        print(json.dumps(checks()))
    elif sys.argv[1] == 'fetch':
        fetch()
    elif sys.argv[1] == 'run':
        run()
    elif sys.argv[1] == 'prepare':
        prepare_complete_suffix()
    else:
        raise SystemExit('check | fetch | run')
