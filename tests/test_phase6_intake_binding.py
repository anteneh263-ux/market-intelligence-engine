import copy,json
from pathlib import Path
import pytest
from test_phase6_intake_validation import fixture
from test_phase6_event_validation import case
from test_phase6_bar_call import clock
from app.paper.session import diagnostic_strategy_v1 as strategy,shadow_reducer_v1 as reducer
from app.research.phase6_validation import canonical_hash as H
from app.research.phase6_event_journal import EventJournal
from app.research.phase6_object_store import ObjectWriter


def setup(tmp_path,warmup=1):
    q=fixture();pa=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=warmup,targets=['0.25','0.75'])
    q['plan'].update(max_target='0.75',warmup_bars=warmup,params_hash=H(pa),end_ms=300000)
    q['reservation']['plan_hash']=H(q['plan']);run=H(q['reservation'])
    q['receipts'][0]['run_id']=run;q['segments'][0].update(run_id=run,accepted_receipt_hash=H(q['receipts'][0]))
    q['prefix'].update(run_id=run,segment_hashes=[H(q['segments'][0])])
    m=case()['manifest'];m.update(run_id=run,plan_hash=H(q['plan']),params_hash=H(pa),strategy_code_hash=q['plan']['strategy_code_hash'],segment_id=H(q['segments'][0]),persisted_utc_ms=120011)
    # Match the test clock's monotonic scale without claiming real time.
    e=q['receipts'][0];probe=next(iter(q['clock_probes'].values()));probe.update(measured_monotonic_ns=119000000000,valid_until_monotonic_ns=121000000000)
    q['clock_probes']={H(probe):probe};e.update(clock_probe_hash=H(probe),received_monotonic_ns=120010000000,clock_session_id='test');probe['clock_session_id']='test';q['clock_probes']={H(probe):probe};e['clock_probe_hash']=H(probe)
    q['segments'][0]['accepted_receipt_hash']=H(e);q['prefix']['segment_hashes']=[H(q['segments'][0])];m['segment_id']=H(q['segments'][0])
    root=tmp_path/'objects';root.mkdir();w=ObjectWriter(str(root),owner='test',run_id=run,max_bytes=1000000)
    j=EventJournal(w,m,utc_ms=120011,monotonic_ns=120011000000)
    s=q['segments'][0];p=dict(schema_version='p6.payload.bar_accepted.v1',segment_hash=H(s),receipt_hash=H(e),bar_hash=s['bar_hash'],accepted_prefix_hash=H(q['prefix']))
    j.append('BAR_ACCEPTED',p,utc_ms=120012,monotonic_ns=120012000000)
    return j,w,q,pa


def read(w,h):return json.loads((Path(w.root)/(h+'.blob')).read_bytes())
def diagnostic(j,q,pa):return j.run_diagnostic(q['plan'],pa,strategy.initial(pa),segment_seq=1,clock=clock(120013))
def bar(q):
    e=q['receipts'][-1];b=q['payloads'][q['segments'][-1]['bar_hash']]
    return dict(close_ms=b['close_ms'],close=b['close'],received_utc_ms=e['received_utc_ms'],received_monotonic_ns=e['received_monotonic_ns'],clock_session_id=e['clock_session_id'],decision_utc_ms=120014,decision_monotonic_ns=120014000000)


