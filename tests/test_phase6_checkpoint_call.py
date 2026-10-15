import copy,json
import pytest
from test_phase6_quote_call import setup,call,read
from test_phase6_bar_call import setup as bar_setup,call as bar_call,clock
from app.research.phase6_validation import canonical_hash as H


def checkpoint(j,t,seq=5,**kw):
    return j.prepare_checkpoint(last_receipt_seq=seq,clock=kw.get('clock',clock(t+200)))


@pytest.mark.parametrize('mode,price',[('buy','100'),('buy','1000000000000'),('sell','1'),('rejected_risk','90'),('timeout','100')])
def test_quote_checkpoint_exact_bindings(tmp_path,mode,price):
    j,w,b,q,t=setup(tmp_path,'buy' if mode=='timeout' else mode)
    q.update(bid=price,ask=price)
    if mode=='timeout':q.update(received_utc_ms=t+108,received_monotonic_ns=(t+108)*1000000)
    quote=call(j,q,t);before=j.snapshot();r=checkpoint(j,t)
    k=read(w,r['checkpoint_hash']);s=quote['state'];e=read(w,r['event_hash']);p=read(w,e['payload_hash'])
    assert set(k)==set('schema_version run_id session_id segment_seq cash units cumulative_fees cumulative_realized_pnl cost_basis mark_price equity peak_equity strategy_state_hash accepted_prefix_hash last_receipt_seq pending_intent_hash journal_head_hash journal_count previous_checkpoint_hash stopped'.split())
    assert k['schema_version']=='p6.checkpoint.v1' and k['journal_head_hash']==r['event_hash'] and k['journal_count']==before['event_count']+1
    assert k['previous_checkpoint_hash']==j._manifest['prior_checkpoint_hash'] and k['session_id']==H(j._manifest)
    assert e['kind']=='CHECKPOINT_PREPARED' and e['previous_event_hash']==before['event_head_hash']
    assert read(w,p['state_hash'])==s and H(s)==p['state_hash']
    assert read(w,k['strategy_state_hash'])==j._diagnostic_context['output']['state']
    for field in 'cash units cumulative_fees cumulative_realized_pnl cost_basis mark_price equity peak_equity stopped'.split():assert k[field]==s[field]
    assert k['pending_intent_hash']==(b['event_hash'] if mode=='timeout' else None)
    commit=read(w,r['commit_hash']);assert commit['checkpoint_hash']==r['checkpoint_hash'] and commit['execution_commit_hash']==quote['commit_hash']
    assert not r['runtime_accepted'] and r['external_reference_verification']=='UPSTREAM_REQUIRED'
    assert j.snapshot()['lifecycle']=='CHECKPOINTED_LOCAL'
    with pytest.raises(ValueError):checkpoint(j,t)
    with pytest.raises(ValueError):j.append('STOP',dict(schema_version='p6.payload.stop.v1',reason_code='OPERATOR_STOP',pending_intent_hash=None),utc_ms=t+201,monotonic_ns=(t+201)*1000000)
    j.close();assert j.snapshot()['lifecycle']=='CLOSED'


@pytest.mark.parametrize('mode',['warmup','zero','risk'])
def test_bar_only_checkpoint(tmp_path,mode):
    j,w,s,o,t=bar_setup(tmp_path,warmup=2 if mode=='warmup' else 0,count=1 if mode=='risk' else 0)
    if mode=='zero':o['close']='1000000'
    if mode=='risk':s['peak_equity']='2000'
    result=bar_call(j,s,o,t);r=checkpoint(j,t);k=read(w,r['checkpoint_hash'])
    assert k['pending_intent_hash'] is None and k['stopped']==result['state']['stopped']
    assert read(w,read(w,r['commit_hash'])['state_hash'])==result['state'];j.close()


def test_pending_order_refused(tmp_path):
    j,w,b,q,t=setup(tmp_path)
    with pytest.raises(ValueError):checkpoint(j,t)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


