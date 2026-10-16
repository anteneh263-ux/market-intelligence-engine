import copy
import pytest
from test_phase6_intake_binding import setup,bar,read
from test_phase6_bar_call import clock
from app.paper.session import diagnostic_strategy_v1 as strategy,shadow_reducer_v1 as reducer
from app.research.phase6_validation import canonical_hash as H
from app.research.phase6_event_journal import EventJournal
from app.research.phase6_object_store import ObjectWriter


def journal(tmp,q,m):
    tmp.mkdir();w=ObjectWriter(str(tmp),owner='test',run_id=m['run_id'],max_bytes=1000000);t=m['persisted_utc_ms']
    j=EventJournal(w,m,utc_ms=t,monotonic_ns=t*1000000);s=q['segments'][-1]
    j.append('BAR_ACCEPTED',dict(schema_version='p6.payload.bar_accepted.v1',segment_hash=H(s),receipt_hash=s['accepted_receipt_hash'],bar_hash=s['bar_hash'],accepted_prefix_hash=H(q['prefix'])),utc_ms=t+1,monotonic_ns=(t+1)*1000000)
    j.bind_intake(q);return j,w


def genesis(tmp,warmup=1):
    seed=tmp/'seed';seed.mkdir();j,w,q,pa=setup(seed,warmup=warmup);m=copy.deepcopy(j._manifest);j.close()
    state=reducer.initial({k:q['plan'][k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT});ss=strategy.initial(pa)
    g=dict(schema_version='p6.genesis.v1',run_id=m['run_id'],plan_hash=H(q['plan']),strategy_state_hash=H(ss),**{k:state[k] for k in 'cash units cost_basis cumulative_fees cumulative_realized_pnl equity peak_equity'.split()})
    m['prior_checkpoint_hash']=H(g);j,w=journal(tmp/'first',q,m)
    return j,w,q,pa,state,ss,g


def execute(j,w,q,pa,state,ss,t):
    d=j.run_diagnostic(q['plan'],pa,ss,segment_seq=len(q['segments']),clock=clock(t+3))
    e=q['receipts'][-1];b=q['payloads'][q['segments'][-1]['bar_hash']]
    o=dict(close_ms=b['close_ms'],close=b['close'],received_utc_ms=e['received_utc_ms'],received_monotonic_ns=e['received_monotonic_ns'],clock_session_id=e['clock_session_id'],decision_utc_ms=t+4,decision_monotonic_ns=(t+4)*1000000)
    return d,j.run_bar(state,o,clock=clock(t+6))


def second(tmp,quote=False):
    j,w,q,pa,state,ss,g=genesis(tmp,warmup=0 if quote else 1)
    # Existing local call path supplies an actual previous checkpoint for verification.
    execute(j,w,q,pa,state,ss,120010)
    if quote:
        from test_phase6_quote_receipt_binding import extend,run
        q=extend(q);j.bind_quote_receipts(q);run(j,q)
    previous=j.snapshot()['last_event'];r=j.prepare_checkpoint(last_receipt_seq=len(q['receipts']),clock=clock(120030))
    c=read(w,r['commit_hash']);e=read(w,r['event_hash']);prior=dict(manifest=j._manifest,checkpoint=read(w,r['checkpoint_hash']),checkpoint_commit=c,event=e,payload=read(w,e['payload_hash']),previous_event=previous,state=read(w,c['state_hash']),strategy_state=read(w,c['strategy_state_hash']),execution_commit=read(w,c['execution_commit_hash']),diagnostic_commit=read(w,c['diagnostic_commit_hash']));j.close()
    q=copy.deepcopy(q);b=copy.deepcopy(next(iter(q['payloads'].values())));b.update(open_ms=120000,close_ms=180000);q['payloads'][H(b)]=b
    probe=copy.deepcopy(next(iter(q['clock_probes'].values())));probe.update(measured_utc_ms=179000,measured_monotonic_ns=179000000000,valid_until_monotonic_ns=181000000000);q['clock_probes'][H(probe)]=probe
    rec=copy.deepcopy(q['receipts'][-1]);rec.update(kind='BAR_FINAL',receipt_seq=len(q['receipts'])+1,previous_receipt_hash=H(q['receipts'][-1]),provider_event_ms=180000,received_utc_ms=180010,received_monotonic_ns=180010000000,normalized_payload_hash=H(b),clock_probe_hash=H(probe));q['receipts'].append(rec)
    seg=copy.deepcopy(q['segments'][-1]);seg.update(segment_seq=2,start_ms=120000,end_ms=180000,bar_hash=H(b),accepted_receipt_hash=H(rec),previous_segment_hash=H(q['segments'][-1]));q['segments'].append(seg);q['prefix']['segment_hashes'].append(H(seg))
    m=copy.deepcopy(prior['manifest']);m.update(segment_id=H(seg),prior_checkpoint_hash=H(prior['checkpoint']),persisted_utc_ms=180011)
    j,w=journal(tmp/'second',q,m)
    return j,w,q,pa,prior['state'],prior['strategy_state'],prior


def bind(j,prior,pa,s,ss):return j.bind_prior_state(prior,params=pa,state=s,strategy_state=ss)


@pytest.mark.parametrize('mode',['genesis','checkpoint'])
def test_actual_prior_binding_pins_inputs(tmp_path,mode):
    j,w,q,pa,s,ss,p=(genesis if mode=='genesis' else second)(tmp_path);b=bind(j,p,pa,s,ss)
    d,out=execute(j,w,q,pa,s,ss,120010 if mode=='genesis' else 180010)
    inv=read(w,d['invocation_hash']);assert inv['schema_version']=='p6.diagnostic_invocation.v3' and inv['prior_state_binding_hash']==b['commit_hash']
    assert read(w,b['validation_hash'])['binding_status']=='VALID' and not b['runtime_accepted'];j.close()


@pytest.mark.parametrize('field',['cash','units','peak_equity','config','last_action_utc_ms'])
def test_different_prior_state_refused(tmp_path,field):
    j,w,q,pa,s,ss,p=second(tmp_path);s=copy.deepcopy(s)
    if field=='config':s[field]['fee_bps']='11'
    elif field=='last_action_utc_ms':s[field]+=1
    else:s[field]='999'
    before=j.snapshot()
    with pytest.raises(ValueError):bind(j,p,pa,s,ss)
    assert j.snapshot()['objects']==before['objects'] and j.snapshot()['lifecycle']=='INCOMPLETE'


@pytest.mark.parametrize('field',['bar_count','last_segment_hash','params_hash'])
def test_different_strategy_state_refused(tmp_path,field):
    j,w,q,pa,s,ss,p=second(tmp_path);ss=copy.deepcopy(ss);ss[field]=3 if field=='bar_count' else 'a'*64
    with pytest.raises(ValueError):bind(j,p,pa,s,ss)


@pytest.mark.parametrize('stage',[1,2,3])
def test_prior_binding_unknown_write_terminal(tmp_path,monkeypatch,stage):
    j,w,q,pa,s,ss,p=genesis(tmp_path);put=w.put;count=0;before=j.snapshot()
    def fail(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==stage:raise RuntimeError('unknown write')
        return h
    monkeypatch.setattr(w,'put',fail)
    with pytest.raises(RuntimeError):bind(j,p,pa,s,ss)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_head_hash']==before['event_head_hash']


def test_caller_mutation_cannot_change_bound_state(tmp_path):
    j,w,q,pa,s,ss,p=genesis(tmp_path);bind(j,p,pa,s,ss);original=copy.deepcopy(s);s['cash']='0'
    d=j.run_diagnostic(q['plan'],pa,ss,segment_seq=1,clock=clock(120013))
    with pytest.raises(ValueError):j.run_bar(s,bar(q),clock=clock(120016))


def test_bound_strategy_substitution_before_callback(tmp_path,monkeypatch):
    j,w,q,pa,s,ss,p=genesis(tmp_path);bind(j,p,pa,s,ss);ss['bar_count']=1
    monkeypatch.setattr(strategy,'advance',lambda *a,**kw:pytest.fail('callback invoked'))
    with pytest.raises(ValueError):j.run_diagnostic(q['plan'],pa,ss,segment_seq=1,clock=clock(120013))


def test_genesis_wrong_hash_refused(tmp_path):
    j,w,q,pa,s,ss,p=genesis(tmp_path);p['cash']='999'
    with pytest.raises(ValueError):bind(j,p,pa,s,ss)


def test_rebind_refused(tmp_path):
    j,w,q,pa,s,ss,p=genesis(tmp_path);bind(j,p,pa,s,ss)
    with pytest.raises(ValueError):bind(j,p,pa,s,ss)


@pytest.mark.parametrize('field',['engine_version','engine_code_hash','strategy_code_hash','params_hash','plan_hash','prior_checkpoint_hash'])
def test_current_manifest_identity_mismatch(tmp_path,field):
    from app.research.phase6_prior_state_validation import verify_prior_state
    j,w,q,pa,s,ss,p=second(tmp_path);m=copy.deepcopy(j._manifest);m[field]='different' if field=='engine_version' else 'a'*64
    r=verify_prior_state(dict(manifest=m,intake=q,prior=p,params=pa,state=s,strategy_state=ss))
    assert r['binding_status']=='INVALID';j.close()


@pytest.mark.parametrize('fault',['stopped','pending','old_clock','early_receipt','wrong_prefix','future_receipt_seq'])
def test_prior_continuity_faults(tmp_path,fault):
    from app.research.phase6_prior_state_validation import verify_prior_state
    j,w,q,pa,s,ss,p=second(tmp_path);s=copy.deepcopy(s);p=copy.deepcopy(p);m=copy.deepcopy(j._manifest)
    if fault=='stopped':s.update(stopped=True,reason_code='OPERATOR_STOP')
    if fault=='pending':s['pending']=dict(side='BUY',quantity='0.01',intent_utc_ms=s['last_action_utc_ms'],intent_monotonic_ns=s['last_action_monotonic_ns'])
    if fault=='old_clock':s['clock_session_id']='different'
    if fault=='early_receipt':p['event']['monotonic_ns']=180020000000
    if fault=='wrong_prefix':p['checkpoint']['accepted_prefix_hash']='a'*64
    if fault=='future_receipt_seq':p['checkpoint']['last_receipt_seq']=3
    assert verify_prior_state(dict(manifest=m,intake=q,prior=p,params=pa,state=s,strategy_state=ss))['binding_status']=='INVALID';j.close()


def test_prior_binding_root_swap(tmp_path,monkeypatch):
    from pathlib import Path
    j,w,q,pa,s,ss,p=genesis(tmp_path);put=w.put;count=0;before=j.snapshot()
    def swap(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==3:
            root=Path(w.root);root.rename(root.with_name('moved'));root.mkdir()
        return h
    monkeypatch.setattr(w,'put',swap)
    with pytest.raises(Exception):bind(j,p,pa,s,ss)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_head_hash']==before['event_head_hash']


def test_foreign_prior_binding_does_not_poison(tmp_path):
    import threading
    j,w,q,pa,s,ss,p=genesis(tmp_path);errors=[]
    def attempt():
        try:bind(j,p,pa,s,ss)
        except ValueError as e:errors.append(str(e))
    t=threading.Thread(target=attempt);t.start();t.join();assert errors and j.snapshot()['lifecycle']=='OPEN';bind(j,p,pa,s,ss);j.close()


def test_prior_binding_reentry_terminal(tmp_path,monkeypatch):
    j,w,q,pa,s,ss,p=genesis(tmp_path);put=w.put
    def reenter(raw,**kw):
        try:bind(j,p,pa,s,ss)
        except ValueError:pass
        return put(raw,**kw)
    monkeypatch.setattr(w,'put',reenter)
    with pytest.raises(Exception):bind(j,p,pa,s,ss)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


@pytest.mark.parametrize('q',[None,{},[],True])
def test_malformed_prior_request(q):
    from app.research.phase6_prior_state_validation import verify_prior_state
    assert verify_prior_state(q)['binding_status']=='INVALID'


@pytest.mark.parametrize('fault',['prefix','receipt_seq','clock'])
def test_rehashed_prior_artifacts_still_need_continuity(tmp_path,fault):
    from app.research.phase6_prior_state_validation import verify_prior_state
    from app.research.phase6_checkpoint_validation import verify_checkpoint
    j,w,q,pa,s,ss,p=second(tmp_path);p=copy.deepcopy(p);m=copy.deepcopy(j._manifest)
    if fault=='prefix':p['checkpoint']['accepted_prefix_hash']=p['payload']['accepted_prefix_hash']='a'*64
    if fault=='receipt_seq':p['checkpoint']['last_receipt_seq']=p['payload']['last_receipt_seq']=3
    if fault=='clock':p['event'].update(utc_ms=180020,monotonic_ns=180020000000)
    p['event']['payload_hash']=H(p['payload']);p['checkpoint']['journal_head_hash']=H(p['event'])
    p['checkpoint_commit'].update(event_hash=H(p['event']),checkpoint_hash=H(p['checkpoint']))
    m['prior_checkpoint_hash']=H(p['checkpoint'])
    assert verify_checkpoint(p)['binding_status']=='VALID'
    assert verify_prior_state(dict(manifest=m,intake=q,prior=p,params=pa,state=s,strategy_state=ss))['binding_status']=='INVALID';j.close()
