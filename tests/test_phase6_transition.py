import copy
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
import pytest
from app.paper.session import shadow_reducer_v1 as reducer
from app.research.phase6_object_store import ObjectWriter, inspect_objects
from app.research.phase6_transition import TransitionSession

H='1'*64

def config():
    return dict(initial_cash='1000',quantity_step='1',tick_size='1',max_target='1',fee_bps='100',slippage_bps='0',max_drawdown='0.5',warmup_bars=0,max_decision_delay_ms=10,max_quote_delay_ms=10)

def bar(price='100',tick=100):
    return dict(close_ms=tick,close=price,received_utc_ms=tick,received_monotonic_ns=tick*1000000,clock_session_id='test',decision_utc_ms=tick+1,decision_monotonic_ns=(tick+1)*1000000)

def quote(price='100',tick=102):
    return dict(bid=price,ask=price,received_utc_ms=tick,received_monotonic_ns=tick*1000000,clock_session_id='test')

def setup(root):
    w=ObjectWriter(str(root),owner='synthetic',run_id=H,max_bytes=1000000)
    return w,TransitionSession(w,config(),context_hash='2'*64)

def blob(root,h):return json.loads((root/(h+'.blob')).read_bytes())

def test_actual_durable_chain_and_equivalence(tmp_path):
    w,s=setup(tmp_path);pure=reducer.initial(config());previous=s.snapshot()['genesis_hash']
    steps=[('BAR',dict(observation=bar(),target='1')),('QUOTE',dict(observation=quote())),('BAR',dict(observation=bar('110',200),target='0')),('QUOTE',dict(observation=quote('110',202)))]
    for seq,(kind,payload) in enumerate(steps,1):
        old=s.snapshot();h=s.apply(kind,payload);commit=blob(tmp_path,h);m=blob(tmp_path,commit['manifest_hash'])
        assert commit['transition_seq']==m['transition_seq']==seq
        assert m['prior_commit_hash']==previous and m['prior_state_hash']==old['state_hash']
        assert blob(tmp_path,m['input_hash'])['payload']==payload
        pure=reducer.bar(pure,payload['observation'],payload['target']) if kind=='BAR' else reducer.quote(pure,payload['observation'])
        assert s.snapshot()['state']==pure==blob(tmp_path,commit['state_hash']);previous=h
    assert pure['cash']=='1071.1'
    snap=s.snapshot();s.close()
    assert inspect_objects(str(tmp_path),header=w.header,objects=snap['objects'])['technical_verification']=='LOCAL_ONLY'
    assert not snap['runtime_accepted'] and snap['transition_count']==4

def test_manifest_persisted_before_reducer(tmp_path,monkeypatch):
    _,s=setup(tmp_path);original=reducer.bar;calls=[]
    def check(state,observation,target):
        documents=[json.loads(p.read_bytes()) for p in tmp_path.glob('*.blob')]
        manifests=[d for d in documents if d.get('schema_version')=='p6.transition_manifest.v1']
        assert len(manifests)==1
        assert manifests[0]['prior_state_hash']==s.snapshot()['state_hash']
        calls.append(1);return original(state,observation,target)
    monkeypatch.setattr(reducer,'bar',check)
    s.apply('BAR',dict(observation=bar(),target='1'));s.close();assert calls==[1]

@pytest.mark.parametrize('nth',[1,2,3,4])
@pytest.mark.parametrize('after',[False,True])
def test_every_write_window_terminal_no_retry(tmp_path,monkeypatch,nth,after):
    w,s=setup(tmp_path);before=s.snapshot();original=w.put;count=0;calls=0;reduce=reducer.bar
    def reducer_count(*args):
        nonlocal calls
        calls+=1;return reduce(*args)
    def fault(raw, **kwargs):
        nonlocal count
        count+=1
        if count==nth and not after:raise OSError('synthetic before publication')
        result=original(raw, **kwargs)
        if count==nth and after:raise OSError('synthetic after publication')
        return result
    monkeypatch.setattr(w,'put',fault);monkeypatch.setattr(reducer,'bar',reducer_count)
    with pytest.raises(OSError):s.apply('BAR',dict(observation=bar(),target='1'))
    snap=s.snapshot();assert snap['lifecycle']=='INCOMPLETE' and snap['state']==before['state'] and snap['transition_count']==0
    assert calls==(1 if nth>=3 else 0)
    with pytest.raises(ValueError,match='SESSION_STOPPED'):s.apply('BAR',dict(observation=bar(),target='1'))
    with pytest.raises(ValueError,match='WRITER_STOPPED'):original(b'x')
    assert list(tmp_path.iterdir())

