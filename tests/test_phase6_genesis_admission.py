import copy
import pytest
pytest.importorskip('cryptography')
from test_phase6_checkpoint_anchor_validation import fixture,intake_for
from test_phase6_anchor_validation import sign
from test_phase6_intake_binding import read
from test_phase6_bar_call import clock
from app.research.phase6_validation import canonical_hash as H
from app.research.phase6_object_store import ObjectWriter
from app.paper.session import diagnostic_strategy_v1 as strategy,shadow_reducer_v1 as reducer


def inputs(tmp,quote=False):
    seed=tmp/'seed';seed.mkdir();source=fixture(seed,1,bound=True,warmup=0 if quote else 2)
    start=source['start'];q=intake_for(start,1);anchor=copy.deepcopy(start['anchor'])
    anchor['now_utc_ms']=120009;anchor['challenge']['issued_utc_ms']=120008
    anchor['head']['observed_utc_ms']=120009;sign(anchor['head'])
    proof=dict(start=start,anchor=anchor)
    m=dict(source['checkpoints'][0]['manifest'],segment_id=H(q['segments'][0]),persisted_utc_ms=120011)
    root=tmp/'genesis';root.mkdir();w=ObjectWriter(str(root),owner='test',run_id=m['run_id'],max_bytes=1000000)
    return w,m,proof,q


def open_session(w,m,p):
    from app.research.phase6_genesis_admission import open_genesis_session
    return open_genesis_session(w,m,p,utc_ms=120011,monotonic_ns=120011000000)


