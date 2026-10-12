import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor
import pytest
pytest.importorskip('cryptography', reason='Install phase6-anchor for signed integration tests')
from test_phase6_start_validation import case
from test_phase6_anchor_validation import sign, digest
from app.research.phase6_object_store import ObjectWriter, inspect_objects
from app.research.phase6_start_attempt import StartBindingAttempt
import app.research.phase6_start_attempt as module


def setup(tmp_path, *, clock=None):
    q = case(tmp_path)
    context = {k: q[k] for k in ('plan', 'authorization', 'reservation', 'genesis', 'registry_identity', 'strategy_state_hash')}
    context['trust'] = q['anchor']['trust']
    writer = ObjectWriter(context['authorization']['evidence_root'], owner=context['plan']['owner'], run_id=digest(context['reservation']), max_bytes=1000000)
    return q, context, writer, clock or (lambda: 950)


def response(attempt, q):
    head = copy.deepcopy(q['anchor']['head'])
    head['nonce'] = attempt.snapshot()['challenge']['nonce']
    return copy.deepcopy(q['anchor']['receipts']), sign(head)


def read_blob(writer, digest_value):
    with open(writer.root + '/' + digest_value + '.blob') as f:
        return json.load(f)


def test_causal_publication_and_complete_result(tmp_path, monkeypatch):
    q, context, writer, clock = setup(tmp_path)
    def nonce(n):
        assert n == 32
        assert read_blob(writer, digest(context['genesis'])) == context['genesis']
        assert read_blob(writer, digest(context)) == context
        return 'd' * 64
    monkeypatch.setattr(module.secrets, 'token_hex', nonce)
    attempt = StartBindingAttempt(writer, context, clock=clock)
    before = attempt.snapshot()
    assert before['lifecycle'] == 'AWAITING_BINDINGS'
    original = module.verify_start_bindings
    def verify(request):
        assert read_blob(writer, digest(request)) == request
        return original(request)
    monkeypatch.setattr(module, 'verify_start_bindings', verify)
    outcome = attempt.finish(*response(attempt, q))
    status = attempt.snapshot()
    assert status['lifecycle'] == 'BOUND_LOCAL' and outcome['binding_status'] == 'VALID'
    assert status['runtime_accepted'] is False and outcome['runtime_accepted'] is False
    result = read_blob(writer, status['result_hash'])
    assert result == dict(schema_version='p6.start_result.v1', challenge_hash=before['challenge_hash'], request_hash=status['request_hash'], validation=outcome)
    assert inspect_objects(writer.root, header=writer.header, objects=status['objects'])['structural_status'] == 'VALID'
    with pytest.raises(ValueError): attempt.finish(*response(attempt, q))
    with pytest.raises(ValueError): ObjectWriter(writer.root, owner=context['plan']['owner'], run_id=digest(context['reservation']), max_bytes=1000000)


@pytest.mark.parametrize('mutation', ['owner','run','root','trust','state','time','extra'])
def test_preflight_refuses_without_claiming(tmp_path, mutation):
    q,c,w,clock = setup(tmp_path)
    if mutation == 'owner': w._header = w._header.replace(c['plan']['owner'].encode(), b'other')
    if mutation == 'run': w._header = w._header.replace(digest(c['reservation']).encode(), b'f'*64)
    if mutation == 'root': c['authorization']['evidence_root'] += '-other'
    if mutation == 'trust': c['trust']['max_head_age_ms'] += 1
    if mutation == 'state': c['strategy_state_hash'] = '8'*64
    if mutation == 'time': clock = lambda: c['plan']['start_ms']
    if mutation == 'extra': c['extra'] = 1
    with pytest.raises(ValueError): StartBindingAttempt(w,c,clock=clock)
    assert os.listdir(w.root) == ['writer.json']
    assert w._claim is None
    w.close()


@pytest.mark.parametrize('bad', [None, True, -1, 2**63, '950'])
def test_bad_initial_clock(tmp_path,bad):
    q,c,w,_=setup(tmp_path)
    with pytest.raises(ValueError): StartBindingAttempt(w,c,clock=lambda:bad)
    assert w._claim is None
    w.close()


