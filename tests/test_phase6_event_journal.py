import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_phase6_event_validation import case,payload,KINDS
from app.research.phase6_validation import canonical_hash as H
from app.research.phase6_object_store import ObjectWriter,inspect_objects
from app.research.phase6_event_journal import EventJournal
import app.research.phase6_event_journal as module


def setup(tmp_path):
    root=tmp_path/'journal';root.mkdir();m=case()['manifest']
    writer=ObjectWriter(str(root),owner='synthetic',run_id=m['run_id'],max_bytes=1000000)
    return writer,m


def start(tmp_path):
    w,m=setup(tmp_path);j=EventJournal(w,m,utc_ms=20,monotonic_ns=200)
    return j,w,m


def read(w,h):
    return json.loads((__import__('pathlib').Path(w.root)/(h+'.blob')).read_bytes())


def test_typed_chain_causal_publication_and_readonly_inspection(tmp_path,monkeypatch):
    w,m=setup(tmp_path);original=w.put;publications=[]
    def put(raw,**kw):
        doc=json.loads(raw)
        if doc.get('schema_version')=='p6.event.v1':
            assert read(w,doc['payload_hash'])
            assert read(w,H(m))==m
        publications.append(doc);return original(raw,**kw)
    monkeypatch.setattr(w,'put',put)
    j=EventJournal(w,m,utc_ms=20,monotonic_ns=200)
    for seq,kind in enumerate(KINDS[1:],2):
        previous=j.snapshot()['event_head_hash']
        h=j.append(kind,payload(kind),utc_ms=20+seq,monotonic_ns=200+seq)
        event=read(w,h)
        assert event['event_seq']==seq and event['previous_event_hash']==previous
    s=j.snapshot();assert s['event_count']==10 and not s['runtime_accepted']
    assert publications[0]==m
    j.close();assert j.snapshot()['lifecycle']=='CLOSED'
    assert inspect_objects(w.root,header=w.header,objects=s['objects'])['structural_status']=='VALID'
    with pytest.raises(ValueError):j.append('STOP',payload('STOP'),utc_ms=100,monotonic_ns=1000)
    with pytest.raises(ValueError):ObjectWriter(w.root,owner='synthetic',run_id=m['run_id'],max_bytes=1000000)


@pytest.mark.parametrize('field,value',[('run_id','f'*64),('extra',1),('persisted_utc_ms',21)])
def test_preflight_does_not_consume_writer(tmp_path,field,value):
    w,m=setup(tmp_path);m[field]=value
    with pytest.raises(ValueError):EventJournal(w,m,utc_ms=20,monotonic_ns=200)
    assert w._claim is None and os.listdir(w.root)==['writer.json'];w.close()


def test_snapshot_and_manifest_detached(tmp_path):
    j,w,m=start(tmp_path);m.clear();s=j.snapshot();s['last_event'].clear();s['objects'].clear()
    h=j.append('STOP',payload('STOP'),utc_ms=21,monotonic_ns=210)
    assert read(w,h)['event_seq']==2;j.close()


@pytest.mark.parametrize('bad',['unknown_kind','bad_payload','clock','repeat_manifest'])
def test_refused_append_terminal(tmp_path,bad):
    j,w,m=start(tmp_path);kind='STOP';p=payload(kind);utc=21
    if bad=='unknown_kind':kind='unknown'
    if bad=='bad_payload':p['unexpected']=1
    if bad=='clock':utc=19
    if bad=='repeat_manifest':kind='MANIFEST_VERIFIED';p=payload(kind);p['manifest_hash']=H(m)
    with pytest.raises(ValueError):j.append(kind,p,utc_ms=utc,monotonic_ns=210)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_count']==1
    assert len(os.listdir(w.root))==4
    with pytest.raises(ValueError):j.append('STOP',payload('STOP'),utc_ms=30,monotonic_ns=300)


@pytest.mark.parametrize('stage',[1,2,3,4,5])
def test_unknown_publication_never_retried(tmp_path,monkeypatch,stage):
    w,m=setup(tmp_path);original=w.put;calls=0
    def put(raw,**kw):
        nonlocal calls
        calls+=1;digest=original(raw,**kw)
        if calls==stage:raise RuntimeError('unknown publication')
        return digest
    monkeypatch.setattr(w,'put',put)
    if stage<=3:
        with pytest.raises(RuntimeError):EventJournal(w,m,utc_ms=20,monotonic_ns=200)
    else:
        j=EventJournal(w,m,utc_ms=20,monotonic_ns=200)
        with pytest.raises(RuntimeError):j.append('STOP',payload('STOP'),utc_ms=21,monotonic_ns=210)
        assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_count']==1
    assert len(os.listdir(w.root))==stage+1
    with pytest.raises(ValueError):w.put(b'retry')


def test_reentrant_append_poisoned(tmp_path,monkeypatch):
    j,w,m=start(tmp_path);original=w.put
    def put(raw,**kw):
        digest=original(raw,**kw)
        with pytest.raises(ValueError):j.close()
        return digest
    monkeypatch.setattr(w,'put',put)
    with pytest.raises(ValueError):j.append('STOP',payload('STOP'),utc_ms=21,monotonic_ns=210)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_count']==1


