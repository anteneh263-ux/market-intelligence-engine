import copy,json
from pathlib import Path
import pytest
from test_phase6_intake_binding import setup,diagnostic,bar,read
from test_phase6_bar_call import clock
from app.paper.session import shadow_reducer_v1 as reducer
from app.research.phase6_validation import canonical_hash as H


def ready(tmp_path):
    j,w,q,pa=setup(tmp_path,warmup=0);j.bind_intake(q);diagnostic(j,q,pa)
    s=reducer.initial({k:q['plan'][k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT})
    j.run_bar(s,bar(q),clock=clock(120016));return j,w,q


def extend(q,t=120018):
    q=copy.deepcopy(q);p=dict(schema_version='p6.quote.v1',provider=q['plan']['provider'],instrument=q['plan']['instrument'],bid='101',ask='101')
    q['payloads'][H(p)]=p;e=copy.deepcopy(q['receipts'][-1])
    e.update(kind='QUOTE',receipt_seq=e['receipt_seq']+1,previous_receipt_hash=H(q['receipts'][-1]),normalized_payload_hash=H(p),provider_event_ms=t,received_utc_ms=t,received_monotonic_ns=t*1000000)
    q['receipts'].append(e);return q


def observation(q):
    e=q['receipts'][-1];p=q['payloads'][e['normalized_payload_hash']]
    return dict(bid=p['bid'],ask=p['ask'],received_utc_ms=e['received_utc_ms'],received_monotonic_ns=e['received_monotonic_ns'],clock_session_id=e['clock_session_id'])


def run(j,q):return j.run_quote(observation(q),quote_receipt_hash=H(q['receipts'][-1]),clock=clock(q['receipts'][-1]['received_utc_ms']+1))


@pytest.mark.parametrize('prebarrier',[False,True])
@pytest.mark.parametrize('timeout',[False,True])
def test_quote_bound_through_checkpoint(tmp_path,prebarrier,timeout):
    j,w,q=ready(tmp_path)
    if prebarrier:q=extend(q,120016)
    q=extend(q,120118 if timeout else 120018);b=j.bind_quote_receipts(q);r=run(j,q)
    assert read(w,b['bundle_hash'])==q
    inv=read(w,r['invocation_hash']);assert inv['schema_version']=='p6.quote_invocation.v2' and inv['quote_binding_hash']==b['commit_hash']
    assert inv['quote_receipt_hash']==H(q['receipts'][-1])
    cp=j.prepare_checkpoint(last_receipt_seq=len(q['receipts']),clock=clock(120200));assert read(w,cp['checkpoint_hash'])['last_receipt_seq']==len(q['receipts'])
    assert r['state']['reason_code']==('QUOTE_TIMEOUT' if timeout else None);j.close()


@pytest.mark.parametrize('fault',['old_receipt','plan','prefix','new_bar','missing_extension','early_only','skip_eligible','extra_payload'])
def test_bad_quote_bundle_before_publication(tmp_path,fault):
    j,w,q=ready(tmp_path);b=extend(q);before=j.snapshot()
    if fault=='old_receipt':b['receipts'][0]['raw_payload_hash']='a'*64
    if fault=='plan':b['plan']['owner']='other'
    if fault=='prefix':b['prefix']['segment_hashes']=['a'*64]
    if fault=='new_bar':b['receipts'][-1]['kind']='BAR_FINAL'
    if fault=='missing_extension':b=q
    if fault=='early_only':b=extend(q,120016)
    if fault=='skip_eligible':b=extend(b,120019)
    if fault=='extra_payload':b['payloads']['a'*64]=copy.deepcopy(next(iter(b['payloads'].values())))
    with pytest.raises(ValueError):j.bind_quote_receipts(b)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['objects']==before['objects']


@pytest.mark.parametrize('stage',[1,2,3])
def test_binding_unknown_write_stops_before_fill(tmp_path,monkeypatch,stage):
    j,w,q=ready(tmp_path);q=extend(q);put=w.put;count=0;before=j.snapshot()
    def fail(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==stage:raise RuntimeError('unknown publication')
        return h
    monkeypatch.setattr(w,'put',fail);monkeypatch.setattr(reducer,'quote',lambda *a:pytest.fail('fill invoked'))
    with pytest.raises(RuntimeError):j.bind_quote_receipts(q)
    assert j.snapshot()['event_head_hash']==before['event_head_hash'] and j.snapshot()['lifecycle']=='INCOMPLETE'


@pytest.mark.parametrize('field',['bid','ask','received_utc_ms','received_monotonic_ns','clock_session_id','receipt_hash'])
def test_quote_cannot_substitute_bound_observation(tmp_path,field,monkeypatch):
    j,w,q=ready(tmp_path);q=extend(q);j.bind_quote_receipts(q);o=observation(q);h=H(q['receipts'][-1])
    if field=='receipt_hash':h='a'*64
    elif field=='clock_session_id':o[field]='other'
    elif field in ('bid','ask'):o[field]='102'
    else:o[field]+=1
    monkeypatch.setattr(reducer,'quote',lambda *a:pytest.fail('fill invoked'))
    with pytest.raises(ValueError):j.run_quote(o,quote_receipt_hash=h,clock=clock(120019))


def test_bound_intake_quote_has_no_unverified_fallback(tmp_path):
    j,w,q=ready(tmp_path);q=extend(q)
    with pytest.raises(ValueError):run(j,q)


@pytest.mark.parametrize('seq',[1,3])
def test_checkpoint_requires_exact_quote_sequence(tmp_path,seq):
    j,w,q=ready(tmp_path);q=extend(q);j.bind_quote_receipts(q);run(j,q)
    with pytest.raises(ValueError):j.prepare_checkpoint(last_receipt_seq=seq,clock=clock(120200))


def test_detached_binding_and_single_consumption(tmp_path):
    j,w,q=ready(tmp_path);q=extend(q);expected=copy.deepcopy(q);j.bind_quote_receipts(q);q['receipts'][-1]['received_utc_ms']=0
    run(j,expected)
    with pytest.raises(ValueError):j.bind_quote_receipts(expected)


@pytest.mark.parametrize('utc,mono',[(120016,120018),(120018,120016)])
def test_crossing_barrier_clocks_refused(tmp_path,utc,mono):
    j,w,q=ready(tmp_path);q=extend(q);q['receipts'][-1].update(received_utc_ms=utc,received_monotonic_ns=mono*1000000)
    with pytest.raises(ValueError,match='CLOCK_INVALID'):j.bind_quote_receipts(q)


def test_callback_observes_durable_quote_binding(tmp_path,monkeypatch):
    j,w,q=ready(tmp_path);q=extend(q);b=j.bind_quote_receipts(q);quote=reducer.quote;seen=[]
    def callback(*a):
        assert read(w,b['commit_hash'])['quote_receipt_hash']==H(q['receipts'][-1])
        assert any(json.loads(f.read_bytes()).get('quote_binding_hash')==b['commit_hash'] for f in Path(w.root).glob('*.blob'))
        seen.append(1);return quote(*a)
    monkeypatch.setattr(reducer,'quote',callback);run(j,q);assert seen==[1];j.close()


def test_final_binding_root_swap_refused(tmp_path,monkeypatch):
    j,w,q=ready(tmp_path);q=extend(q);put=w.put;count=0;before=j.snapshot()
    def swap(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==3:
            root=Path(w.root);root.rename(root.with_name('moved'));root.mkdir()
        return h
    monkeypatch.setattr(w,'put',swap)
    with pytest.raises(Exception):j.bind_quote_receipts(q)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_head_hash']==before['event_head_hash']


def test_quote_binding_reentry_refused(tmp_path,monkeypatch):
    j,w,q=ready(tmp_path);q=extend(q);put=w.put
    def reenter(raw,**kw):
        try:j.bind_quote_receipts(q)
        except ValueError:pass
        return put(raw,**kw)
    monkeypatch.setattr(w,'put',reenter)
    with pytest.raises(Exception):j.bind_quote_receipts(q)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_foreign_quote_binding_does_not_poison(tmp_path):
    import threading
    j,w,q=ready(tmp_path);q=extend(q);errors=[]
    def attempt():
        try:j.bind_quote_receipts(q)
        except ValueError as e:errors.append(str(e))
    t=threading.Thread(target=attempt);t.start();t.join()
    assert errors and j.snapshot()['lifecycle']=='OPEN'
    j.bind_quote_receipts(q);run(j,q);j.close()


def test_quote_binding_no_rebind(tmp_path):
    j,w,q=ready(tmp_path);q=extend(q);j.bind_quote_receipts(q)
    with pytest.raises(ValueError):j.bind_quote_receipts(q)


def test_bound_rejection_keeps_receipt_link(tmp_path):
    j,w,q=ready(tmp_path);q=extend(q);e=q['receipts'][-1];p=q['payloads'].pop(e['normalized_payload_hash']);p.update(bid='1000000000000',ask='1000000000000');q['payloads'][H(p)]=p;e['normalized_payload_hash']=H(p)
    j.bind_quote_receipts(q);r=run(j,q);event=read(w,r['event_hashes'][0]);payload=read(w,event['payload_hash'])
    assert event['kind']=='REJECTED' and payload['quote_receipt_hash']==H(e);j.close()