@pytest.mark.parametrize('nth',[1,2])
def test_initialization_failure_closes_writer(tmp_path,monkeypatch,nth):
    w=ObjectWriter(str(tmp_path),owner='synthetic',run_id=H,max_bytes=1000000);original=w.put;count=0
    def fault(raw, **kwargs):
        nonlocal count
        count+=1
        if count==nth:raise OSError('synthetic constructor failure')
        return original(raw, **kwargs)
    monkeypatch.setattr(w,'put',fault)
    with pytest.raises(OSError):TransitionSession(w,config(),context_hash='2'*64)
    with pytest.raises(ValueError,match='WRITER_STOPPED'):original(b'x')

def test_reducer_failure_never_retried(tmp_path,monkeypatch):
    _,s=setup(tmp_path);calls=[]
    def fail(*args):calls.append(1);raise KeyboardInterrupt()
    monkeypatch.setattr(reducer,'bar',fail)
    with pytest.raises(KeyboardInterrupt):s.apply('BAR',dict(observation=bar(),target='1'))
    with pytest.raises(ValueError,match='SESSION_STOPPED'):s.apply('BAR',dict(observation=bar(),target='1'))
    assert calls==[1] and s.snapshot()['lifecycle']=='INCOMPLETE'

def test_copies_isolate_configuration_and_snapshots(tmp_path):
    w=ObjectWriter(str(tmp_path),owner='synthetic',run_id=H,max_bytes=1000000);c=config();s=TransitionSession(w,c,context_hash='2'*64)
    c['initial_cash']='999';snap=s.snapshot();snap['state']['cash']='0';snap['objects'].clear();h=w.header;h['run_id']='0'*64
    assert s.snapshot()['state']['cash']=='1000' and s.snapshot()['objects'] and w.header['run_id']==H
    p=dict(observation=bar(),target='1');s.apply('BAR',p);p['observation']['close']='1'
    assert s.snapshot()['state']['mark_price']=='100';s.close()

def test_stop_and_close_terminal(tmp_path):
    _,s=setup(tmp_path);s.apply('STOP',{'reason':'OPERATOR_STOP'})
    assert s.snapshot()['lifecycle']=='STOPPED'
    with pytest.raises(ValueError,match='SESSION_STOPPED'):s.apply('STOP',{'reason':'OPERATOR_STOP'})
    s.close();assert s.snapshot()['lifecycle']=='STOPPED'

@pytest.mark.parametrize('kind,payload',[('X',{}),('BAR',{}),('STOP',{'reason':'RISK_STOP'}),('QUOTE',{'observation':{}})])
def test_invalid_transition_terminal(tmp_path,kind,payload):
    _,s=setup(tmp_path)
    with pytest.raises(ValueError):s.apply(kind,payload)
    assert s.snapshot()['lifecycle']=='INCOMPLETE'

def test_foreign_thread_cannot_apply_or_close(tmp_path):
    _,s=setup(tmp_path)
    def run():
        for action in (lambda:s.apply('STOP',{'reason':'OPERATOR_STOP'}),s.close):
            with pytest.raises(ValueError,match='SESSION_STOPPED'):action()
    with ThreadPoolExecutor(max_workers=1) as pool:pool.submit(run).result()
    assert s.snapshot()['lifecycle']=='OPEN';s.close()

def test_reentrant_reducer_poison_even_if_exception_caught(tmp_path,monkeypatch):
    _,s=setup(tmp_path);original=reducer.bar
    def nested(*args):
        with pytest.raises(ValueError,match='SESSION_STOPPED'):s.apply('STOP',{'reason':'OPERATOR_STOP'})
        return original(*args)
    monkeypatch.setattr(reducer,'bar',nested)
    with pytest.raises(ValueError,match='SESSION_STOPPED'):s.apply('BAR',dict(observation=bar(),target='1'))
    assert s.snapshot()['lifecycle']=='INCOMPLETE' and s.snapshot()['transition_count']==0


