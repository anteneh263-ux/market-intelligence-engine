import copy
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
pytest.importorskip('cryptography')
from test_phase6_genesis_admission import prepare as genesis,bind
from test_phase6_checkpoint_continuation import prepare as continuation,bind_all
from test_phase6_quote_receipt_binding import extend,run
from test_phase6_intake_binding import read
from test_phase6_bar_call import clock
from test_phase6_anchor_validation import sign
from app.research.phase6_validation import canonical_hash as H


def fixture(tmp,mode='genesis'):
    if mode=='continuation':
        j,w,p,q=continuation(tmp);pa=bind_all(tmp,j,p,q);old=p['checkpoints'][-1];s,ss=old['state'],old['strategy_state'];t=180010
    else:
        j,w,p,q,pa,s,ss=genesis(tmp,quote=mode in ('fill','timeout'));bind(j,p,q,pa,s,ss);t=120010
    j.run_diagnostic(q['plan'],pa,ss,segment_seq=len(q['segments']),clock=clock(t+3))
    o=dict(close_ms=t-10,close='100',received_utc_ms=t,received_monotonic_ns=t*1000000,clock_session_id='test',decision_utc_ms=t+4,decision_monotonic_ns=(t+4)*1000000)
    j.run_bar(s,o,clock=clock(t+6))
    if mode in ('fill','timeout'):
        q=extend(q,t+108 if mode=='timeout' else t+8);j.bind_quote_receipts(q);run(j,q)
    t+=200
    previous=j.snapshot()['last_event'];r=j.prepare_checkpoint(last_receipt_seq=len(q['receipts']),clock=clock(t))
    c=read(w,r['commit_hash']);e=read(w,r['event_hash']);k=read(w,r['checkpoint_hash'])
    b=dict(manifest=j._manifest,checkpoint=k,checkpoint_commit=c,event=e,payload=read(w,e['payload_hash']),previous_event=previous,state=read(w,c['state_hash']),strategy_state=read(w,c['strategy_state_hash']),execution_commit=read(w,c['execution_commit_hash']),diagnostic_commit=read(w,c['diagnostic_commit_hash']))
    proof=copy.deepcopy(dict(start=p['start'],checkpoints=p.get('checkpoints',[])+[b],anchor=p['anchor']))
    rr=proof['anchor']['receipts'];rr.append(sign(dict(schema_version='p6.anchor_receipt.v1',run_id=k['run_id'],anchor_seq=len(rr)+1,subject_kind='CHECKPOINT',subject_hash=H(k),previous_anchor_hash=H(rr[-1]),retained_utc_ms=t+2,service_identity=proof['anchor']['trust']['service_identity'])))
    a=proof['anchor'];a['now_utc_ms']=t+4;a['challenge']['issued_utc_ms']=t+3
    a['head'].update(anchor_seq=len(rr),anchor_hash=H(rr[-1]),observed_utc_ms=t+4);sign(a['head'])
    return j,w,proof,t+5


@pytest.mark.parametrize('mode',['genesis','continuation','fill','timeout'])
def test_actual_checkpoint_anchor_binding(tmp_path,mode):
    j,w,p,t=fixture(tmp_path,mode);before=j.snapshot();r=j.bind_checkpoint_anchor(p,clock=clock(t))
    after=j.snapshot();assert after['lifecycle']=='CHECKPOINT_ANCHOR_BOUND_LOCAL'
    assert after['event_head_hash']==before['event_head_hash'] and after['event_count']==before['event_count']
    c=read(w,r['commit_hash']);assert c['checkpoint_hash']==H(p['checkpoints'][-1]['checkpoint'])
    assert c['anchor_hash']==H(p['anchor']['receipts'][-1])
    assert read(w,c['package_hash'])['proof']==p
    assert read(w,c['validation_hash'])['binding_status']=='VALID'
    assert not r['runtime_accepted'] and not r['independent_retention_verified']
    j.close();assert j.snapshot()['lifecycle']=='CLOSED'


@pytest.mark.parametrize('fault',['signature','subject','extra','offline','freshness','clock_session','clock_backwards','future','wrong_commit','prior_prefix'])
def test_refused_before_publications(tmp_path,fault):
    j,w,p,t=fixture(tmp_path);before=j.snapshot();c=clock(t)
    if fault=='signature':p['anchor']['head']['signature']='0'*128
    if fault=='subject':p['anchor']['receipts'][-1]['subject_hash']='f'*64;sign(p['anchor']['receipts'][-1])
    if fault=='extra':p['checkpoints'].append(copy.deepcopy(p['checkpoints'][-1]))
    if fault=='offline':p['anchor'].update(head=None,challenge=None)
    if fault=='freshness':c=clock(t+101)
    if fault=='clock_session':c=lambda:dict(utc_ms=t,monotonic_ns=t*1000000,clock_session_id='other')
    if fault=='clock_backwards':c=clock(t-6)
    if fault=='future':p['anchor']['now_utc_ms']=t+1
    if fault=='wrong_commit':
        b=p['checkpoints'][-1];b['diagnostic_commit']['invocation_hash']='f'*64;b['checkpoint_commit']['diagnostic_commit_hash']=H(b['diagnostic_commit'])
        from app.research.phase6_checkpoint_validation import verify_checkpoint
        assert verify_checkpoint(b)['binding_status']=='VALID'
    if fault=='prior_prefix':p['start']['authorization']['approval_reference']='changed'
    with pytest.raises(ValueError):j.bind_checkpoint_anchor(p,clock=c)
    after=j.snapshot();assert after['lifecycle']=='INCOMPLETE' and after['objects']==before['objects'] and after['event_head_hash']==before['event_head_hash']


