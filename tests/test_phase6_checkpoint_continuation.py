import copy
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
pytest.importorskip('cryptography')
from test_phase6_checkpoint_anchor_validation import fixture,intake_for
from test_phase6_anchor_validation import sign
from test_phase6_intake_binding import read
from test_phase6_bar_call import clock
from app.research.phase6_validation import canonical_hash as H
from app.research.phase6_object_store import ObjectWriter
from app.research.phase6_event_journal import EventJournal
from app.paper.session import diagnostic_strategy_v1 as strategy


def inputs(tmp):
    proof=fixture(tmp,1,bound=True);old=proof['checkpoints'][-1];q=intake_for(proof['start'],2)
    proof['anchor']['now_utc_ms']=180009;proof['anchor']['challenge']['issued_utc_ms']=180008
    proof['anchor']['head']['observed_utc_ms']=180009;sign(proof['anchor']['head'])
    m=dict(old['manifest'],segment_id=H(q['segments'][-1]),prior_checkpoint_hash=H(old['checkpoint']),prior_anchor_hash=H(proof['anchor']['receipts'][-1]),persisted_utc_ms=180011)
    root=tmp/'next';root.mkdir();w=ObjectWriter(str(root),owner='test',run_id=m['run_id'],max_bytes=1000000)
    return w,m,proof,q


def open_session(w,m,proof):
    from app.research.phase6_checkpoint_continuation import open_checkpoint_continuation
    return open_checkpoint_continuation(w,m,proof,utc_ms=180011,monotonic_ns=180011000000)


def prepare(tmp):
    w,m,proof,q=inputs(tmp);j=open_session(w,m,proof)
    s=q['segments'][-1]
    j.append('BAR_ACCEPTED',dict(schema_version='p6.payload.bar_accepted.v1',segment_hash=H(s),receipt_hash=s['accepted_receipt_hash'],bar_hash=s['bar_hash'],accepted_prefix_hash=H(q['prefix'])),utc_ms=180012,monotonic_ns=180012000000)
    return j,w,proof,q


def bind_all(tmp,j,proof,q):
    j.bind_intake(q);p=proof['checkpoints'][-1]
    pa=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=2,targets=['0.25','0.75'])
    j.bind_prior_state(p,params=pa,state=p['state'],strategy_state=p['strategy_state'])
    def read_old(h):return json.loads((tmp/'session1'/(h+'.blob')).read_bytes())
    d=read_old(p['diagnostic_commit']['invocation_hash']);b=read_old(d['intake_binding_hash'])
    receiptproof=dict(diagnostic_invocation=d,intake_binding=b,intake_bundle=read_old(b['bundle_hash']),intake_validation=read_old(b['validation_hash']),execution_invocation=read_old(p['execution_commit']['invocation_hash']),quote_binding=None,quote_bundle=None,quote_validation=None)
    j.bind_prior_receipts(receiptproof);return pa


def test_actual_bound_continuation(tmp_path):
    j,w,proof,q=prepare(tmp_path);pa=bind_all(tmp_path,j,proof,q);old=proof['checkpoints'][-1]
    d=j.run_diagnostic(q['plan'],pa,old['strategy_state'],segment_seq=2,clock=clock(180013))
    inv=read(w,d['invocation_hash']);assert inv['schema_version']=='p6.diagnostic_invocation.v5'
    binding=read(w,inv['checkpoint_admission_hash']);assert binding['prior_checkpoint_hash']==H(old['checkpoint'])
    assert read(w,binding['validation_hash'])['binding_status']=='VALID'
    assert read(w,binding['package_hash'])==dict(manifest=j._manifest,proof=proof,utc_ms=180011)
    e=q['receipts'][-1];o=dict(close_ms=180000,close='100',received_utc_ms=e['received_utc_ms'],received_monotonic_ns=e['received_monotonic_ns'],clock_session_id='test',decision_utc_ms=180014,decision_monotonic_ns=180014000000)
    j.run_bar(old['state'],o,clock=clock(180016));r=j.prepare_checkpoint(last_receipt_seq=2,clock=clock(180020))
    assert read(w,r['checkpoint_hash'])['segment_seq']==2 and not r['runtime_accepted'];j.close()


@pytest.mark.parametrize('fault',['missing','signature','prior_anchor','prior_checkpoint','plan','engine','stale','future','stopped'])
def test_refused_before_any_object_publication(tmp_path,fault):
    w,m,p,q=inputs(tmp_path)
    if fault=='missing':p=None
    if fault=='signature':p['anchor']['head']['signature']='0'*128
    if fault in ('prior_anchor','prior_checkpoint'):m[fault+'_hash']='a'*64
    if fault=='plan':m['plan_hash']='a'*64
    if fault=='engine':m['engine_code_hash']='a'*64
    if fault=='stale':p['anchor']['now_utc_ms']=179000
    if fault=='future':p['anchor']['now_utc_ms']=180012
    if fault=='stopped':p['checkpoints'][-1]['checkpoint']['stopped']=True
    with pytest.raises(ValueError):open_session(w,m,p)
    from pathlib import Path
    assert not list(Path(w.root).glob('*.blob'));w.close()