def test_bound_strategy_bar_and_checkpoint(tmp_path):
    j,w,q,pa=setup(tmp_path);r=j.bind_intake(q)
    assert read(w,r['bundle_hash'])==q and not r['runtime_accepted']
    d=diagnostic(j,q,pa);inv=read(w,d['invocation_hash'])
    assert inv['schema_version']=='p6.diagnostic_invocation.v2' and inv['intake_binding_hash']==r['commit_hash']
    state=reducer.initial({k:q['plan'][k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT})
    j.run_bar(state,bar(q),clock=clock(120016))
    cp=j.prepare_checkpoint(last_receipt_seq=1,clock=clock(120020));k=read(w,cp['checkpoint_hash'])
    assert k['accepted_prefix_hash']==H(q['prefix']) and k['last_receipt_seq']==1
    j.close()


@pytest.mark.parametrize('fault',['plan','run','prefix','bar','trailing_quote'])
def test_mismatched_bundle_refused_before_write(tmp_path,fault):
    j,w,q,pa=setup(tmp_path);before=j.snapshot()
    if fault=='plan':q['plan']['owner']='other'
    if fault=='run':q['reservation']['registry_identity']='a'*64
    if fault=='prefix':q['prefix']['segment_hashes']=['a'*64]
    if fault=='bar':next(iter(q['payloads'].values()))['close']='100'
    if fault=='trailing_quote':
        from test_phase6_intake_validation import append
        append(q)
    with pytest.raises(ValueError):j.bind_intake(q)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['objects']==before['objects']


@pytest.mark.parametrize('stage',[1,2,3])
def test_uncertain_binding_publications_stop(tmp_path,monkeypatch,stage):
    j,w,q,pa=setup(tmp_path);put=w.put;before=j.snapshot();count=0
    def fail(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==stage:raise RuntimeError('uncertain write')
        return h
    monkeypatch.setattr(w,'put',fail)
    with pytest.raises(RuntimeError):j.bind_intake(q)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_head_hash']==before['event_head_hash']
    with pytest.raises(ValueError):diagnostic(j,q,pa)


@pytest.mark.parametrize('field',['close','close_ms','received_utc_ms','received_monotonic_ns','clock_session_id'])
def test_bar_cannot_substitute_bound_input(tmp_path,field):
    j,w,q,pa=setup(tmp_path);j.bind_intake(q);diagnostic(j,q,pa);o=bar(q)
    o[field]='other' if field=='clock_session_id' else '100' if field=='close' else o[field]+1
    state=reducer.initial({k:q['plan'][k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT})
    with pytest.raises(ValueError):j.run_bar(state,o,clock=clock(120016))
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_no_quote_checkpoint_cannot_invent_later_receipts(tmp_path):
    j,w,q,pa=setup(tmp_path);j.bind_intake(q);diagnostic(j,q,pa)
    state=reducer.initial({k:q['plan'][k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT});j.run_bar(state,bar(q),clock=clock(120016))
    with pytest.raises(ValueError):j.prepare_checkpoint(last_receipt_seq=2,clock=clock(120020))


def test_binding_copies_caller_input(tmp_path):
    j,w,q,pa=setup(tmp_path);j.bind_intake(q);q['receipts'][0]['received_utc_ms']=0
    diagnostic(j,q,pa);j.close()


def test_double_binding_refused(tmp_path):
    j,w,q,pa=setup(tmp_path);j.bind_intake(q)
    with pytest.raises(ValueError):j.bind_intake(q)


@pytest.mark.parametrize('seq',[1,2])
def test_quote_requires_later_checkpoint_receipt(tmp_path,seq):
    j,w,q,pa=setup(tmp_path,warmup=0);j.bind_intake(q);diagnostic(j,q,pa)
    state=reducer.initial({k:q['plan'][k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT});j.run_bar(state,bar(q),clock=clock(120016))
    from test_phase6_quote_receipt_binding import extend
    q=extend(q);j.bind_quote_receipts(q)
    j.run_quote(dict(bid='101',ask='101',received_utc_ms=120018,received_monotonic_ns=120018000000,clock_session_id='test'),quote_receipt_hash=H(q['receipts'][-1]),clock=clock(120019))
    if seq==1:
        with pytest.raises(ValueError):j.prepare_checkpoint(last_receipt_seq=seq,clock=clock(120022))
    else:
        cp=j.prepare_checkpoint(last_receipt_seq=seq,clock=clock(120022));assert read(w,cp['checkpoint_hash'])['last_receipt_seq']==2;j.close()


def test_binding_after_diagnostic_refused(tmp_path):
    j,w,q,pa=setup(tmp_path);diagnostic(j,q,pa)
    with pytest.raises(ValueError):j.bind_intake(q)


def test_callback_sees_durable_binding(tmp_path,monkeypatch):
    j,w,q,pa=setup(tmp_path);binding=j.bind_intake(q);advance=strategy.advance;seen=[]
    def callback(*a,**kw):
        assert read(w,binding['commit_hash'])['bundle_hash']==binding['bundle_hash']
        assert any(json.loads(f.read_bytes()).get('intake_binding_hash')==binding['commit_hash'] for f in Path(w.root).glob('*.blob'))
        seen.append(1);return advance(*a,**kw)
    monkeypatch.setattr(strategy,'advance',callback);diagnostic(j,q,pa);assert seen==[1];j.close()


def test_final_root_substitution_refused(tmp_path,monkeypatch):
    j,w,q,pa=setup(tmp_path);put=w.put;count=0;before=j.snapshot()
    def swap(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==3:
            root=Path(w.root);root.rename(root.with_name('moved'));root.mkdir()
        return h
    monkeypatch.setattr(w,'put',swap)
    with pytest.raises(Exception):j.bind_intake(q)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_head_hash']==before['event_head_hash']


def test_binding_reentry_terminal(tmp_path,monkeypatch):
    j,w,q,pa=setup(tmp_path);put=w.put
    def reenter(raw,**kw):
        try:j.bind_intake(q)
        except ValueError:pass
        return put(raw,**kw)
    monkeypatch.setattr(w,'put',reenter)
    with pytest.raises(Exception):j.bind_intake(q)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_foreign_thread_does_not_poison_binding(tmp_path):
    import threading
    j,w,q,pa=setup(tmp_path);errors=[]
    def attempt():
        try:j.bind_intake(q)
        except ValueError as e:errors.append(str(e))
    t=threading.Thread(target=attempt);t.start();t.join()
    assert errors and j.snapshot()['lifecycle']=='OPEN'
    j.bind_intake(q);j.close()


def test_bound_diagnostic_wrong_sequence_refused(tmp_path):
    j,w,q,pa=setup(tmp_path);j.bind_intake(q)
    with pytest.raises(ValueError):j.run_diagnostic(q['plan'],pa,strategy.initial(pa),segment_seq=2,clock=clock(120013))