@pytest.mark.parametrize('boundary',['close','inspect','reentry'])
def test_final_verification_failure_no_closed_ack(tmp_path,monkeypatch,boundary):
    j,w,m=start(tmp_path)
    def fail(*args,**kw):raise RuntimeError('final failure')
    if boundary=='close':monkeypatch.setattr(w,'close',fail)
    elif boundary=='inspect':monkeypatch.setattr(module,'inspect_objects',fail)
    else:
        original=module.inspect_objects
        def inspect(*args,**kw):
            with pytest.raises(ValueError):j.close()
            return original(*args,**kw)
        monkeypatch.setattr(module,'inspect_objects',inspect)
    with pytest.raises((RuntimeError,ValueError)):j.close()
    assert j.snapshot()['lifecycle']=='INCOMPLETE'
    if boundary=='close':ObjectWriter.close(w,claim=w._claim)


def test_foreign_thread_and_process_refuse_without_poison(tmp_path):
    j,w,m=start(tmp_path)
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(ValueError):pool.submit(j.close).result()
    pid=os.fork()
    if pid==0:
        try:j.close()
        except ValueError:os._exit(0)
        os._exit(1)
    assert os.waitpid(pid,0)[1]==0
    j.close();assert j.snapshot()['lifecycle']=='CLOSED'


def test_root_substitution_refused(tmp_path):
    j,w,m=start(tmp_path);os.rename(w.root,w.root+'-preserved');os.mkdir(w.root)
    with pytest.raises(ValueError):j.append('STOP',payload('STOP'),utc_ms=21,monotonic_ns=210)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_last_publication_root_swap_cannot_ack(tmp_path,monkeypatch):
    j,w,m=start(tmp_path);original=w.put
    def put(raw,**kw):
        h=original(raw,**kw)
        if json.loads(raw)['schema_version']=='p6.event.v1':
            os.rename(w.root,w.root+'-preserved');os.mkdir(w.root)
        return h
    monkeypatch.setattr(w,'put',put)
    with pytest.raises(ValueError):j.append('STOP',payload('STOP'),utc_ms=21,monotonic_ns=210)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_count']==1


def test_external_mutation_during_validation_cannot_change_stored_bytes(tmp_path,monkeypatch):
    j,w,m=start(tmp_path);p=payload('STOP');original=module.verify_event
    def verify(request):
        result=original(request);p.clear();return result
    monkeypatch.setattr(module,'verify_event',verify)
    h=j.append('STOP',p,utc_ms=21,monotonic_ns=210)
    assert read(w,read(w,h)['payload_hash'])['reason_code']=='OPERATOR_STOP';j.close()


def test_stop_is_only_typed_event_not_run_completion(tmp_path):
    j,w,m=start(tmp_path)
    j.append('STOP',payload('STOP'),utc_ms=21,monotonic_ns=210)
    assert j.snapshot()['lifecycle']=='OPEN'
    j.append('CHECKPOINT_PREPARED',payload('CHECKPOINT_PREPARED'),utc_ms=22,monotonic_ns=220)
    j.close();assert j.snapshot()['lifecycle']=='CLOSED'


def test_strategy_and_reducer_derived_payloads_persist(tmp_path):
    from app.paper.session import diagnostic_strategy_v1 as strategy,shadow_reducer_v1 as reducer
    from test_phase6_shadow_reducer import config,bar,quote
    from fractions import Fraction
    j,w,m=start(tmp_path)
    p=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=0,targets=['0.25','0.75'])
    initial=strategy.initial(p);out=strategy.advance(initial,p,segment_seq=1,segment_hash=m['segment_id'])
    decision=payload('DECISION');decision.update(strategy_input_hash=H(initial),strategy_output_hash=H(out['state']),target=out['target'])
    dh=j.append('DECISION',decision,utc_ms=21,monotonic_ns=210)
    before=reducer.bar(reducer.initial(config()),bar(),out['target'])
    intent=payload('ORDER_INTENT');intent.update(decision_event_hash=dh,side=before['pending']['side'],quantity=before['pending']['quantity'])
    ih=j.append('ORDER_INTENT',intent,utc_ms=22,monotonic_ns=220)
    after=reducer.quote(before,quote())
    fill=payload('SIMULATED_FILL');fill.update(intent_event_hash=ih,quantity=after['units'],price=str(Fraction(after['cost_basis'])/Fraction(after['units'])),fee=after['cumulative_fees'])
    fh=j.append('SIMULATED_FILL',fill,utc_ms=23,monotonic_ns=230)
    assert read(w,read(w,fh)['payload_hash'])==fill
    j.close()


def test_process_exit_preserves_partial_constructor_and_blocks_reopen(tmp_path):
    root=tmp_path/'process';root.mkdir();m=case()['manifest']
    pid=os.fork()
    if pid==0:
        w=ObjectWriter(str(root),owner='synthetic',run_id=m['run_id'],max_bytes=1000000)
        original=w.put
        def put(raw,**kw):
            h=original(raw,**kw)
            if json.loads(raw).get('schema_version')=='p6.payload.manifest_verified.v1':os._exit(17)
            return h
        w.put=put
        EventJournal(w,m,utc_ms=20,monotonic_ns=200)
        os._exit(1)
    assert os.waitpid(pid,0)[1]==17*256
    assert len(os.listdir(root))==3
    with pytest.raises(ValueError):ObjectWriter(str(root),owner='synthetic',run_id=m['run_id'],max_bytes=1000000)


def test_constructor_reentry_during_publication_is_terminal(tmp_path,monkeypatch):
    w,m=setup(tmp_path);original=module.EventJournal._publish
    def publish(self,raw):
        h=original(self,raw)
        with pytest.raises(ValueError):self.close()
        return h
    monkeypatch.setattr(module.EventJournal,'_publish',publish)
    with pytest.raises(ValueError):EventJournal(w,m,utc_ms=20,monotonic_ns=200)
    with pytest.raises(ValueError):w.put(b'retry')