@pytest.mark.parametrize('stage',[1,2,3])
def test_unknown_publication_terminal(tmp_path,monkeypatch,stage):
    j,w,p,t=fixture(tmp_path);before=j.snapshot();put=w.put;count=0
    def fail(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==stage:raise RuntimeError('unknown write')
        return h
    monkeypatch.setattr(w,'put',fail)
    with pytest.raises(RuntimeError):j.bind_checkpoint_anchor(p,clock=clock(t))
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_head_hash']==before['event_head_hash']
    with pytest.raises(ValueError):j.bind_checkpoint_anchor(p,clock=clock(t))


def test_root_substitution(tmp_path,monkeypatch):
    j,w,p,t=fixture(tmp_path);root=Path(w.root);put=w.put;count=0
    def replace(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==3:root.rename(tmp_path/'moved');root.mkdir()
        return h
    monkeypatch.setattr(w,'put',replace)
    with pytest.raises(ValueError):j.bind_checkpoint_anchor(p,clock=clock(t))
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_foreign_thread_does_not_poison(tmp_path):
    j,w,p,t=fixture(tmp_path)
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(ValueError):pool.submit(j.bind_checkpoint_anchor,p,clock=clock(t)).result()
    j.bind_checkpoint_anchor(p,clock=clock(t));j.close()


def test_reentry_clock_stops(tmp_path):
    j,w,p,t=fixture(tmp_path)
    def reenter():j.bind_checkpoint_anchor(p,clock=clock(t))
    with pytest.raises(ValueError):j.bind_checkpoint_anchor(p,clock=reenter)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_snapshot_before_clock_mutation(tmp_path):
    j,w,p,t=fixture(tmp_path);original=copy.deepcopy(p)
    def mutate():p.clear();return dict(utc_ms=t,monotonic_ns=t*1000000,clock_session_id='test')
    r=j.bind_checkpoint_anchor(p,clock=mutate);assert read(w,r['package_hash'])['proof']==original;j.close()


def test_one_shot_and_no_further_events(tmp_path):
    j,w,p,t=fixture(tmp_path);j.bind_checkpoint_anchor(p,clock=clock(t));before=j.snapshot()
    with pytest.raises(ValueError):j.bind_checkpoint_anchor(p,clock=clock(t))
    with pytest.raises(ValueError):j.append('STOP',{},utc_ms=t,monotonic_ns=t*1000000)
    assert j.snapshot()==before;j.close()


def test_requires_actual_checkpoint(tmp_path):
    j,w,p,q,pa,s,ss=genesis(tmp_path)
    with pytest.raises(ValueError):j.bind_checkpoint_anchor({},clock=clock(120020))
    assert j.snapshot()['lifecycle']=='OPEN';j.close()


def test_rehashed_valid_earlier_bundle_cannot_replace_admitted_prefix(tmp_path):
    j,w,p,t=fixture(tmp_path,'continuation');b=p['checkpoints'][0]
    b['diagnostic_commit']['invocation_hash']='f'*64
    b['checkpoint_commit']['diagnostic_commit_hash']=H(b['diagnostic_commit'])
    from app.research.phase6_checkpoint_anchor_validation import verify_checkpoint_anchors
    assert verify_checkpoint_anchors(p)['binding_status']=='VALID'
    before=j.snapshot()
    with pytest.raises(ValueError,match='ANCESTRY_MISMATCH'):j.bind_checkpoint_anchor(p,clock=clock(t))
    assert j.snapshot()['objects']==before['objects']


def test_legacy_checkpoint_has_no_admission_authority(tmp_path):
    from test_phase6_bar_call import setup,call
    j,w,s,o,t=setup(tmp_path,warmup=2)
    call(j,s,o,t);j.prepare_checkpoint(last_receipt_seq=1,clock=clock(t+200))
    before=j.snapshot()
    with pytest.raises(ValueError,match='ADMISSION_REQUIRED'):j.bind_checkpoint_anchor({},clock=clock(t+201))
    assert j.snapshot()['objects']==before['objects'] and j.snapshot()['lifecycle']=='INCOMPLETE'


@pytest.mark.parametrize('bad',[None,{},[],True])
def test_malformed_proof_is_terminal_without_writes(tmp_path,bad):
    j,w,p,t=fixture(tmp_path);before=j.snapshot()
    with pytest.raises(ValueError):j.bind_checkpoint_anchor(bad,clock=clock(t))
    assert j.snapshot()['objects']==before['objects'] and j.snapshot()['lifecycle']=='INCOMPLETE'
