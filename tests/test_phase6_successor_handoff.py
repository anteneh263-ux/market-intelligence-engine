import copy
from pathlib import Path
import pytest
pytest.importorskip('cryptography')
from test_phase6_checkpoint_anchor_binding import fixture
from test_phase6_checkpoint_anchor_validation import intake_for
from test_phase6_anchor_validation import sign
from test_phase6_intake_binding import read
from test_phase6_bar_call import clock
from app.research.phase6_object_store import ObjectWriter
from app.research.phase6_validation import canonical_hash as H


def ready(tmp):
    j,w,p,t=fixture(tmp);binding=j.bind_checkpoint_anchor(p,clock=clock(t))
    p=copy.deepcopy(p);p['anchor']['now_utc_ms']=180009;p['anchor']['challenge']['issued_utc_ms']=180008;p['anchor']['head']['observed_utc_ms']=180009;sign(p['anchor']['head'])
    q=intake_for(p['start'],2);k=p['checkpoints'][-1]['checkpoint']
    m=dict(j._manifest,segment_id=H(q['segments'][-1]),prior_checkpoint_hash=H(k),prior_anchor_hash=H(p['anchor']['receipts'][-1]),persisted_utc_ms=180011)
    root=tmp/'successor';root.mkdir();writer=ObjectWriter(str(root),owner='test',run_id=m['run_id'],max_bytes=1000000)
    return j,w,p,q,m,writer,binding


def open_next(j,writer,m,p):return j.open_successor(writer,m,p,clock=clock(180011))


def test_actual_closed_handoff_through_next_checkpoint(tmp_path):
    j,w,p,q,m,nw,binding=ready(tmp_path);n=open_next(j,nw,m,p)
    status=j.snapshot();assert status['lifecycle']=='CLOSED_SUCCESSOR_OPENED_LOCAL'
    intents=[read(w,h) for h in status['objects'] if read(w,h).get('schema_version')=='p6.successor_intent.v1']
    assert len(intents)==1;c=intents[0];package=read(w,c['package_hash'])
    assert package['manifest']==m and package['proof']==p and package['writer_root']==nw.root
    assert c['checkpoint_anchor_binding_hash']==binding['commit_hash'] and c['next_session_id']==H(m)
    segment=q['segments'][-1];n.append('BAR_ACCEPTED',dict(schema_version='p6.payload.bar_accepted.v1',segment_hash=H(segment),receipt_hash=segment['accepted_receipt_hash'],bar_hash=segment['bar_hash'],accepted_prefix_hash=H(q['prefix'])),utc_ms=180012,monotonic_ns=180012000000)
    n.bind_intake(q);old=p['checkpoints'][-1];pa=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=2,targets=['0.25','0.75'])
    n.bind_prior_state(old,params=pa,state=old['state'],strategy_state=old['strategy_state'])
    d=read(w,old['diagnostic_commit']['invocation_hash']);ib=read(w,d['intake_binding_hash'])
    rp=dict(diagnostic_invocation=d,intake_binding=ib,intake_bundle=read(w,ib['bundle_hash']),intake_validation=read(w,ib['validation_hash']),execution_invocation=read(w,old['execution_commit']['invocation_hash']),quote_binding=None,quote_bundle=None,quote_validation=None)
    n.bind_prior_receipts(rp);n.run_diagnostic(q['plan'],pa,old['strategy_state'],segment_seq=2,clock=clock(180013))
    o=dict(close_ms=180000,close='100',received_utc_ms=180010,received_monotonic_ns=180010000000,clock_session_id='test',decision_utc_ms=180014,decision_monotonic_ns=180014000000)
    n.run_bar(old['state'],o,clock=clock(180016));result=n.prepare_checkpoint(last_receipt_seq=2,clock=clock(180020))
    assert read(nw,result['checkpoint_hash'])['segment_seq']==2 and not result['runtime_accepted'];n.close()
    with pytest.raises(ValueError):open_next(j,nw,m,p)


@pytest.mark.parametrize('fault',['signature','prior_bundle','manifest','clock_session','clock_backwards','same_writer'])
def test_refused_before_intent(tmp_path,fault):
    j,w,p,q,m,nw,b=ready(tmp_path);before=j.snapshot();c=clock(180011);target=nw
    if fault=='signature':p['anchor']['head']['signature']='0'*128
    if fault=='prior_bundle':
        old=p['checkpoints'][0];old['diagnostic_commit']['invocation_hash']='f'*64;old['checkpoint_commit']['diagnostic_commit_hash']=H(old['diagnostic_commit'])
    if fault=='manifest':m['prior_checkpoint_hash']='f'*64
    if fault=='clock_session':c=lambda:dict(utc_ms=180011,monotonic_ns=180011000000,clock_session_id='other')
    if fault=='clock_backwards':c=clock(120000)
    if fault=='same_writer':target=w
    with pytest.raises(ValueError):j.open_successor(target,m,p,clock=c)
    assert j.snapshot()['objects']==before['objects'] and j.snapshot()['lifecycle']=='INCOMPLETE'
    assert not list(Path(nw.root).glob('*.blob'));nw.close()


