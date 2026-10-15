import copy
import pytest
from test_phase6_quote_call import setup,call,read
from test_phase6_bar_call import clock
from app.research.phase6_validation import canonical_hash as H


def bundle(tmp_path,timeout=False):
    j,w,b,q,t=setup(tmp_path)
    if timeout:q.update(received_utc_ms=t+108,received_monotonic_ns=(t+108)*1000000)
    call(j,q,t);previous=j.snapshot()['last_event']
    result=j.prepare_checkpoint(last_receipt_seq=5,clock=clock(t+200))
    k=read(w,result['checkpoint_hash']);c=read(w,result['commit_hash']);e=read(w,result['event_hash'])
    request=dict(manifest=j._manifest,checkpoint=k,checkpoint_commit=c,event=e,payload=read(w,e['payload_hash']),previous_event=previous,
        state=read(w,c['state_hash']),strategy_state=read(w,c['strategy_state_hash']),
        execution_commit=read(w,c['execution_commit_hash']),diagnostic_commit=read(w,c['diagnostic_commit_hash']))
    j.close();return request


def verify(q):
    from app.research.phase6_checkpoint_validation import verify_checkpoint
    return verify_checkpoint(q)


@pytest.mark.parametrize('timeout',[False,True])
def test_actual_persisted_checkpoint_bundle(tmp_path,timeout):
    q=bundle(tmp_path,timeout);before=copy.deepcopy(q);r=verify(q)
    assert r['binding_status']=='VALID' and r['checkpoint_hash']==H(q['checkpoint'])
    assert r['runtime_accepted'] is False and r['external_reference_verification']=='UPSTREAM_REQUIRED'
    assert q==before


@pytest.mark.parametrize('field',['cash','units','cost_basis','cumulative_fees','cumulative_realized_pnl','mark_price','equity','peak_equity','strategy_state_hash','accepted_prefix_hash','pending_intent_hash','previous_checkpoint_hash','journal_head_hash','journal_count','segment_seq','last_receipt_seq','stopped','run_id','session_id'])
def test_checkpoint_substitution_even_rehashed_commit(tmp_path,field):
    q=bundle(tmp_path);v=q['checkpoint'][field]
    q['checkpoint'][field]=not v if type(v) is bool else v+1 if type(v) is int else 'a'*64 if v is None or len(v)==64 else '123'
    q['checkpoint_commit']['checkpoint_hash']=H(q['checkpoint'])
    assert verify(q)['binding_status']=='INVALID'


@pytest.mark.parametrize('field',['manifest','checkpoint','checkpoint_commit','event','payload','previous_event','state','strategy_state','execution_commit','diagnostic_commit'])
def test_extra_schema_fields_refused(tmp_path,field):
    q=bundle(tmp_path);q[field]['extra']=1
    assert verify(q)['binding_status']=='INVALID'


@pytest.mark.parametrize('field',['checkpoint_hash','event_hash','state_hash','strategy_state_hash','execution_commit_hash','diagnostic_commit_hash'])
def test_commit_references_refused(tmp_path,field):
    q=bundle(tmp_path);q['checkpoint_commit'][field]='b'*64
    assert verify(q)['binding_status']=='INVALID'


@pytest.mark.parametrize('q',[None,[],{}, {'extra':1}])
def test_malformed_envelope(q):
    assert verify(q)['binding_status']=='INVALID'


def from_journal(j,w,t):
    previous=j.snapshot()['last_event'];r=j.prepare_checkpoint(last_receipt_seq=5,clock=clock(t+200));c=read(w,r['commit_hash']);e=read(w,r['event_hash'])
    q=dict(manifest=j._manifest,checkpoint=read(w,r['checkpoint_hash']),checkpoint_commit=c,event=e,
           payload=read(w,e['payload_hash']),previous_event=previous,state=read(w,c['state_hash']),
           strategy_state=read(w,c['strategy_state_hash']),execution_commit=read(w,c['execution_commit_hash']),diagnostic_commit=read(w,c['diagnostic_commit_hash']))
    j.close();return q


@pytest.mark.parametrize('mode',['warmup','zero','bar_risk','fill_risk','reject_risk','rejected'])
def test_all_other_actual_outcomes(tmp_path,mode):
    if mode in ('warmup','zero','bar_risk'):
        from test_phase6_bar_call import setup as bs,call as bc
        j,w,s,o,t=bs(tmp_path,warmup=2 if mode=='warmup' else 0,count=1 if mode=='bar_risk' else 0)
        if mode=='zero':o['close']='1000000'
        if mode=='bar_risk':s['peak_equity']='2000'
        bc(j,s,o,t)
    else:
        j,w,b,q,t=setup(tmp_path,'sell' if mode=='fill_risk' else 'rejected_risk' if mode=='reject_risk' else 'buy')
        price={'fill_risk':'1','reject_risk':'90','rejected':'1000000000000'}[mode]
        q.update(bid=price,ask=price);call(j,q,t)
    q=from_journal(j,w,t);assert verify(q)['binding_status']=='VALID'


@pytest.mark.parametrize('fault',['missing','duplicate','wrong_last','too_many','string'])
def test_quote_event_list_malformed_even_rehashed(tmp_path,fault):
    q=bundle(tmp_path);events=q['execution_commit']['event_hashes']
    q['execution_commit']['event_hashes']={'missing':[],'duplicate':events*2,'wrong_last':['b'*64],'too_many':events*3,'string':events[0]}[fault]
    q['checkpoint_commit']['execution_commit_hash']=H(q['execution_commit'])
    assert verify(q)['binding_status']=='INVALID'


def test_bool_count_even_rehashed(tmp_path):
    q=bundle(tmp_path);q['checkpoint']['segment_seq']=True
    q['checkpoint_commit']['checkpoint_hash']=H(q['checkpoint'])
    assert verify(q)['binding_status']=='INVALID'


def test_bounded_snapshot_refuses_deep_input():
    q={};node=q
    for _ in range(12):node['x']={};node=node['x']
    assert verify(q)['binding_status']=='INVALID'


def test_rehashed_risk_stop_must_not_retain_pending(tmp_path):
    j,w,b,q,t=setup(tmp_path,'sell');q.update(bid='1',ask='1');call(j,q,t)
    q=from_journal(j,w,t);s=q['state']
    s['pending']=dict(side='BUY',quantity='0.01',intent_utc_ms=s['last_action_utc_ms'],intent_monotonic_ns=s['last_action_monotonic_ns'])
    q['checkpoint']['pending_intent_hash']='a'*64;q['payload']['pending_intent_hash']='a'*64
    q['payload']['state_hash']=H(s);q['event']['payload_hash']=H(q['payload'])
    q['checkpoint']['journal_head_hash']=H(q['event'])
    q['execution_commit']['output_state_hash']=H(s)
    for field,obj in [('state_hash',s),('event_hash',q['event']),('checkpoint_hash',q['checkpoint']),('execution_commit_hash',q['execution_commit'])]:q['checkpoint_commit'][field]=H(obj)
    assert verify(q)['binding_status']=='INVALID'
