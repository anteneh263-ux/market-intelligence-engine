import copy,json,os
from pathlib import Path
import pytest
from test_phase6_event_validation import case,payload
from test_phase6_reservation_ledger import inputs
from app.research.phase6_object_store import ObjectWriter
from app.research.phase6_event_journal import EventJournal
from app.research.phase6_validation import canonical_hash as H
from app.paper.session import diagnostic_strategy_v1 as strategy,shadow_reducer_v1 as reducer


def clock(start):
    ticks=iter(range(start,start+10))
    def sample():
        t=next(ticks);return dict(utc_ms=t,monotonic_ns=t*1000000,clock_session_id='test')
    return sample


def setup(tmp_path,warmup=0,count=0,invoke=True):
    p,_=inputs(tmp_path/'registry',tmp_path/'journal')
    pa=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=warmup,targets=['0.25','0.75'])
    p.update(warmup_bars=warmup,max_target='0.75',end_ms=60000*(warmup+count+5),params_hash=H(pa))
    close=p['start_ms']+(count+1)*60000
    m=case()['manifest'];m.update(plan_hash=H(p),params_hash=H(pa),strategy_code_hash=p['strategy_code_hash'],persisted_utc_ms=close+1)
    root=tmp_path/'journal';root.mkdir();w=ObjectWriter(str(root),owner='test',run_id=m['run_id'],max_bytes=1000000)
    j=EventJournal(w,m,utc_ms=close+1,monotonic_ns=(close+1)*1000000)
    j.append('BAR_ACCEPTED',payload('BAR_ACCEPTED'),utc_ms=close+2,monotonic_ns=(close+2)*1000000)
    ss=strategy.initial(pa)
    if count:ss.update(bar_count=count,last_segment_hash='9'*64)
    if invoke:j.run_diagnostic(p,pa,ss,segment_seq=count+1,clock=clock(close+3))
    config={k:p[k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT}
    state=reducer.initial(config)
    if count:
        previous=dict(close_ms=close-60000,close='100',received_utc_ms=close-59999,received_monotonic_ns=(close-59999)*1000000,clock_session_id='test',decision_utc_ms=close-59996,decision_monotonic_ns=(close-59996)*1000000)
        state=reducer.bar(state,previous,None if warmup else '0')
    obs=dict(close_ms=close,close='100',received_utc_ms=close+1,received_monotonic_ns=(close+1)*1000000,clock_session_id='test',decision_utc_ms=close+4,decision_monotonic_ns=(close+4)*1000000)
    return j,w,state,obs,close


def read(w,h):return json.loads((Path(w.root)/(h+'.blob')).read_bytes())
def call(j,s,o,t,**kw):return j.run_bar(s,o,clock=kw.get('clock',clock(t+6)))


def test_bar_to_durable_intent_without_fill(tmp_path,monkeypatch):
    j,w,s,o,t=setup(tmp_path);original=reducer.bar;calls=[]
    def run(state,observation,target):
        calls.append(1);assert read(w,H(s))==s and read(w,H(o))==o
        assert any(json.loads(f.read_bytes()).get('schema_version')=='p6.bar_invocation.v1' for f in Path(w.root).glob('*.blob'))
        return original(state,observation,target)
    monkeypatch.setattr(reducer,'bar',run)
    result=call(j,s,o,t);assert calls==[1]
    assert result['state']['cash']==s['cash'] and result['state']['units']==s['units'] and result['state']['pending']['quantity']=='2.5'
    event=read(w,result['event_hash']);assert event['kind']=='ORDER_INTENT'
    barrier=read(w,result['barrier_hash']);assert barrier['intent_event_hash']==result['event_hash']
    assert barrier['utc_ms']==t+7 and result['state']['pending']['intent_utc_ms']==t+4
    assert result['prospective_quote_ready'] is False and result['runtime_accepted'] is False
    assert read(w,result['commit_hash'])['barrier_hash']==result['barrier_hash'];j.close()


def test_barrier_sample_after_durable_intent(tmp_path):
    j,w,s,o,t=setup(tmp_path);base=clock(t+6);calls=[]
    def sample():
        calls.append(1)
        if len(calls)==2:
            events=[json.loads(f.read_bytes()) for f in Path(w.root).glob('*.blob')]
            assert any(e.get('kind')=='ORDER_INTENT' for e in events)
        return base()
    call(j,s,o,t,clock=sample);assert len(calls)==2;j.close()


@pytest.mark.parametrize('stage',range(1,9))
def test_all_publication_faults_stop_once(tmp_path,monkeypatch,stage):
    j,w,s,o,t=setup(tmp_path);put=w.put;run=reducer.bar;writes=[];calls=[];before=j.snapshot()
    def publish(raw,**kw):
        h=put(raw,**kw);writes.append(h)
        if len(writes)==stage:raise RuntimeError('unknown write')
        return h
    def callback(*a):calls.append(1);return run(*a)
    monkeypatch.setattr(w,'put',publish);monkeypatch.setattr(reducer,'bar',callback)
    with pytest.raises(RuntimeError):call(j,s,o,t)
    assert len(calls)==(0 if stage<=3 else 1)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_head_hash']==before['event_head_hash']
    with pytest.raises(ValueError):call(j,s,o,t)


def test_warmup_has_no_zero_quantity_event(tmp_path):
    j,w,s,o,t=setup(tmp_path,warmup=2);before=j.snapshot();r=call(j,s,o,t)
    assert r['event_hash'] is None and r['barrier_hash'] is None and r['state']['bar_count']==1
    assert j.snapshot()['event_count']==3 and j.snapshot()['event_head_hash']==before['event_head_hash']
    with pytest.raises(ValueError):call(j,s,o,t)


def test_post_warmup_zero_quantity(tmp_path):
    j,w,s,o,t=setup(tmp_path);o['close']='1000000';r=call(j,s,o,t)
    e=read(w,r['event_hash']);assert e['kind']=='NO_TRADE'
    assert read(w,e['payload_hash'])['reason_code']=='ZERO_QUANTITY' and r['barrier_hash'] is None;j.close()


def test_risk_stop_without_order(tmp_path):
    j,w,s,o,t=setup(tmp_path,count=1);s['peak_equity']='2000';r=call(j,s,o,t)
    assert r['state']['stopped'] and r['state']['pending'] is None and r['barrier_hash'] is None
    assert read(w,r['event_hash'])['kind']=='STOP';j.close()


@pytest.mark.parametrize('fault',['config','count','decision','session','late_receipt','close','previous_close'])
def test_inconsistent_input_never_calls_reducer(tmp_path,monkeypatch,fault):
    j,w,s,o,t=setup(tmp_path,count=1)
    if fault=='config':s['config']['fee_bps']='99'
    if fault=='count':s['bar_count']=2
    if fault=='decision':o['decision_utc_ms']+=1
    if fault=='session':o['clock_session_id']='other'
    if fault=='late_receipt':o['received_utc_ms']=t+4
    if fault=='close':o['close_ms']-=1
    if fault=='previous_close':s['last_bar_close_ms']-=1
    def forbidden(*a):raise AssertionError('should not call')
    monkeypatch.setattr(reducer,'bar',forbidden)
    with pytest.raises(ValueError):call(j,s,o,t)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_generic_decision_cannot_substitute_call(tmp_path):
    j,w,s,o,t=setup(tmp_path,invoke=False);p=payload('DECISION')
    p.update(callback_started_utc_ms=t+3,callback_finished_utc_ms=t+4,callback_started_monotonic_ns=(t+3)*1000000,callback_finished_monotonic_ns=(t+4)*1000000)
    j.append('DECISION',p,utc_ms=t+5,monotonic_ns=(t+5)*1000000)
    with pytest.raises(ValueError):call(j,s,o,t)


@pytest.mark.parametrize('fault',['raise','argument','output','reentry'])
def test_reducer_faults(tmp_path,monkeypatch,fault):
    j,w,s,o,t=setup(tmp_path);original=reducer.bar
    def run(state,obs,target):
        out=original(state,obs,target)
        if fault=='raise':raise RuntimeError('callback')
        if fault=='argument':state['bar_count']=False
        if fault=='output':out['pending']['quantity']='3'
        if fault=='reentry':
            with pytest.raises(ValueError):j.close()
        return out
    monkeypatch.setattr(reducer,'bar',run)
    with pytest.raises((ValueError,RuntimeError)):call(j,s,o,t)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


@pytest.mark.parametrize('fault',['rollback','session','raise','reentry'])
def test_barrier_clock_fault(tmp_path,fault):
    j,w,s,o,t=setup(tmp_path);base=clock(t+6);calls=[]
    def sample():
        v=base();calls.append(1)
        if len(calls)==2:
            if fault=='rollback':v['monotonic_ns']=0
            if fault=='session':v['clock_session_id']='other'
            if fault=='raise':raise RuntimeError('clock')
            if fault=='reentry':
                with pytest.raises(ValueError):j.close()
        return v
    with pytest.raises((ValueError,RuntimeError)):call(j,s,o,t,clock=sample)
    assert j.snapshot()['event_count']==3 and j.snapshot()['lifecycle']=='INCOMPLETE'


def test_final_root_substitution_no_ack(tmp_path,monkeypatch):
    j,w,s,o,t=setup(tmp_path);put=w.put
    def publish(raw,**kw):
        h=put(raw,**kw)
        if json.loads(raw).get('schema_version')=='p6.bar_call_commit.v1':os.rename(w.root,w.root+'-preserved');os.mkdir(w.root)
        return h
    monkeypatch.setattr(w,'put',publish)
    with pytest.raises(ValueError):call(j,s,o,t)
    assert j.snapshot()['event_count']==3