def test_duplicate_and_used_writer_claim_refused(tmp_path):
    w,s=setup(tmp_path)
    with pytest.raises(ValueError,match='WRITER_OWNERSHIP'):TransitionSession(w,config(),context_hash='2'*64)
    with pytest.raises(ValueError,match='WRITER_OWNERSHIP'):w.put(b'bypass')
    with pytest.raises(ValueError,match='WRITER_OWNERSHIP'):w.close()
    assert s.snapshot()['lifecycle']=='OPEN';s.close()
    other=tmp_path/'other';other.mkdir();w=ObjectWriter(str(other),owner='synthetic',run_id=H,max_bytes=1000000)
    w.put(b'previous')
    with pytest.raises(ValueError,match='WRITER_OWNERSHIP'):TransitionSession(w,config(),context_hash='2'*64)
    w.close()

def test_forked_session_cannot_execute_or_close_parent(tmp_path):
    _,s=setup(tmp_path);pid=os.fork()
    if pid==0:
        for action in (lambda:s.apply('STOP',{'reason':'OPERATOR_STOP'}),s.close):
            try:action()
            except ValueError as e:
                if str(e)!='SESSION_STOPPED':os._exit(2)
            else:os._exit(3)
        os._exit(0)
    _,status=os.waitpid(pid,0);assert os.waitstatus_to_exitcode(status)==0
    s.apply('BAR',dict(observation=bar(),target='1'));s.close()
    assert s.snapshot()['transition_count']==1

def test_bad_context_leaves_unclaimed_writer_usable(tmp_path):
    w=ObjectWriter(str(tmp_path),owner='synthetic',run_id=H,max_bytes=1000000)
    with pytest.raises(ValueError,match='INPUT_INVALID'):TransitionSession(w,config(),context_hash='bad')
    w.put(b'untouched');w.close()

def test_bad_config_leaves_unclaimed_writer_usable(tmp_path):
    w=ObjectWriter(str(tmp_path),owner='synthetic',run_id=H,max_bytes=1000000)
    with pytest.raises(ValueError,match='INPUT_INVALID'):TransitionSession(w,{},context_hash='2'*64)
    w.put(b'untouched');w.close()

def test_wrong_token_cannot_publish_or_close(tmp_path):
    w=ObjectWriter(str(tmp_path),owner='synthetic',run_id=H,max_bytes=1000000);token=w.claim()
    with pytest.raises(ValueError,match='WRITER_OWNERSHIP'):w.put(b'blocked',claim=object())
    with pytest.raises(ValueError,match='WRITER_OWNERSHIP'):w.close(claim=object())
    h=w.put(b'owned',claim=token);w.close(claim=token)
    assert inspect_objects(str(tmp_path),header=w.header,objects={h:5})['object_count']==1


def test_cleanup_failure_preserves_original_exception(tmp_path,monkeypatch):
    w,s=setup(tmp_path);original=w.close
    def cleanup(**kwargs):raise OSError('cleanup failed')
    def fail(*args):raise ValueError('CLOCK_INVALID')
    monkeypatch.setattr(w,'close',cleanup);monkeypatch.setattr(reducer,'bar',fail)
    with pytest.raises(ValueError,match='CLOCK_INVALID'):s.apply('BAR',dict(observation=bar(),target='1'))
    assert s.snapshot()['lifecycle']=='INCOMPLETE'
    monkeypatch.setattr(w,'close',original);s.close()

def test_explicit_close_failure_is_terminal(tmp_path,monkeypatch):
    w,s=setup(tmp_path);original=w.close
    def cleanup(**kwargs):raise OSError('cleanup failed')
    monkeypatch.setattr(w,'close',cleanup)
    with pytest.raises(OSError,match='cleanup failed'):s.close()
    assert s.snapshot()['lifecycle']=='INCOMPLETE'
    with pytest.raises(ValueError,match='SESSION_STOPPED'):s.apply('STOP',{'reason':'OPERATOR_STOP'})
    monkeypatch.setattr(w,'close',original);s.close()
