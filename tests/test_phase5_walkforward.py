import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.research.walkforward import run_walkforward
from app.strategy_research.evaluation.backtest import run_backtest


def policy(**updates):
    value = dict(experiment_id='a'*64, dataset_id='b'*64, strategy_id='synthetic',
        start_ms=0,end_ms=100,train_span_ms=40,test_span_ms=20,step_ms=20,
        mode='rolling',min_train_rows=2,min_test_rows=2,embargo_ms=0,
        cost_bps=5,slippage_bps=2,timeframe='1h',callback_version='c'*64,
        regime_policy_ref='d'*64,aggregation='disjoint_oos')
    value.update(updates)
    return value


def bars(start,end):
    return [dict(sample_id=str(t),open_time=t,close_time=t+10,
                 close=100+t/10,label_start_ms=t+10,label_end_ms=t+20)
            for t in range(start,end,10)]


def run(root, p=None, **overrides):
    args=dict(read_bars=bars,select_parameters=lambda rows: {'direction':1},
              signal=lambda history,params: params['direction'],regime=lambda history:'calm')
    args.update(overrides)
    return run_walkforward(policy() if p is None else p,evidence_directory=root,**args)


def test_freeze_is_durable_before_first_oos_and_selector_is_purged(tmp_path):
    selected=[]
    def select(rows):
        selected.append(rows)
        assert max(r['label_end_ms'] for r in rows)<=max(r['close_time'] for r in rows)+10
        return {'direction':1}
    def reader(start,end):
        if start==40 and end==60:
            artifact=json.loads((tmp_path/'params-0.json').read_text())
            assert artifact['parameters']=={'direction':1}
            assert (tmp_path/'open-0.json').is_file()
        return bars(start,end)
    report=run(tmp_path,select_parameters=select,read_bars=reader)
    assert report['walkforward_status']=='VALID'
    assert [r['sample_id'] for r in selected[0]]==['0','10','20']
    assert report['windows'][0]['coverage']['purged_rows']==1
    assert (tmp_path/'REPORT.json').is_file()


@pytest.mark.parametrize('mode',['rolling','expanding'])
def test_deterministic_replay_and_numerical_parity(tmp_path,mode):
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    x=run(a,policy(mode=mode));y=run(b,policy(mode=mode))
    assert x==y
    assert x['walkforward_status']=='VALID'
    frame=pd.DataFrame(bars(30,60))
    expected=run_backtest(frame,pd.Series([1.,1.,1.]),'1h',5,2).net_returns[1:]
    np.testing.assert_allclose(x['aggregate']['net']['returns'][:2],expected)
    assert x['aggregate']['by_regime']['calm']['n_rows']==6
    assert x['windows'][1]['train']==([20,60] if mode=='rolling' else [0,60])


def test_prefixes_never_contain_future_data(tmp_path):
    seen=[]
    def signal(history,params):
        seen.append([r['close_time'] for r in history])
        assert set(history[0])=={'open_time','close_time','close'}
        return 1
    report=run(tmp_path,signal=signal)
    assert report['walkforward_status']=='VALID'
    assert [max(h) for h in seen]==[40,50,60,70,80,90]
    for w in report['windows']:
        assert all(r['max_available_ms']==r['decision_time_ms']<r['priced_close_ms'] for r in w['runtime_ledger'])


@pytest.mark.parametrize('update',[
    {'step_ms':10},{'train_span_ms':True},{'test_span_ms':0},{'cost_bps':float('nan')},
    {'min_train_rows':0},{'mode':'random'},{'strategy_id':'H-025'}, {'start_ms':False},
    {'end_ms':59}, {'aggregation':'overlap'}, {'unexpected':1}])
def test_bad_policy_never_reads(tmp_path,update):
    report=run(tmp_path,policy(**update),read_bars=lambda *args:pytest.fail('data opened'))
    assert report['reason_code']=='WINDOW_POLICY_INVALID'
    assert not list(tmp_path.iterdir())


def test_insufficient_rows_stop_and_never_read_oos(tmp_path):
    calls=[]
    def reader(a,b):calls.append((a,b));return bars(a,b)
    result=run(tmp_path,policy(min_train_rows=10),read_bars=reader)
    assert result['walkforward_status']=='INVALID'
    assert result['windows'][0]['window_status']=='INSUFFICIENT_DATA'
    assert result['windows'][1]['window_status']=='NOT_EVALUATED'
    assert result['aggregate'] is None
    assert calls==[(0,40)]


def test_changed_parameter_file_fails_closed(tmp_path):
    def signal(history,params):
        (tmp_path/'params-0.json').write_text('{}')
        return 1
    result=run(tmp_path,signal=signal)
    assert result['reason_code']=='PARAM_NOT_FROZEN'
    assert result['aggregate'] is None


def test_callback_parameter_mutation_is_refused(tmp_path):
    def signal(history,params):
        params['direction']=0
        history.clear()
        return 1
    result=run(tmp_path,signal=signal)
    assert result['reason_code']=='PARAM_NOT_FROZEN'
    assert json.loads((tmp_path/'params-0.json').read_text())['parameters']=={'direction':1}


