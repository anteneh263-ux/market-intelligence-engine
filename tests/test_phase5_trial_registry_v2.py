import copy
import hashlib
import json
import sqlite3
from pathlib import Path
import pytest
from app.research.trial_registry_v2 import initialize_trial_registry, execute_trial
from app.research.phase5_identity_contract import validate_experiment_contract
from test_phase5_contract_binding import request as identity_request


def digest(x):
    return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


@pytest.fixture
def store(tmp_path):
    root=tmp_path.resolve();r=root/'registry';a=root/'anchor'
    initialize_trial_registry(str(r),str(a))
    return r,a


@pytest.fixture
def reservation_request():
    c=validate_experiment_contract(identity_request('CONTINUOUS_24_7'))
    return dict(mode='RESERVATION',research_program_id=c['research_program_id'],experiment_id=c['experiment_id'],
        contract_hash=c['contract_hash'],contract_record=c,attempt_fingerprint_record=dict(
            research_program_id=c['research_program_id'],experiment_id=c['experiment_id'],
            ordered_input_hashes=['a'*64],method_hash='b'*64,intended_dataset_zone='TRAIN'))


def call(store,q):
    result=execute_trial(q,*map(str,store))
    assert set(result)=={'schema_version','mode','counter_status','counter_incremented','N_total',
        'ledger_chain_hash','checks','reason_code','result','ledger_report_hash'}
    assert result['ledger_report_hash']==digest({k:v for k,v in result.items() if k!='ledger_report_hash'})
    states=[x['status'] for x in result['checks']]
    if 'FAIL' in states:
        pos=states.index('FAIL');assert states[:pos]==['PASS']*pos
        assert states[pos+1:]==['NOT_EVALUATED']*(len(states)-pos-1)
        assert result['result'] is None
    return result


def state(store,program):return call(store,dict(mode='REGISTRY_STATE',research_program_id=program))


def test_fresh_state_and_v1_refusal(store):
    assert state(store,'a'*64)['N_total']==0
    from app.research.registry_inspection import inspect_registry
    assert inspect_registry(*map(str,store))['inspection_status']=='REFUSED'


def test_reserve_repeated_fingerprint_counts(store,reservation_request):
    q=reservation_request;before=copy.deepcopy(q)
    a=call(store,q);b=call(store,q)
    assert a['counter_status']==b['counter_status']=='COMMITTED'
    assert a['N_total']==1 and b['N_total']==2
    assert a['result']['trial_id']!=b['result']['trial_id']
    assert a['result']['repeated_attempt_fingerprint'] is False
    assert b['result']['repeated_attempt_fingerprint'] is True
    assert state(store,q['research_program_id'])['result']['reservation_count']==2
    assert q==before


def test_outcome_and_replay(store,reservation_request):
    q=reservation_request;a=call(store,q);trial=a['result']['trial_id']
    out=dict(mode='OUTCOME_APPEND',research_program_id=q['research_program_id'],trial_id=trial,
             trial_outcome='COMPLETED_VALID',result_hash='c'*64)
    b=call(store,out)
    assert b['counter_status']=='VERIFIED' and b['counter_incremented'] is False and b['N_total']==1
    assert b['ledger_chain_hash']!=a['ledger_chain_hash']
    assert call(store,out)['reason_code']=='OUTCOME_ALREADY_FINAL'
    replay=dict(mode='VERIFICATION_REPLAY',research_program_id=q['research_program_id'],
        verification_replay_record=dict(replay_of_trial_id=trial,original_input_hashes=['a'*64],
        original_method_hash='b'*64,original_result_hash='c'*64),performance_data_accessed=False,
        append_reservation_requested=False)
    before={p.name:p.read_bytes() for p in store[0].parent.iterdir()}
    r=call(store,replay);assert r['counter_status']=='REPLAY_VERIFIED'
    assert r['N_total']==1 and r['counter_incremented'] is False
    assert before=={p.name:p.read_bytes() for p in store[0].parent.iterdir()}
    replay['performance_data_accessed']=True
    assert call(store,replay)['reason_code']=='REPLAY_DATA_ACCESS_FORBIDDEN'