@pytest.mark.parametrize('stage',range(1,7))
def test_checkpoint_write_failure_no_ack_or_retry(tmp_path,monkeypatch,stage):
    j,w,b,q,t=setup(tmp_path);call(j,q,t);before=j.snapshot();put=w.put;writes=[]
    def fail(raw,**kw):
        h=put(raw,**kw);writes.append(h)
        if len(writes)==stage:raise RuntimeError('uncertain publication')
        return h
    monkeypatch.setattr(w,'put',fail)
    with pytest.raises(RuntimeError):checkpoint(j,t)
    after=j.snapshot();assert after['lifecycle']=='INCOMPLETE' and after['event_head_hash']==before['event_head_hash'] and after['event_count']==before['event_count']
    with pytest.raises(ValueError):checkpoint(j,t)


@pytest.mark.parametrize('seq',[True,0,-1,2**63,'5',None])
def test_bad_receipt_sequence(tmp_path,seq):
    j,w,b,q,t=setup(tmp_path);call(j,q,t);before=j.snapshot()
    with pytest.raises(ValueError):checkpoint(j,t,seq)
    assert j.snapshot()['objects']==before['objects'] and j.snapshot()['lifecycle']=='INCOMPLETE'


def test_mutated_return_cannot_replace_state(tmp_path):
    j,w,b,q,t=setup(tmp_path);r=call(j,q,t);expected=copy.deepcopy(r['state']);r['state']['cash']='0'
    cp=checkpoint(j,t);assert read(w,read(w,cp['commit_hash'])['state_hash'])==expected;j.close()


def test_appended_event_cannot_replace_execution_head(tmp_path):
    j,w,b,q,t=setup(tmp_path);call(j,q,t)
    j.append('STOP',dict(schema_version='p6.payload.stop.v1',reason_code='OPERATOR_STOP',pending_intent_hash=None),utc_ms=t+100,monotonic_ns=(t+100)*1000000)
    with pytest.raises(ValueError):checkpoint(j,t)


@pytest.mark.parametrize('fault',['early','session','reentry'])
def test_clock_faults_terminal(tmp_path,fault):
    j,w,b,q,t=setup(tmp_path);call(j,q,t);before=j.snapshot()
    def bad():
        if fault=='reentry':
            try:checkpoint(j,t)
            except ValueError:pass
        return dict(utc_ms=t if fault=='early' else t+200,monotonic_ns=(t+200)*1000000,clock_session_id='other' if fault=='session' else 'test')
    with pytest.raises(ValueError):checkpoint(j,t,clock=bad)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_head_hash']==before['event_head_hash']


def test_final_root_substitution_refused(tmp_path,monkeypatch):
    j,w,b,q,t=setup(tmp_path);call(j,q,t);before=j.snapshot();put=w.put;count=0
    from pathlib import Path
    def swap(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==6:
            root=Path(w.root);root.rename(root.with_name('moved'));root.mkdir()
        return h
    monkeypatch.setattr(w,'put',swap)
    with pytest.raises(Exception):checkpoint(j,t)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_head_hash']==before['event_head_hash']


def test_close_after_checkpoint_checks_inventory(tmp_path,monkeypatch):
    j,w,b,q,t=setup(tmp_path);call(j,q,t);checkpoint(j,t)
    import app.research.phase6_event_journal as module
    def fail(*a,**kw):raise RuntimeError('inventory mismatch')
    monkeypatch.setattr(module,'inspect_objects',fail)
    with pytest.raises(RuntimeError):j.close()
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_foreign_thread_refused_without_poison(tmp_path):
    j,w,b,q,t=setup(tmp_path);call(j,q,t);errors=[]
    import threading
    def attempt():
        try:checkpoint(j,t)
        except ValueError as e:errors.append(str(e))
    thread=threading.Thread(target=attempt);thread.start();thread.join()
    assert errors and j.snapshot()['lifecycle']=='OPEN'
    checkpoint(j,t);j.close()


def test_receipt_sequence_below_segment_refused(tmp_path):
    j,w,b,q,t=setup(tmp_path,'sell');call(j,q,t)
    with pytest.raises(ValueError):checkpoint(j,t,1)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'
