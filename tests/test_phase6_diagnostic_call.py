import copy,json,os
from pathlib import Path
import pytest
from test_phase6_event_validation import case,payload
from test_phase6_reservation_ledger import inputs
from app.research.phase6_event_journal import EventJournal
from app.research.phase6_object_store import ObjectWriter
from app.paper.session import diagnostic_strategy_v1 as strategy
from app.research.phase6_validation import canonical_hash as H


def setup(tmp_path,warmup=0,count=0):
    p,_=inputs(tmp_path/'registry',tmp_path/'journal')
    params=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=warmup,targets=['0.25','0.75'])
    p.update(warmup_bars=warmup,max_target='0.75',end_ms=p['start_ms']+(warmup+3)*60000,params_hash=H(params))
    m=case()['manifest'];m.update(plan_hash=H(p),params_hash=H(params),strategy_code_hash=p['strategy_code_hash'])
    root=tmp_path/'journal';root.mkdir()
    w=ObjectWriter(str(root),owner='test',run_id=m['run_id'],max_bytes=1000000)
    j=EventJournal(w,m,utc_ms=20,monotonic_ns=200)
    j.append('BAR_ACCEPTED',payload('BAR_ACCEPTED'),utc_ms=21,monotonic_ns=210)
    state=strategy.initial(params)
    if count:state.update(bar_count=count,last_segment_hash='9'*64)
    return j,w,p,params,state,m


def clock():
    times=iter([(22,220),(23,230),(24,240)])
    def sample():
        u,m=next(times);return dict(utc_ms=u,monotonic_ns=m,clock_session_id='test')
    return sample


def call(j,p,params,state,**kwargs):
    return j.run_diagnostic(p,params,state,segment_seq=state['bar_count']+1,clock=kwargs.get('clock',clock()))


def read(w,h):return json.loads((Path(w.root)/(h+'.blob')).read_bytes())


def test_exact_causal_call_and_persisted_commit(tmp_path,monkeypatch):
    j,w,p,params,state,m=setup(tmp_path);original=strategy.advance;calls=[]
    def advance(s,pa,**kw):
        calls.append(1)
        for value in (p,params,state,m):assert read(w,H(value))==value
        descriptors=[json.loads(x.read_bytes()) for x in Path(w.root).glob('*.blob')]
        assert len([x for x in descriptors if x.get('schema_version')=='p6.diagnostic_invocation.v1'])==1
        return original(s,pa,**kw)
    monkeypatch.setattr(strategy,'advance',advance)
    result=call(j,p,params,state)
    assert calls==[1] and result['target']=='0.25' and result['state']['bar_count']==1
    assert not result['runtime_accepted'] and result['external_reference_verification']=='UPSTREAM_REQUIRED'
    commit=read(w,result['commit_hash'])
    assert commit==dict(schema_version='p6.diagnostic_call_commit.v1',invocation_hash=result['invocation_hash'],event_hash=result['event_hash'],output_state_hash=H(result['state']))
    event=read(w,result['event_hash']);ep=read(w,event['payload_hash'])
    assert read(w,ep['strategy_input_hash'])==state and read(w,ep['strategy_output_hash'])==result['state']
    assert ep['callback_started_utc_ms']==22 and ep['callback_finished_utc_ms']==23
    assert j.snapshot()['event_count']==3;j.close()


@pytest.mark.parametrize('warmup,count,target',[(1,0,None),(2,1,None),(1,1,'0.25'),(1,2,'0.75')])
def test_warmup_and_state_continuity(tmp_path,warmup,count,target):
    j,w,p,pa,s,m=setup(tmp_path,warmup,count);result=call(j,p,pa,s)
    assert result['target']==target and result['state']['bar_count']==count+1;j.close()


@pytest.mark.parametrize('stage',range(1,9))
def test_every_unknown_publication_is_terminal(tmp_path,monkeypatch,stage):
    j,w,p,pa,s,m=setup(tmp_path);put=w.put;advance=strategy.advance;calls=[];writes=[]
    def publish(raw,**kw):
        h=put(raw,**kw);writes.append(h)
        if len(writes)==stage:raise RuntimeError('unknown write')
        return h
    def run(*args,**kw):calls.append(1);return advance(*args,**kw)
    monkeypatch.setattr(w,'put',publish);monkeypatch.setattr(strategy,'advance',run)
    before=j.snapshot()
    with pytest.raises(RuntimeError,match='unknown write'):call(j,p,pa,s)
    assert len(calls)==(0 if stage<=4 else 1)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_head_hash']==before['event_head_hash']
    assert j.snapshot()['event_count']==2
    with pytest.raises(ValueError):call(j,p,pa,s)


@pytest.mark.parametrize('fault',['raise','mutate_state','mutate_params','extra','wrong_target','wrong_state'])
def test_callback_failure_or_mutation_stops(tmp_path,monkeypatch,fault):
    j,w,p,pa,s,m=setup(tmp_path);original=strategy.advance;called=[]
    def run(state,params,**kw):
        called.append(1);out=original(state,params,**kw)
        if fault=='raise':raise RuntimeError('callback')
        if fault=='mutate_state':state['bar_count']=99
        if fault=='mutate_params':params['targets'].clear()
        if fault=='extra':out['extra']=1
        if fault=='wrong_target':out['target']='0.5'
        if fault=='wrong_state':out['state']['bar_count']=99
        return out
    monkeypatch.setattr(strategy,'advance',run)
    with pytest.raises((ValueError,RuntimeError)):call(j,p,pa,s)
    assert called==[1] and j.snapshot()['lifecycle']=='INCOMPLETE' and s['bar_count']==0