def test_timing_integrated_with_persisted_trial_zone(store,reservation_request):
    q=reservation_request;a=call(store,q);c=q['contract_record']
    opened=c['boundaries']['train']['start_ms'];d=c['dataset_identity']['timeframe_ms']
    r=dict(bar_open_ms=opened,bar_available_ms=opened+d,feature_observed_ms=opened+d,
           signal_decision_ms=opened+d,order_submitted_ms=opened+d,fill_ms=opened+d)
    timing={k:q[k] for k in ('research_program_id','experiment_id','contract_hash','contract_record')}
    timing.update(mode='TIMING_VALIDATION',trial_id=a['result']['trial_id'],timing_ledger_rows=[r])
    assert call(store,timing)['result']['timing_rows_status']=='VALID'
    timing['timing_ledger_rows'][0]['bar_open_ms']=c['boundaries']['val']['start_ms']
    assert call(store,timing)['reason_code']=='TIMING_CONTRACT_MISMATCH'


def test_preflight_does_not_open_registry(tmp_path,reservation_request):
    q=copy.deepcopy(reservation_request);q['trial_id']='f'*64
    result=call((tmp_path/'absent',tmp_path/'noanchor'),q)
    assert result['reason_code']=='CALLER_SUPPLIED_RESERVATION_FIELD'
    assert not list(tmp_path.iterdir())


def test_pending_publication_failure_keeps_count_and_next_writer_recovers(store,reservation_request,monkeypatch):
    import app.research.trial_registry_v2 as module
    real=module._publish
    monkeypatch.setattr(module,'_publish',lambda *a:(_ for _ in ()).throw(OSError('synthetic publication failure')))
    a=call(store,reservation_request)
    assert a['counter_status']=='COMMITTED' and a['counter_incremented'] is True
    assert state(store,reservation_request['research_program_id'])['reason_code']=='COUNTER_NONMONOTONIC'
    monkeypatch.setattr(module,'_publish',real)
    b=call(store,reservation_request)
    assert b['counter_status']=='COMMITTED' and b['N_total']==2
    assert state(store,reservation_request['research_program_id'])['N_total']==2


def test_metadata_immutability_and_state_pending(store):
    with sqlite3.connect(str(store[0])) as c:
        with pytest.raises(sqlite3.IntegrityError):c.execute('DELETE FROM registry_meta')
        assert c.execute("SELECT meta_value FROM registry_meta WHERE meta_key='registry_schema_version'").fetchone()[0]=='wp5_0a2.registry.v2'


def test_corruption_never_publishes_counts(store,reservation_request):
    call(store,reservation_request)
    with sqlite3.connect(str(store[0])) as c:
        c.execute('DROP TRIGGER trial_ledger_no_update')
    result=state(store,reservation_request['research_program_id'])
    assert result['counter_status']=='INVALID' and result['N_total'] is None


@pytest.mark.parametrize('raw',['','{','{}','null'])
def test_malformed_pending_never_accepted(store,reservation_request,raw):
    with sqlite3.connect(str(store[0])) as c:c.execute('INSERT INTO registry_state VALUES (?,?)',('pending_anchor_update',raw))
    for q in (reservation_request,dict(mode='REGISTRY_STATE',research_program_id=reservation_request['research_program_id'])):
        r=call(store,q)
        assert r['reason_code']=='REGISTRY_SCHEMA_UNSUPPORTED'
        assert next(x for x in r['checks'] if x['check_id']=='REGISTRY_OPEN')['status']=='FAIL'
        assert r['N_total'] is None