def test_detached_context_and_challenge(tmp_path):
    q,c,w,clock=setup(tmp_path);a=StartBindingAttempt(w,c,clock=clock)
    c['genesis'].clear(); c['trust'].clear()
    snapshot=a.snapshot();snapshot['challenge']['nonce']='f'*64;snapshot['objects'].clear()
    assert a.finish(*response(a,q))['binding_status']=='VALID'


def test_wrong_nonce_is_recorded_rejection(tmp_path):
    q,c,w,clock=setup(tmp_path);a=StartBindingAttempt(w,c,clock=clock)
    receipts,head=response(a,q);head['nonce']='b'*64;sign(head)
    result=a.finish(receipts,head)
    assert result['binding_status']=='INVALID' and result['anchor_reason_code']=='IDENTITY_MISMATCH'
    assert a.snapshot()['lifecycle']=='REJECTED'
    with pytest.raises(ValueError):a.finish(receipts,head)


@pytest.mark.parametrize('stage', range(1,9))
def test_publication_unknown_outcome_forbids_retry(tmp_path,monkeypatch,stage):
    q,c,w,clock=setup(tmp_path);original=w.put;calls=0
    def fail(raw,**kw):
        nonlocal calls
        calls+=1
        result=original(raw,**kw)
        if calls==stage:raise RuntimeError('unknown publication')
        return result
    monkeypatch.setattr(w,'put',fail)
    if stage<=6:
        with pytest.raises(RuntimeError,match='unknown publication'):StartBindingAttempt(w,c,clock=clock)
    else:
        a=StartBindingAttempt(w,c,clock=clock)
        with pytest.raises(RuntimeError,match='unknown publication'):a.finish(*response(a,q))
        assert a.snapshot()['lifecycle']=='INCOMPLETE'
        with pytest.raises(ValueError):a.finish(*response(a,q))
    with pytest.raises(ValueError):w.put(b'new')
    assert len(os.listdir(w.root))==stage+1


def test_finish_clock_rollback_terminal(tmp_path):
    times=iter([900,950,949]);q,c,w,_=setup(tmp_path)
    a=StartBindingAttempt(w,c,clock=lambda:next(times))
    with pytest.raises(ValueError,match='CLOCK_INVALID'):a.finish(*response(a,q))
    assert a.snapshot()['lifecycle']=='INCOMPLETE'


def test_clock_callback_reentry_poisoned(tmp_path):
    box={}
    def clock():
        if box:
            with pytest.raises(ValueError):box['attempt'].close()
        return 950
    q,c,w,_=setup(tmp_path);a=StartBindingAttempt(w,c,clock=clock);box['attempt']=a
    with pytest.raises(ValueError):a.finish(*response(a,q))
    assert a.snapshot()['lifecycle']=='INCOMPLETE'


@pytest.mark.parametrize('boundary',['verifier','close','inspection'])
def test_final_boundary_failure_no_ack(tmp_path,monkeypatch,boundary):
    q,c,w,clock=setup(tmp_path);a=StartBindingAttempt(w,c,clock=clock)
    def fail(*args,**kwargs):raise RuntimeError('boundary')
    if boundary=='verifier':monkeypatch.setattr(module,'verify_start_bindings',fail)
    elif boundary=='close':monkeypatch.setattr(w,'close',fail)
    else:monkeypatch.setattr(module,'inspect_objects',fail)
    with pytest.raises(RuntimeError,match='boundary'):a.finish(*response(a,q))
    assert a.snapshot()['lifecycle']=='INCOMPLETE'
    if boundary=='close':ObjectWriter.close(w,claim=w._claim)


def test_reentrant_inspection_cannot_ack(tmp_path,monkeypatch):
    q,c,w,clock=setup(tmp_path);a=StartBindingAttempt(w,c,clock=clock);original=module.inspect_objects
    def inspect(*args,**kwargs):
        with pytest.raises(ValueError):a.close()
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'inspect_objects',inspect)
    with pytest.raises(ValueError):a.finish(*response(a,q))
    assert a.snapshot()['lifecycle']=='INCOMPLETE'


def test_foreign_thread_does_not_poison_owner(tmp_path):
    q,c,w,clock=setup(tmp_path);a=StartBindingAttempt(w,c,clock=clock)
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(ValueError):pool.submit(a.close).result()
    assert a.finish(*response(a,q))['binding_status']=='VALID'


