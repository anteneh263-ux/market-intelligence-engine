import copy,json,os
from pathlib import Path
import pytest
from test_phase6_event_validation import case,payload
from test_phase6_reservation_ledger import inputs
from test_phase6_bar_call import clock
from app.research.phase6_event_journal import EventJournal
from app.research.phase6_object_store import ObjectWriter
from app.research.phase6_validation import canonical_hash as H
from app.paper.session import diagnostic_strategy_v1 as strategy,shadow_reducer_v1 as reducer


def setup(tmp_path,mode='buy'):
    p,_=inputs(tmp_path/'registry',tmp_path/'journal');pa=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=0,targets=['0.25','0.75'])
    p.update(max_target='0.75',params_hash=H(pa),end_ms=360000)
    count=0 if mode=='buy' else 1
    if mode=='rejected_risk':p.update(quantity_step='2',fee_bps='9999',slippage_bps='9000')
    t=p['start_ms']+(count+1)*60000;m=case()['manifest'];m.update(plan_hash=H(p),params_hash=H(pa),strategy_code_hash=p['strategy_code_hash'],persisted_utc_ms=t+1)
    root=tmp_path/'journal';root.mkdir();w=ObjectWriter(str(root),owner='test',run_id=m['run_id'],max_bytes=1000000)
    j=EventJournal(w,m,utc_ms=t+1,monotonic_ns=(t+1)*1000000);j.append('BAR_ACCEPTED',payload('BAR_ACCEPTED'),utc_ms=t+2,monotonic_ns=(t+2)*1000000)
    ss=strategy.initial(pa)
    if count:ss.update(bar_count=1,last_segment_hash='9'*64)
    j.run_diagnostic(p,pa,ss,segment_seq=count+1,clock=clock(t+3))
    state=reducer.initial({k:p[k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT})
    if count:
        old=dict(close_ms=t-60000,close='100',received_utc_ms=t-59999,received_monotonic_ns=(t-59999)*1000000,clock_session_id='test',decision_utc_ms=t-59996,decision_monotonic_ns=(t-59996)*1000000)
        state=reducer.bar(state,old,'0')
        if mode=='sell':state.update(cash='99',units='9',cost_basis='900',cumulative_fees='1',equity='999',peak_equity='1000')
        if mode=='full_sell':state.update(cash='0',units='0.01',cost_basis='1000',equity='1',peak_equity='1')
        if mode=='rejected_risk':state.update(cash='600',units='4',cost_basis='400',equity='1000',peak_equity='1240')
    obs=dict(close_ms=t,close='100',received_utc_ms=t+1,received_monotonic_ns=(t+1)*1000000,clock_session_id='test',decision_utc_ms=t+4,decision_monotonic_ns=(t+4)*1000000)
    bar=j.run_bar(state,obs,clock=clock(t+6))
    q=dict(bid='100',ask='100',received_utc_ms=t+8,received_monotonic_ns=(t+8)*1000000,clock_session_id='test')
    return j,w,bar,q,t


def read(w,h):return json.loads((Path(w.root)/(h+'.blob')).read_bytes())
def call(j,q,t,**kw):return j.run_quote(q,quote_receipt_hash=kw.get('receipt','a'*64),clock=kw.get('clock',clock(max(t+9,q['received_utc_ms']+1))))
def kinds(w,r):return [read(w,h)['kind'] for h in r['event_hashes']]


def test_quote_call_is_durable_and_raw_state_unchanged(tmp_path,monkeypatch):
    j,w,b,q,t=setup(tmp_path);original=reducer.quote;calls=[];raw=copy.deepcopy(b['state'])
    def run(state,obs):
        calls.append(1);assert read(w,H(state))==state and read(w,H(obs))==obs
        assert state['pending']['intent_utc_ms']==t+7 and raw['pending']['intent_utc_ms']==t+4
        return original(state,obs)
    monkeypatch.setattr(reducer,'quote',run)
    r=call(j,q,t);assert calls==[1] and kinds(w,r)==['SIMULATED_FILL']
    assert read(w,H(raw))==raw and b['state']==raw
    assert r['state']['units']=='2.5' and r['state']['pending'] is None
    e=read(w,r['event_hashes'][0]);p=read(w,e['payload_hash'])
    assert p['price']=='100.05' and p['fee']=='0.250125' and p['quantity']=='2.5'
    assert not r['runtime_accepted'] and not r['prospective_quote_ready']
    assert read(w,r['commit_hash'])['event_hashes']==r['event_hashes'];j.close()


@pytest.mark.parametrize('mode',['sell','full_sell'])
def test_sell_and_full_basis_remainder(tmp_path,mode):
    j,w,b,q,t=setup(tmp_path,mode);r=call(j,q,t);assert kinds(w,r)==['SIMULATED_FILL']
    from fractions import Fraction
    assert Fraction(r['state']['units'])<Fraction(b['state']['units'])
    if mode=='full_sell':assert r['state']['units']=='0' and r['state']['cost_basis']=='0' and r['state']['cumulative_realized_pnl']=='-999.0005'
    j.close()


@pytest.mark.parametrize('mode,price,wanted',[('buy','1000000000000',['REJECTED']),('sell','1',['SIMULATED_FILL','STOP']),('rejected_risk','90',['REJECTED','STOP'])])
def test_zero_fill_and_risk_outcomes(tmp_path,mode,price,wanted):
    j,w,b,q,t=setup(tmp_path,mode);q.update(bid=price,ask=price);r=call(j,q,t)
    assert kinds(w,r)==wanted
    if wanted[-1]=='STOP':
        assert r['state']['reason_code']=='RISK_STOP'
        assert read(w,read(w,r['event_hashes'][-1])['payload_hash'])['pending_intent_hash'] is None
    j.close()


@pytest.mark.parametrize('clock_axis',['both','utc','mono'])
@pytest.mark.parametrize('offset',[100,101])
def test_deadline_from_barrier_not_old_callback(tmp_path,clock_axis,offset):
    j,w,b,q,t=setup(tmp_path)
    if clock_axis in ('both','utc'):q['received_utc_ms']=t+7+offset
    if clock_axis in ('both','mono'):q['received_monotonic_ns']=(t+7+offset)*1000000
    eventtime=t+110;r=call(j,q,t,clock=clock(eventtime))
    if offset==100:assert kinds(w,r)==['SIMULATED_FILL']
    else:
        assert kinds(w,r)==['STOP'] and r['state']['reason_code']=='QUOTE_TIMEOUT'
        assert r['state']['pending']['intent_utc_ms']==t+7 and r['state']['cash']==b['state']['cash']
        assert r['state']['mark_price']==b['state']['mark_price']
        assert read(w,read(w,r['event_hashes'][0])['payload_hash'])['pending_intent_hash']==b['event_hash']
    j.close()


@pytest.mark.parametrize('mode,stage',[('buy',n) for n in range(1,8)]+[('sell',n) for n in range(1,10)])
def test_every_publication_fault_is_terminal(tmp_path,monkeypatch,mode,stage):
    j,w,b,q,t=setup(tmp_path,mode)
    if mode=='sell':q.update(bid='1',ask='1')
    put=w.put;run=reducer.quote;writes=[];calls=[];before=j.snapshot()
    def publish(raw,**kw):
        h=put(raw,**kw);writes.append(h)
        if len(writes)==stage:raise RuntimeError('unknown write')
        return h
    def invoke(*args):calls.append(1);return run(*args)
    monkeypatch.setattr(w,'put',publish);monkeypatch.setattr(reducer,'quote',invoke)
    with pytest.raises(RuntimeError):call(j,q,t)
    assert len(calls)==(0 if stage<=3 else 1)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_head_hash']==before['event_head_hash']
    with pytest.raises(ValueError):call(j,q,t)


@pytest.mark.parametrize('fault',['early','utc','session','extra','receipt'])
def test_bad_quote_never_reaches_reducer(tmp_path,monkeypatch,fault):
    j,w,b,q,t=setup(tmp_path);kw={}
    if fault=='early':q['received_monotonic_ns']=(t+7)*1000000
    if fault=='utc':q['received_utc_ms']=t+6
    if fault=='session':q['clock_session_id']='other'
    if fault=='extra':q['extra']=1
    if fault=='receipt':kw['receipt']='bad'
    def forbidden(*args):raise AssertionError('must not run')
    monkeypatch.setattr(reducer,'quote',forbidden)
    with pytest.raises(ValueError):call(j,q,t,**kw)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


@pytest.mark.parametrize('fault',['raise','mutation','output','reentry'])
def test_reducer_faults_refused(tmp_path,monkeypatch,fault):
    j,w,b,q,t=setup(tmp_path);original=reducer.quote
    def run(state,obs):
        out=original(state,obs)
        if fault=='raise':raise RuntimeError('reducer')
        if fault=='mutation':state['bar_count']=True
        if fault=='output':out['cash']='0'
        if fault=='reentry':
            with pytest.raises(ValueError):j.close()
        return out
    monkeypatch.setattr(reducer,'quote',run)
    with pytest.raises((ValueError,RuntimeError)):call(j,q,t)
    assert j.snapshot()['event_count']==4


@pytest.mark.parametrize('fault',['before_receipt','session','reentry'])
def test_event_clock_failure_terminal(tmp_path,fault):
    j,w,b,q,t=setup(tmp_path)
    def sample():
        v=dict(utc_ms=t+9,monotonic_ns=(t+9)*1000000,clock_session_id='test')
        if fault=='before_receipt':v['monotonic_ns']=(t+7)*1000000
        if fault=='session':v['clock_session_id']='other'
        if fault=='reentry':
            with pytest.raises(ValueError):j.close()
        return v
    with pytest.raises(ValueError):call(j,q,t,clock=sample)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_final_root_swap_no_ack(tmp_path,monkeypatch):
    j,w,b,q,t=setup(tmp_path);put=w.put
    def publish(raw,**kw):
        h=put(raw,**kw)
        if json.loads(raw).get('schema_version')=='p6.quote_call_commit.v1':os.rename(w.root,w.root+'-preserved');os.mkdir(w.root)
        return h
    monkeypatch.setattr(w,'put',publish)
    with pytest.raises(ValueError):call(j,q,t)
    assert j.snapshot()['event_count']==4


def test_second_quote_refused_and_result_detached(tmp_path):
    j,w,b,q,t=setup(tmp_path);r=call(j,q,t);r['state'].clear()
    assert read(w,r['commit_hash'])['output_state_hash']!=H({})
    with pytest.raises(ValueError):call(j,q,t)


def test_buy_quantity_is_maximal_under_cash_and_exposure_limits(tmp_path):
    j,w,b,q,t=setup(tmp_path);q.update(bid='1000',ask='1000');r=call(j,q,t)
    assert r['state']['units']=='0.74' and kinds(w,r)==['SIMULATED_FILL'];j.close()


def test_consistent_but_underfilled_result_refused(tmp_path,monkeypatch):
    j,w,b,q,t=setup(tmp_path);original=reducer.quote
    def run(state,obs):
        smaller=copy.deepcopy(state);smaller['pending']['quantity']='1'
        return original(smaller,obs)
    monkeypatch.setattr(reducer,'quote',run)
    with pytest.raises(ValueError,match='EXECUTION_INVALID'):call(j,q,t)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_clock_cannot_mutate_frozen_valid_output(tmp_path,monkeypatch):
    j,w,b,q,t=setup(tmp_path);original=reducer.quote;aliases={}
    def run(state,obs):
        out=original(state,obs);aliases['out']=out;return out
    base=clock(t+9)
    def sample():aliases['out']['cash']='0';return base()
    monkeypatch.setattr(reducer,'quote',run)
    r=call(j,q,t,clock=sample);assert r['state']['cash']!='0';j.close()


def test_generic_intent_does_not_authorize_quote(tmp_path):
    from test_phase6_bar_call import setup as before_bar
    j,w,s,o,t=before_bar(tmp_path)
    j.append('ORDER_INTENT',payload('ORDER_INTENT'),utc_ms=t+6,monotonic_ns=(t+6)*1000000)
    q=dict(bid='100',ask='100',received_utc_ms=t+8,received_monotonic_ns=(t+8)*1000000,clock_session_id='test')
    with pytest.raises(ValueError):call(j,q,t)


def test_foreign_thread_refusal_preserves_owner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    j,w,b,q,t=setup(tmp_path)
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(ValueError):pool.submit(call,j,q,t).result()
    assert kinds(w,call(j,q,t))==['SIMULATED_FILL'];j.close()