def test_error_after_commit_does_not_invite_duplicate_retry(store,reservation_request,monkeypatch):
    import app.research.trial_registry_v2 as module
    from urllib.parse import quote
    real=sqlite3.connect
    class Connection(sqlite3.Connection):
        inserted=False
        def execute(self,statement,*args):
            result=super().execute(statement,*args)
            if statement.startswith('INSERT INTO trial_ledger'):self.inserted=True
            if statement=='COMMIT' and self.inserted:
                self.inserted=False
                raise sqlite3.OperationalError('synthetic exception AFTER durable commit')
            return result
    def connect(path,mode):return real('file:'+quote(path,safe='/')+'?mode='+mode,uri=True,isolation_level=None,timeout=0,factory=Connection)
    monkeypatch.setattr(module.base,'_connect',connect)
    r=call(store,reservation_request)
    assert r['counter_status']=='COMMITTED' and r['counter_incremented'] is True and r['N_total']==1
    assert state(store,reservation_request['research_program_id'])['N_total']==1


def test_already_applied_pending_recovery_does_not_replace_anchor_twice(store,reservation_request,monkeypatch):
    import app.research.trial_registry_v2 as module
    real=module._clear
    monkeypatch.setattr(module,'_clear',lambda *a:(_ for _ in ()).throw(sqlite3.OperationalError('clear failure')))
    first=call(store,reservation_request);assert first['counter_status']=='COMMITTED'
    doc=json.loads(store[1].read_bytes());assert doc['document_sequence']==2
    assert state(store,reservation_request['research_program_id'])['reason_code']=='COUNTER_NONMONOTONIC'
    monkeypatch.setattr(module,'_clear',real)
    second=call(store,reservation_request);assert second['N_total']==2
    assert json.loads(store[1].read_bytes())['document_sequence']==3


def test_exact_pending_delete_rejects_substitution(store):
    import app.research.trial_registry_v2 as module
    with sqlite3.connect(str(store[0]),isolation_level=None) as c:
        c.execute("INSERT INTO registry_state VALUES ('pending_anchor_update','different')")
        with pytest.raises(module.base._Refused):module._clear(c,'verified-original')
        assert c.execute("SELECT state_value FROM registry_state WHERE state_key='pending_anchor_update'").fetchone()[0]=='different'


def test_reader_and_writer_busy_under_exclusive_mutex(store,reservation_request):
    lock=sqlite3.connect(str(store[1])+'.lock',isolation_level=None)
    try:
        lock.execute('BEGIN EXCLUSIVE')
        for q in (reservation_request,dict(mode='REGISTRY_STATE',research_program_id=reservation_request['research_program_id'])):
            r=call(store,q);assert r['reason_code']=='REGISTRY_BUSY' and r['N_total'] is None
    finally:lock.close()
    assert state(store,reservation_request['research_program_id'])['N_total']==0


@pytest.mark.parametrize('boundary',['publish','clear'])
def test_process_crash_after_durable_row_recovers_without_lost_count(store,reservation_request,boundary):
    import subprocess,sys,os
    script="""
import json,os,sys
from app.research import trial_registry_v2 as m
setattr(m, '_publish' if sys.argv[4]=='publish' else '_clear',lambda *args:os._exit(73))
m.execute_trial(json.loads(sys.argv[3]),sys.argv[1],sys.argv[2])
"""
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    process=subprocess.run([sys.executable,'-c',script,str(store[0]),str(store[1]),json.dumps(reservation_request),boundary],
        env=env,capture_output=True,timeout=15)
    assert process.returncode==73,process.stderr.decode()
    assert state(store,reservation_request['research_program_id'])['reason_code']=='COUNTER_NONMONOTONIC'
    r=call(store,reservation_request)
    assert r['N_total']==2 and r['counter_status']=='COMMITTED'
    assert state(store,reservation_request['research_program_id'])['N_total']==2