@pytest.mark.parametrize('bad', ['future','gap','nan','duplicate'])
def test_invalid_source_refuses(tmp_path,bad):
    def reader(a,b):
        rows=bars(a,b)
        if bad=='future': rows[-1]['close_time']=b+1
        if bad=='gap': rows.pop(1)
        if bad=='nan': rows[0]['close']=float('nan')
        if bad=='duplicate': rows[-1]['sample_id']=rows[0]['sample_id']
        return rows
    result=run(tmp_path,read_bars=reader)
    assert result['walkforward_status']=='INVALID'
    assert not (tmp_path/'open-0.json').exists()


def test_existing_root_never_resumes_or_overwrites(tmp_path):
    first=run(tmp_path)
    snapshot={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(FileExistsError): run(tmp_path)
    assert snapshot=={p.name:p.read_bytes() for p in tmp_path.iterdir()}


def test_nonfinite_params_prevent_oos(tmp_path):
    calls=[]
    def reader(a,b):calls.append((a,b));return bars(a,b)
    report=run(tmp_path,read_bars=reader,select_parameters=lambda rows:{'x':float('inf')})
    assert report['reason_code']=='PARAM_NOT_FROZEN'
    assert calls==[(0,40)]


def test_single_window_and_gapped_steps(tmp_path):
    report=run(tmp_path,policy(end_ms=60,step_ms=100))
    assert report['walkforward_status']=='VALID'
    assert len(report['windows'])==1


def test_root_symlink_refused(tmp_path):
    link=tmp_path/'link';root=tmp_path/'actual';root.mkdir();link.symlink_to(root)
    with pytest.raises((OSError,ValueError)): run(link)
    assert not list(root.iterdir())


def test_regime_reports_both_gross_and_net(tmp_path):
    result=run(tmp_path)
    group=result['aggregate']['by_regime']['calm']
    assert group['gross']==result['aggregate']['gross']
    assert group['net']==result['aggregate']['net']


def test_parameter_fsync_failure_never_opens_oos(tmp_path,monkeypatch):
    import app.research.walkforward as wf
    real=wf.os.fsync
    def fail(fd):
        if (tmp_path/'params-0.json').exists(): raise OSError('injected durability failure')
        return real(fd)
    calls=[]
    def reader(a,b):calls.append((a,b));return bars(a,b)
    monkeypatch.setattr(wf.os,'fsync',fail)
    with pytest.raises(OSError): run(tmp_path,read_bars=reader)
    assert calls==[(0,40)]
    assert not (tmp_path/'open-0.json').exists()


def test_report_publication_failure_is_not_success(tmp_path,monkeypatch):
    import app.research.walkforward as wf
    real=wf._Store.put
    def fail(self,name,value):
        if name=='REPORT.json': raise OSError('injected report failure')
        return real(self,name,value)
    monkeypatch.setattr(wf._Store,'put',fail)
    with pytest.raises(OSError):run(tmp_path)
    assert (tmp_path/'open-2.json').exists()


def test_reader_failure_preserves_consumed_window_and_stops(tmp_path):
    calls=[]
    def reader(a,b):
        calls.append((a,b))
        if a==40 and b==60:raise RuntimeError('injected reader failure')
        return bars(a,b)
    result=run(tmp_path,read_bars=reader)
    assert result['walkforward_status']=='INVALID'
    assert result['windows'][1]['window_status']=='NOT_EVALUATED'
    assert (tmp_path/'open-0.json').exists()
    assert calls==[(0,40),(40,60)]


def test_parameter_symlink_substitution_refuses(tmp_path):
    outside=tmp_path/'other.json'
    def reader(a,b):
        if a==40 and b==60:
            path=tmp_path/'params-0.json';outside.write_bytes(path.read_bytes())
            path.unlink();path.symlink_to(outside)
        return bars(a,b)
    result=run(tmp_path,read_bars=reader)
    assert result['reason_code']=='PARAM_NOT_FROZEN'


def test_costs_charge_first_entry_and_each_reversal(tmp_path):
    def signal(history,params): return 1 if history[-1]['close_time']%20==0 else -1
    result=run(tmp_path,signal=signal)
    assert result['walkforward_status']=='VALID'
    for window in result['windows']:
        assert window['modeled_costs']==pytest.approx([.0007,.0014])
        assert window['net_metrics']['n_rows']==2


def test_nonfinite_signal_refused(tmp_path):
    result=run(tmp_path,signal=lambda *args:float('nan'))
    assert result['reason_code']=='LOOKAHEAD_DETECTED'
    assert result['aggregate'] is None


def test_insolvent_costs_refused(tmp_path):
    result=run(tmp_path,policy(cost_bps=20000))
    assert result['reason_code']=='COST_MODEL_INVALID'
    assert result['aggregate'] is None


def test_insolvent_short_return_refused(tmp_path):
    def reader(a,b):
        rows=bars(a,b)
        if a==40 and b==60: rows[0]['close']=500
        return rows
    result=run(tmp_path,read_bars=reader,signal=lambda *args:-1)
    assert result['reason_code']=='COST_MODEL_INVALID'
    assert result['aggregate'] is None