@pytest.mark.parametrize('fault',['plan','params','state','limit','seq','history'])
def test_context_refused_before_callback(tmp_path,monkeypatch,fault):
    j,w,p,pa,s,m=setup(tmp_path)
    if fault=='plan':p['owner']='other'
    if fault=='params':pa['warmup_bars']=1
    if fault=='state':s['params_hash']='f'*64
    if fault=='limit':p['max_target']='0.5'
    if fault=='seq':s['bar_count']=2**63-1;s['last_segment_hash']='8'*64
    if fault=='history':j.append('STOP',payload('STOP'),utc_ms=22,monotonic_ns=220)
    def forbidden(*a,**kw):raise AssertionError('callback should not run')
    monkeypatch.setattr(strategy,'advance',forbidden)
    with pytest.raises(ValueError):call(j,p,pa,s)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


@pytest.mark.parametrize('stage',[1,2,3])
def test_clock_reentry_terminal(tmp_path,stage):
    j,w,p,pa,s,m=setup(tmp_path);base=clock();calls=[]
    def sample():
        calls.append(1)
        if len(calls)==stage:
            with pytest.raises(ValueError):j.close()
        return base()
    with pytest.raises(ValueError):call(j,p,pa,s,clock=sample)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


@pytest.mark.parametrize('fault',['rollback','session','bool','extra'])
def test_invalid_clock_samples(tmp_path,fault):
    j,w,p,pa,s,m=setup(tmp_path);base=clock();calls=[]
    def sample():
        v=base();calls.append(1)
        if len(calls)==2:
            if fault=='rollback':v['monotonic_ns']=1
            if fault=='session':v['clock_session_id']='other'
            if fault=='bool':v['utc_ms']=True
            if fault=='extra':v['extra']=1
        return v
    with pytest.raises(ValueError):call(j,p,pa,s,clock=sample)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_result_detached_and_second_call_refused(tmp_path):
    j,w,p,pa,s,m=setup(tmp_path);r=call(j,p,pa,s);r['state'].clear()
    assert read(w,r['commit_hash'])['output_state_hash']!=H({})
    with pytest.raises(ValueError):call(j,p,pa,s)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_final_commit_root_swap_cannot_ack(tmp_path,monkeypatch):
    j,w,p,pa,s,m=setup(tmp_path);original=w.put
    def put(raw,**kw):
        h=original(raw,**kw)
        if json.loads(raw).get('schema_version')=='p6.diagnostic_call_commit.v1':
            os.rename(w.root,w.root+'-preserved');os.mkdir(w.root)
        return h
    monkeypatch.setattr(w,'put',put)
    with pytest.raises(ValueError):call(j,p,pa,s)
    assert j.snapshot()['event_count']==2 and j.snapshot()['lifecycle']=='INCOMPLETE'


def test_boolean_input_mutation_is_not_equal_integer(tmp_path,monkeypatch):
    j,w,p,pa,s,m=setup(tmp_path);original=strategy.advance
    def run(state,params,**kw):
        result=original(state,params,**kw);state['bar_count']=False;return result
    monkeypatch.setattr(strategy,'advance',run)
    with pytest.raises(ValueError,match='STRATEGY_INVALID'):call(j,p,pa,s)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_callback_reentry_cannot_ack(tmp_path,monkeypatch):
    j,w,p,pa,s,m=setup(tmp_path);original=strategy.advance
    def run(*args,**kw):
        result=original(*args,**kw)
        with pytest.raises(ValueError):j.append('STOP',payload('STOP'),utc_ms=23,monotonic_ns=230)
        return result
    monkeypatch.setattr(strategy,'advance',run)
    with pytest.raises(ValueError):call(j,p,pa,s)
    assert j.snapshot()['event_count']==2 and j.snapshot()['lifecycle']=='INCOMPLETE'


def test_output_bool_counter_is_refused(tmp_path,monkeypatch):
    j,w,p,pa,s,m=setup(tmp_path);original=strategy.advance
    def run(*args,**kw):
        result=original(*args,**kw);result['state']['bar_count']=True;return result
    monkeypatch.setattr(strategy,'advance',run)
    with pytest.raises(ValueError):call(j,p,pa,s)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_foreign_thread_cannot_invoke_or_poison(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    j,w,p,pa,s,m=setup(tmp_path)
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(ValueError):pool.submit(call,j,p,pa,s).result()
    assert call(j,p,pa,s)['target']=='0.25';j.close()


@pytest.mark.parametrize('fault',['output','argument'])
def test_finished_clock_cannot_repair_callback_return(tmp_path,monkeypatch,fault):
    j,w,p,pa,s,m=setup(tmp_path);original=strategy.advance;aliases={}
    def run(state,params,**kw):
        out=original(state,params,**kw)
        if fault=='output':out['target']='0.5';aliases['out']=out
        else:state['bar_count']=99;aliases['state']=state
        return out
    base=clock();calls=[]
    def sample():
        calls.append(1)
        if len(calls)==2:
            if fault=='output':aliases['out']['target']='0.25'
            else:aliases['state']['bar_count']=0
        return base()
    monkeypatch.setattr(strategy,'advance',run)
    with pytest.raises(ValueError,match='STRATEGY_INVALID'):call(j,p,pa,s,clock=sample)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'