def test_cooperative_writer_journal_is_busy_not_corrupt(store,reservation_request,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import app.research.trial_registry_v2 as module
    ready=threading.Event();release=threading.Event();original=module._append
    def paused(connection,*args):
        connection.execute("INSERT INTO registry_state VALUES ('synthetic_barrier','uncommitted')")
        ready.set()
        assert release.wait(5)
        connection.execute("DELETE FROM registry_state WHERE state_key='synthetic_barrier'")
        return original(connection,*args)
    monkeypatch.setattr(module,'_append',paused)
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker=executor.submit(call,store,reservation_request)
        try:
            assert ready.wait(5)
            assert Path(str(store[0])+'-journal').exists()
            result=state(store,reservation_request['research_program_id'])
            assert result['reason_code']=='REGISTRY_BUSY'
        finally:release.set()
        assert worker.result(timeout=5)['counter_status']=='COMMITTED'
    assert state(store,reservation_request['research_program_id'])['N_total']==1


def test_precommit_insert_failure_rolls_back_without_count(store,reservation_request,monkeypatch):
    import app.research.trial_registry_v2 as module
    real=module._append
    def broken(connection,*args):
        connection.execute("INSERT INTO registry_state VALUES ('synthetic_uncommitted','x')")
        raise sqlite3.OperationalError('injected precommit')
    monkeypatch.setattr(module,'_append',broken)
    result=call(store,reservation_request)
    assert result['counter_status']=='INVALID' and result['counter_incremented'] is False
    monkeypatch.setattr(module,'_append',real)
    assert state(store,reservation_request['research_program_id'])['N_total']==0
    with sqlite3.connect(str(store[0])) as c:
        assert c.execute("SELECT state_value FROM registry_state WHERE state_key='synthetic_uncommitted'").fetchone() is None


def test_cross_program_recovery_preserves_other_anchor_entries(store,reservation_request,monkeypatch):
    import app.research.trial_registry_v2 as module
    one=call(store,reservation_request);p=reservation_request['research_program_id']
    before=json.loads(store[1].read_bytes())['programs'][p]
    raw=identity_request('CONTINUOUS_24_7');raw['research_scope']['hypothesis_family_hash']='9'*64
    c=validate_experiment_contract(raw);q=copy.deepcopy(reservation_request)
    q.update(research_program_id=c['research_program_id'],experiment_id=c['experiment_id'],contract_hash=c['contract_hash'],contract_record=c)
    q['attempt_fingerprint_record'].update(research_program_id=c['research_program_id'],experiment_id=c['experiment_id'])
    real=module._publish
    monkeypatch.setattr(module,'_publish',lambda *a:(_ for _ in ()).throw(OSError('publication failure')))
    assert call(store,q)['N_total']==1
    monkeypatch.setattr(module,'_publish',real)
    assert call(store,q)['N_total']==2
    assert json.loads(store[1].read_bytes())['programs'][p]==before
    assert state(store,p)['N_total']==1


@pytest.mark.parametrize('field,value,reason',[
    ('target_chain_high_water',100,'LEDGER_CHAIN_BROKEN'),
    ('operation_type','OUTCOME_APPEND','LEDGER_CHAIN_BROKEN'),
    ('target_reservation_high_water',100,'COUNTER_NONMONOTONIC'),
    ('target_document_sequence',100,'COUNTER_NONMONOTONIC')])
def test_rehashed_pending_semantic_tampering_is_refused(store,reservation_request,monkeypatch,field,value,reason):
    import app.research.trial_registry_v2 as module
    real=module._publish
    monkeypatch.setattr(module,'_publish',lambda *a:(_ for _ in ()).throw(OSError('publication failure')))
    call(store,reservation_request);monkeypatch.setattr(module,'_publish',real)
    with sqlite3.connect(str(store[0])) as c:
        pending=json.loads(c.execute("SELECT state_value FROM registry_state WHERE state_key='pending_anchor_update'").fetchone()[0])
        pending[field]=value;pending['pending_hash']=digest({k:v for k,v in pending.items() if k!='pending_hash'})
        c.execute("UPDATE registry_state SET state_value=? WHERE state_key='pending_anchor_update'",(json.dumps(pending,sort_keys=True,separators=(',',':')),))
    before={p.name:p.read_bytes() for p in store[0].parent.iterdir()}
    r=call(store,reservation_request);assert r['reason_code']==reason and r['N_total'] is None
    assert before=={p.name:p.read_bytes() for p in store[0].parent.iterdir()}


def test_nonexistent_trial_fails_after_verified_chain(store,reservation_request):
    q=dict(mode='OUTCOME_APPEND',research_program_id=reservation_request['research_program_id'],trial_id='0'*64,
           trial_outcome='CRASHED',result_hash='a'*64)
    r=call(store,q)
    assert r['reason_code']=='TRIAL_TARGET_NOT_FOUND' and r['N_total']==0


def test_v1_store_is_not_mutated_or_migrated(tmp_path):
    from app.research.phase5_trial_registry import initialize_registry
    root=tmp_path.resolve();store=(root/'registry',root/'anchor');initialize_registry(*map(str,store))
    before={p.name:p.read_bytes() for p in root.iterdir()}
    r=state(store,'a'*64)
    assert r['reason_code']=='REGISTRY_SCHEMA_UNSUPPORTED'
    assert before=={p.name:p.read_bytes() for p in root.iterdir()}


def test_cleanup_rollback_failure_preserves_durable_success(store,reservation_request,monkeypatch):
    import app.research.trial_registry_v2 as module
    from urllib.parse import quote
    real=sqlite3.connect
    class Connection(sqlite3.Connection):
        def rollback(self):raise sqlite3.OperationalError('cleanup rollback failed')
    def connect(path,mode):return real('file:'+quote(path,safe='/')+'?mode='+mode,uri=True,isolation_level=None,timeout=0,factory=Connection)
    def bad_clear(connection,raw):
        connection.execute('BEGIN IMMEDIATE')
        raise sqlite3.OperationalError('pending clear failed')
    monkeypatch.setattr(module.base,'_connect',connect);monkeypatch.setattr(module,'_clear',bad_clear)
    result=call(store,reservation_request)
    assert result['counter_status']=='COMMITTED' and result['counter_incremented'] is True and result['N_total']==1
    with real(str(store[0])) as c:assert c.execute('SELECT COUNT(*) FROM trial_ledger').fetchone()[0]==1


def test_unresolvable_commit_never_returns_safe_retry_envelope(store,reservation_request,monkeypatch):
    import app.research.trial_registry_v2 as module
    from urllib.parse import quote
    real=sqlite3.connect
    class Connection(sqlite3.Connection):
        inserted=False
        def execute(self,statement,*args):
            result=super().execute(statement,*args)
            if statement.startswith('INSERT INTO trial_ledger'):self.inserted=True
            if statement=='COMMIT' and self.inserted:
                self.close()
                raise sqlite3.OperationalError('connection lost after commit')
            return result
    def connect(path,mode):return real('file:'+quote(path,safe='/')+'?mode='+mode,uri=True,isolation_level=None,timeout=0,factory=Connection)
    monkeypatch.setattr(module.base,'_connect',connect)
    with pytest.raises(module.CommitOutcomeUnknown,match='do not retry'):
        call(store,reservation_request)
    with real(str(store[0])) as c:assert c.execute('SELECT COUNT(*) FROM trial_ledger').fetchone()[0]==1


def test_close_error_after_commit_preserves_durable_success(store,reservation_request,monkeypatch):
    import app.research.trial_registry_v2 as module
    from urllib.parse import quote
    real=sqlite3.connect
    class Connection(sqlite3.Connection):
        def close(self):
            super().close()
            raise sqlite3.OperationalError('error after close')
    def connect(path,mode):
        kw=dict(uri=True,isolation_level=None,timeout=0)
        if path==str(store[0]):kw['factory']=Connection
        return real('file:'+quote(path,safe='/')+'?mode='+mode,**kw)
    monkeypatch.setattr(module.base,'_connect',connect)
    r=call(store,reservation_request)
    assert r['counter_status']=='COMMITTED' and r['counter_incremented'] is True and r['reason_code'] is None