def test_foreign_process_cannot_close_owner(tmp_path):
    q,c,w,clock=setup(tmp_path);a=StartBindingAttempt(w,c,clock=clock)
    pid=os.fork()
    if pid==0:
        try:a.close()
        except ValueError:os._exit(0)
        os._exit(1)
    assert os.waitpid(pid,0)[1]==0
    assert a.finish(*response(a,q))['binding_status']=='VALID'


def test_abandon_terminal_and_root_property_readonly(tmp_path):
    q,c,w,clock=setup(tmp_path);a=StartBindingAttempt(w,c,clock=clock)
    with pytest.raises(AttributeError):w.root='other'
    a.close();assert a.snapshot()['lifecycle']=='CLOSED'
    with pytest.raises(ValueError):a.finish(*response(a,q))


def test_physical_writer_location_must_match_authorization(tmp_path):
    q,c,w,clock=setup(tmp_path)
    other=tmp_path/'unapproved';other.mkdir()
    wrong=ObjectWriter(str(other),owner=c['plan']['owner'],run_id=digest(c['reservation']),max_bytes=1000000)
    with pytest.raises(ValueError,match='WRITER_MISMATCH'):StartBindingAttempt(wrong,c,clock=clock)
    assert wrong._claim is None
    wrong.close();w.close()


@pytest.mark.parametrize('boundary',['nonce','second_clock'])
def test_prepare_callback_failure_preserves_candidate_only(tmp_path,monkeypatch,boundary):
    q,c,w,clock=setup(tmp_path)
    def failure(*args):raise RuntimeError('prepare failure')
    if boundary=='nonce':monkeypatch.setattr(module.secrets,'token_hex',failure)
    else:
        calls=0
        def clock():
            nonlocal calls
            calls+=1
            if calls==2:failure()
            return 950
    with pytest.raises(RuntimeError,match='prepare failure'):StartBindingAttempt(w,c,clock=clock)
    assert len(os.listdir(w.root))==6
    with pytest.raises(ValueError):w.put(b'retry')


def test_response_custom_copy_hooks_never_called(tmp_path):
    q,c,w,clock=setup(tmp_path);a=StartBindingAttempt(w,c,clock=clock)
    class Custom:
        def __deepcopy__(self,*args):raise AssertionError('custom hook')
    with pytest.raises(ValueError,match='INPUT_INVALID'):a.finish(Custom(),None)
    assert a.snapshot()['lifecycle']=='INCOMPLETE'


def test_response_snapshot_precedes_clock_callback(tmp_path):
    box={}
    def clock():
        if box:box['head']['signature']='0'*128
        return 950
    q,c,w,_=setup(tmp_path);a=StartBindingAttempt(w,c,clock=clock)
    receipts,head=response(a,q);box['head']=head
    assert a.finish(receipts,head)['binding_status']=='VALID'
    assert head['signature']=='0'*128


def test_reentrant_publication_cannot_ack(tmp_path,monkeypatch):
    q,c,w,clock=setup(tmp_path);a=StartBindingAttempt(w,c,clock=clock);original=w.put
    def put(raw,**kw):
        digest_value=original(raw,**kw)
        with pytest.raises(ValueError):a.finish([],None)
        return digest_value
    monkeypatch.setattr(w,'put',put)
    with pytest.raises(ValueError):a.finish(*response(a,q))
    assert a.snapshot()['lifecycle']=='INCOMPLETE' and a.snapshot()['result_hash'] is None


def test_root_replacement_before_finish_refused(tmp_path):
    q,c,w,clock=setup(tmp_path);a=StartBindingAttempt(w,c,clock=clock)
    os.rename(w.root,w.root+'-preserved');os.mkdir(w.root)
    with pytest.raises(ValueError):a.finish(*response(a,q))
    assert a.snapshot()['lifecycle']=='INCOMPLETE'


def test_result_mutation_does_not_change_persisted_evidence(tmp_path):
    q,c,w,clock=setup(tmp_path);a=StartBindingAttempt(w,c,clock=clock)
    result=a.finish(*response(a,q));result['runtime_accepted']=True
    assert read_blob(w,a.snapshot()['result_hash'])['validation']['runtime_accepted'] is False