@pytest.mark.parametrize('stage',[1,2])
def test_intent_write_unknown_no_next_open(tmp_path,monkeypatch,stage):
    j,w,p,q,m,nw,b=ready(tmp_path);put=w.put;count=0
    def fail(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==stage:raise RuntimeError('unknown intent')
        return h
    monkeypatch.setattr(w,'put',fail)
    with pytest.raises(RuntimeError):open_next(j,nw,m,p)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and not list(Path(nw.root).glob('*.blob'))
    with pytest.raises(ValueError):open_next(j,nw,m,p)
    nw.close()


@pytest.mark.parametrize('stage',range(1,7))
def test_next_publication_unknown_no_retry(tmp_path,monkeypatch,stage):
    j,w,p,q,m,nw,b=ready(tmp_path);put=nw.put;count=0
    def fail(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==stage:raise RuntimeError('unknown successor')
        return h
    monkeypatch.setattr(nw,'put',fail)
    with pytest.raises(RuntimeError):open_next(j,nw,m,p)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'
    with pytest.raises(ValueError):w.put(b'{}')
    with pytest.raises(ValueError):nw.put(b'{}')
    with pytest.raises(ValueError):open_next(j,nw,m,p)


def test_inventory_failure_blocks_next_writer(tmp_path,monkeypatch):
    import app.research.phase6_event_journal as module
    j,w,p,q,m,nw,b=ready(tmp_path)
    def fail(*a,**kw):raise ValueError('INTEGRITY_FAILURE')
    monkeypatch.setattr(module,'inspect_objects',fail)
    with pytest.raises(ValueError):open_next(j,nw,m,p)
    assert not list(Path(nw.root).glob('*.blob'));nw.close()


def test_old_inventory_checked_before_next_publish(tmp_path,monkeypatch):
    j,w,p,q,m,nw,b=ready(tmp_path);put=nw.put
    def check(raw,**kw):
        assert j.snapshot()['lifecycle']=='CLOSED_SUCCESSOR_PENDING'
        with pytest.raises(ValueError):w.put(b'{}')
        return put(raw,**kw)
    monkeypatch.setattr(nw,'put',check);n=open_next(j,nw,m,p);n.close()


def test_clock_reentry_is_terminal(tmp_path):
    j,w,p,q,m,nw,b=ready(tmp_path)
    def reenter():open_next(j,nw,m,p)
    with pytest.raises(ValueError):j.open_successor(nw,m,p,clock=reenter)
    assert j.snapshot()['lifecycle']=='INCOMPLETE';nw.close()


def test_requires_bound_checkpoint_not_just_checkpoint(tmp_path):
    j,w,p,t=fixture(tmp_path)
    with pytest.raises(ValueError):j.open_successor(w,j._manifest,p,clock=clock(t))
    assert j.snapshot()['lifecycle']=='CHECKPOINTED_LOCAL';j.close()


def test_foreign_thread_keeps_owner_usable(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    j,w,p,q,m,nw,b=ready(tmp_path)
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(ValueError):pool.submit(open_next,j,nw,m,p).result()
    n=open_next(j,nw,m,p);n.close()


@pytest.mark.parametrize('target',['old','next'])
def test_root_substitution_before_close_blocks_open(tmp_path,monkeypatch,target):
    j,w,p,q,m,nw,b=ready(tmp_path);put=w.put;count=0
    root=Path(w.root if target=='old' else nw.root)
    def replace(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==2:root.rename(tmp_path/'moved');root.mkdir()
        return h
    monkeypatch.setattr(w,'put',replace)
    with pytest.raises(ValueError):open_next(j,nw,m,p)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and not list(Path(nw.root).glob('*.blob'))
    try:nw.close()
    except ValueError:pass


def test_callback_mutation_cannot_replace_inputs(tmp_path):
    j,w,p,q,m,nw,b=ready(tmp_path);expected=copy.deepcopy(m)
    def mutate():p.clear();m.clear();return dict(utc_ms=180011,monotonic_ns=180011000000,clock_session_id='test')
    n=j.open_successor(nw,m,p,clock=mutate);assert n._manifest==expected;n.close()


def test_swallowed_reentry_during_next_open_closes_both(tmp_path,monkeypatch):
    j,w,p,q,m,nw,b=ready(tmp_path);put=nw.put
    def reenter(raw,**kw):
        with pytest.raises(ValueError):open_next(j,nw,m,p)
        return put(raw,**kw)
    monkeypatch.setattr(nw,'put',reenter)
    with pytest.raises(ValueError):open_next(j,nw,m,p)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'
    with pytest.raises(ValueError):nw.put(b'{}')


def test_stopped_checkpoint_cannot_handoff(tmp_path):
    j,w,p,t=fixture(tmp_path,'timeout');j.bind_checkpoint_anchor(p,clock=clock(t))
    p['anchor']['now_utc_ms']=180009;p['anchor']['challenge']['issued_utc_ms']=180008;p['anchor']['head']['observed_utc_ms']=180009;sign(p['anchor']['head'])
    q=intake_for(p['start'],2);m=dict(j._manifest,segment_id=H(q['segments'][-1]),prior_checkpoint_hash=H(p['checkpoints'][-1]['checkpoint']),prior_anchor_hash=H(p['anchor']['receipts'][-1]),persisted_utc_ms=180011)
    root=tmp_path/'stopped-next';root.mkdir();nw=ObjectWriter(str(root),owner='test',run_id=m['run_id'],max_bytes=1000000)
    with pytest.raises(ValueError,match='STATE_INVALID'):open_next(j,nw,m,p)
    assert not list(root.glob('*.blob'));nw.close()