@pytest.mark.parametrize('stage',range(1,7))
def test_uncertain_publication_closes_writer(tmp_path,monkeypatch,stage):
    w,m,p,q=inputs(tmp_path);put=w.put;count=0
    def fail(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==stage:raise RuntimeError('unknown publication')
        return h
    monkeypatch.setattr(w,'put',fail)
    with pytest.raises(RuntimeError):open_session(w,m,p)
    with pytest.raises(ValueError):w.put(b'{}')


@pytest.mark.parametrize('stage',['intake','state','receipts'])
def test_missing_mandatory_binding_blocks_callback(tmp_path,monkeypatch,stage):
    j,w,p,q=prepare(tmp_path);old=p['checkpoints'][-1]
    pa=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=2,targets=['0.25','0.75'])
    if stage!='intake':j.bind_intake(q)
    if stage=='receipts':j.bind_prior_state(old,params=pa,state=old['state'],strategy_state=old['strategy_state'])
    monkeypatch.setattr(strategy,'advance',lambda *a,**kw:pytest.fail('callback called'))
    with pytest.raises(ValueError,match='ADMISSION_REQUIRED'):j.run_diagnostic(q['plan'],pa,old['strategy_state'],segment_seq=2,clock=clock(180013))
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_caller_mutation_cannot_change_admission(tmp_path):
    w,m,p,q=inputs(tmp_path);j=open_session(w,m,p);p.clear();m.clear()
    assert j.snapshot()['event_count']==1;j.close()


def test_prior_bundle_substitution_even_same_checkpoint(tmp_path):
    j,w,p,q=prepare(tmp_path);j.bind_intake(q);old=copy.deepcopy(p['checkpoints'][-1])
    old['diagnostic_commit']['invocation_hash']='f'*64
    old['checkpoint_commit']['diagnostic_commit_hash']=H(old['diagnostic_commit'])
    from app.research.phase6_checkpoint_validation import verify_checkpoint
    assert verify_checkpoint(old)['binding_status']=='VALID'
    pa=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=2,targets=['0.25','0.75'])
    before=j.snapshot()
    with pytest.raises(ValueError,match='IDENTITY_MISMATCH'):j.bind_prior_state(old,params=pa,state=old['state'],strategy_state=old['strategy_state'])
    assert j.snapshot()['objects']==before['objects'] and j.snapshot()['lifecycle']=='INCOMPLETE'


def test_head_fresh_at_recorded_time_but_stale_at_open(tmp_path):
    w,m,p,q=inputs(tmp_path)
    p['anchor']['now_utc_ms']=179910;p['anchor']['head']['observed_utc_ms']=179910
    p['anchor']['challenge']['issued_utc_ms']=179910;sign(p['anchor']['head'])
    from app.research.phase6_checkpoint_anchor_validation import verify_checkpoint_anchors
    assert verify_checkpoint_anchors(p)['binding_status']=='VALID'
    with pytest.raises(ValueError,match='ANCHOR_INVALID'):open_session(w,m,p)
    w.close()


def test_root_substitution_before_admission_ack(tmp_path,monkeypatch):
    from pathlib import Path
    w,m,p,q=inputs(tmp_path);put=w.put;count=0;root=Path(w.root)
    def replace(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==3:root.rename(tmp_path/'moved');root.mkdir()
        return h
    monkeypatch.setattr(w,'put',replace)
    with pytest.raises(ValueError):open_session(w,m,p)
    assert not list(root.glob('*.blob'))


def test_foreign_thread_refused_without_poisoning(tmp_path):
    w,m,p,q=inputs(tmp_path)
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(ValueError):pool.submit(open_session,w,m,p).result()
    j=open_session(w,m,p);j.close()


def test_admission_published_before_manifest(tmp_path,monkeypatch):
    w,m,p,q=inputs(tmp_path);put=w.put;versions=[]
    def capture(raw,**kw):
        versions.append(json.loads(raw).get('schema_version','package'));return put(raw,**kw)
    monkeypatch.setattr(w,'put',capture);j=open_session(w,m,p)
    assert versions[:4]==['package','p6.checkpoint_continuation_validation.v1','p6.checkpoint_admission.v1','p6.session_manifest.v1'];j.close()


@pytest.mark.parametrize('fault',['expired','clock_session'])
def test_recheck_at_actual_callback(tmp_path,monkeypatch,fault):
    j,w,p,q=prepare(tmp_path);pa=bind_all(tmp_path,j,p,q)
    monkeypatch.setattr(strategy,'advance',lambda *a,**kw:pytest.fail('callback invoked'))
    c=clock(180200) if fault=='expired' else lambda:dict(utc_ms=180013,monotonic_ns=180013000000,clock_session_id='different')
    with pytest.raises(ValueError,match='ADMISSION_EXPIRED' if fault=='expired' else 'CLOCK_INVALID'):
        j.run_diagnostic(q['plan'],pa,p['checkpoints'][-1]['strategy_state'],segment_seq=2,clock=c)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'