def prepare(tmp,quote=False):
    w,m,p,q=inputs(tmp,quote);j=open_session(w,m,p);s=q['segments'][0]
    j.append('BAR_ACCEPTED',dict(schema_version='p6.payload.bar_accepted.v1',segment_hash=H(s),receipt_hash=s['accepted_receipt_hash'],bar_hash=s['bar_hash'],accepted_prefix_hash=H(q['prefix'])),utc_ms=120012,monotonic_ns=120012000000)
    pa=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=q['plan']['warmup_bars'],targets=['0.25','0.75'])
    state=reducer.initial({k:q['plan'][k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT})
    return j,w,p,q,pa,state,strategy.initial(pa)


def bind(j,p,q,pa,s,ss):
    j.bind_intake(q);j.bind_prior_state(p['start']['genesis'],params=pa,state=s,strategy_state=ss)


@pytest.mark.parametrize('quote',[False,True])
def test_actual_genesis_through_checkpoint_and_receipt_reader(tmp_path,quote):
    j,w,p,q,pa,s,ss=prepare(tmp_path,quote);bind(j,p,q,pa,s,ss)
    d=j.run_diagnostic(q['plan'],pa,ss,segment_seq=1,clock=clock(120013))
    inv=read(w,d['invocation_hash']);assert inv['schema_version']=='p6.diagnostic_invocation.v6'
    b=read(w,inv['genesis_admission_hash']);assert read(w,b['package_hash'])==dict(manifest=j._manifest,proof=p,utc_ms=120011)
    assert read(w,b['validation_hash'])['binding_status']=='VALID'
    o=dict(close_ms=120000,close='100',received_utc_ms=120010,received_monotonic_ns=120010000000,clock_session_id='test',decision_utc_ms=120014,decision_monotonic_ns=120014000000)
    j.run_bar(s,o,clock=clock(120016))
    source=q
    if quote:
        from test_phase6_quote_receipt_binding import extend,run
        source=extend(q);j.bind_quote_receipts(source);qr=run(j,source)
        assert read(w,qr['invocation_hash'])['schema_version']=='p6.quote_invocation.v2'
    previous=j.snapshot()['last_event'];r=j.prepare_checkpoint(last_receipt_seq=len(source['receipts']),clock=clock(120020))
    c=read(w,r['commit_hash']);e=read(w,r['event_hash']);k=read(w,r['checkpoint_hash'])
    old=dict(manifest=j._manifest,checkpoint=k,checkpoint_commit=c,event=e,payload=read(w,e['payload_hash']),previous_event=previous,state=read(w,c['state_hash']),strategy_state=read(w,c['strategy_state_hash']),execution_commit=read(w,c['execution_commit_hash']),diagnostic_commit=read(w,c['diagnostic_commit_hash']))
    intake=intake_for(p['start'],2)
    if quote:
        last=intake['receipts'][-1];last.update(receipt_seq=3,previous_receipt_hash=H(source['receipts'][-1]))
        intake['receipts']=copy.deepcopy(source['receipts'])+[last]
        intake['payloads'].update(source['payloads']);intake['clock_probes'].update(source['clock_probes'])
        intake['segments'][-1]['accepted_receipt_hash']=H(last);intake['prefix']['segment_hashes'][-1]=H(intake['segments'][-1])
    m=dict(j._manifest,segment_id=H(intake['segments'][-1]),prior_checkpoint_hash=H(k),persisted_utc_ms=180011)
    ps=dict(manifest=m,intake=intake,prior=old,params=pa,state=old['state'],strategy_state=old['strategy_state'])
    ib=read(w,inv['intake_binding_hash']);proof=dict(diagnostic_invocation=inv,intake_binding=ib,intake_bundle=read(w,ib['bundle_hash']),intake_validation=read(w,ib['validation_hash']),execution_invocation=read(w,old['execution_commit']['invocation_hash']),quote_binding=None,quote_bundle=None,quote_validation=None)
    if quote:
        qb=read(w,proof['execution_invocation']['quote_binding_hash'])
        proof.update(quote_binding=qb,quote_bundle=read(w,qb['bundle_hash']),quote_validation=read(w,qb['validation_hash']))
    from app.research.phase6_prior_receipt_validation import verify_prior_receipts
    assert verify_prior_receipts(dict(prior_state=ps,proof=proof))['binding_status']=='VALID'
    assert not r['runtime_accepted'];j.close()


@pytest.mark.parametrize('fault',['missing','signature','extra_receipt','wrong_genesis','wrong_anchor','plan','params','start_prefix','stale_open','future','offline','retention'])
def test_preclaim_refusal(tmp_path,fault):
    w,m,p,q=inputs(tmp_path)
    if fault=='missing':p=None
    if fault=='signature':p['anchor']['head']['signature']='0'*128
    if fault=='extra_receipt':p['anchor']['receipts'].append(copy.deepcopy(p['anchor']['receipts'][-1]))
    if fault=='wrong_genesis':m['prior_checkpoint_hash']='f'*64
    if fault=='wrong_anchor':m['prior_anchor_hash']='f'*64
    if fault in ('plan','params'):m[fault+'_hash']='f'*64
    if fault=='start_prefix':p['anchor']['receipts'][0]['retained_utc_ms']+=1;sign(p['anchor']['receipts'][0])
    if fault=='stale_open':
        p['anchor']['now_utc_ms']=119910;p['anchor']['challenge']['issued_utc_ms']=119910;p['anchor']['head']['observed_utc_ms']=119910;sign(p['anchor']['head'])
    if fault=='future':p['anchor']['now_utc_ms']=120012
    if fault=='offline':p['anchor'].update(head=None,challenge=None)
    if fault=='retention':p['anchor']['head']['retention_until_ms']=301200;sign(p['anchor']['head'])
    with pytest.raises(ValueError):open_session(w,m,p)
    from pathlib import Path
    assert not list(Path(w.root).glob('*.blob'));w.close()


@pytest.mark.parametrize('stage',range(1,7))
def test_uncertain_publication(tmp_path,monkeypatch,stage):
    w,m,p,q=inputs(tmp_path);put=w.put;count=0
    def fail(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==stage:raise RuntimeError('unknown write')
        return h
    monkeypatch.setattr(w,'put',fail)
    with pytest.raises(RuntimeError):open_session(w,m,p)
    with pytest.raises(ValueError):w.put(b'{}')


@pytest.mark.parametrize('fault',['intake','state','expired','clock'])
def test_no_callback_before_admission(tmp_path,monkeypatch,fault):
    j,w,p,q,pa,s,ss=prepare(tmp_path)
    if fault!='intake':j.bind_intake(q)
    if fault in ('expired','clock'):j.bind_prior_state(p['start']['genesis'],params=pa,state=s,strategy_state=ss)
    monkeypatch.setattr(strategy,'advance',lambda *a,**kw:pytest.fail('callback invoked'))
    c=clock(120200) if fault=='expired' else lambda:dict(utc_ms=120013,monotonic_ns=120013000000,clock_session_id='wrong') if fault=='clock' else dict(utc_ms=120013,monotonic_ns=120013000000,clock_session_id='test')
    with pytest.raises(ValueError):j.run_diagnostic(q['plan'],pa,ss,segment_seq=1,clock=c)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_mixed_admissions_refused(tmp_path):
    from app.research.phase6_event_journal import EventJournal
    w,m,p,q=inputs(tmp_path)
    with pytest.raises(ValueError):EventJournal(w,m,utc_ms=120011,monotonic_ns=120011000000,checkpoint_admission={},genesis_admission=p)
    w.close()


def test_genesis_substitution_before_binding(tmp_path):
    j,w,p,q,pa,s,ss=prepare(tmp_path);j.bind_intake(q);g=copy.deepcopy(p['start']['genesis']);g['cash']='1'
    before=j.snapshot()
    with pytest.raises(ValueError):j.bind_prior_state(g,params=pa,state=s,strategy_state=ss)
    assert j.snapshot()['objects']==before['objects']


def test_resigned_replaced_start_prefix(tmp_path):
    w,m,p,q=inputs(tmp_path);rr=p['anchor']['receipts']
    rr[0]['retained_utc_ms']+=1;sign(rr[0]);rr[1]['previous_anchor_hash']=H(rr[0]);sign(rr[1])
    p['anchor']['head']['anchor_hash']=H(rr[-1]);sign(p['anchor']['head']);m['prior_anchor_hash']=H(rr[-1])
    with pytest.raises(ValueError,match='START_PREFIX_MISMATCH'):open_session(w,m,p)
    w.close()


def test_root_substitution_before_ack(tmp_path,monkeypatch):
    from pathlib import Path
    w,m,p,q=inputs(tmp_path);root=Path(w.root);put=w.put;count=0
    def replace(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==3:root.rename(tmp_path/'moved');root.mkdir()
        return h
    monkeypatch.setattr(w,'put',replace)
    with pytest.raises(ValueError):open_session(w,m,p)
    assert not list(root.glob('*.blob'))


def test_foreign_thread_does_not_poison_owner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    w,m,p,q=inputs(tmp_path)
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(ValueError):pool.submit(open_session,w,m,p).result()
    j=open_session(w,m,p);j.close()


def test_mutation_after_snapshot_and_publication_order(tmp_path,monkeypatch):
    import json
    w,m,p,q=inputs(tmp_path);put=w.put;versions=[]
    def publish(raw,**kw):
        versions.append(json.loads(raw).get('schema_version','package'));p.clear();m.clear();return put(raw,**kw)
    monkeypatch.setattr(w,'put',publish);j=open_session(w,m,p)
    assert versions[:4]==['package','p6.genesis_admission_validation.v1','p6.genesis_admission.v1','p6.session_manifest.v1']
    assert j.snapshot()['event_count']==1;j.close()


@pytest.mark.parametrize('bad',[None,{},[],True])
def test_pure_malformed(bad):
    from app.research.phase6_genesis_admission import verify_genesis_admission
    assert verify_genesis_admission(bad)['binding_status']=='INVALID'
