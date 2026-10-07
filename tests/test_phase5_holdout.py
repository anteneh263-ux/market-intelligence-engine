import json
import os
import sqlite3
import subprocess
import sys
import threading
import pytest
from app.research.holdout import (initialize_holdout_store,run_holdout,verify_holdout_report,
    recover_holdout_store,reservation_key,append_recovery_note)
from app.research.registry_inspection import _hash,_canonical


@pytest.fixture
def setup(tmp_path):
    root=tmp_path.resolve();initialize_holdout_store(root)
    data=dict(net=[.01,-.005,.02]*10)
    q=dict(strategy_identity='a'*64,research_program_id='b'*64,experiment_id='c'*64,trial_id='d'*64,
        holdout_dataset_hash=_hash(data),parameters={'direction':1},param_hash=_hash({'direction':1}),
        decision_rule=dict(min_total_return=0,max_drawdown=.5),actor='synthetic-test')
    return root,q,data


def run(setup,**kwargs):
    root,q,data=setup
    args=dict(verify_prerequisites=lambda q:None,read_data=lambda:data,evaluate=lambda data,params:data['net'])
    args.update(kwargs)
    return run_holdout(q,directory=root,**args)


def test_first_evaluation_and_verification_replay(setup):
    root,q,data=setup;report=run(setup)
    assert report['holdout_status']=='HOLDOUT_PASSED'
    before={p.name:p.read_bytes() for p in root.iterdir()}
    assert verify_holdout_report(root,reservation_key(q))==report
    assert before=={p.name:p.read_bytes() for p in root.iterdir()}
    assert report['evaluation_ordinal']==1


def test_new_experiment_cannot_reuse_strategy_dataset(setup):
    run(setup);root,q,data=setup;q['experiment_id']='e'*64;q['trial_id']='f'*64
    result=run(setup,read_data=lambda:pytest.fail('second read'))
    assert result['reason_code']=='HOLDOUT_ALREADY_CONSUMED'


def test_prerequisites_fail_before_reservation(setup):
    def reject(q):raise ValueError('PREREQ_NOT_MET')
    result=run(setup,verify_prerequisites=reject,read_data=lambda:pytest.fail('opened'))
    assert result['reason_code']=='PREREQ_NOT_MET'
    assert not list(setup[0].glob('row-*'))


def test_concurrent_callers_only_one_reads(setup):
    entered=threading.Event();release=threading.Event();results=[];reads=[]
    def reader():reads.append(1);entered.set();assert release.wait(5);return setup[2]
    worker=threading.Thread(target=lambda:results.append(run(setup,read_data=reader)))
    worker.start();assert entered.wait(5)
    other=run(setup,read_data=lambda:pytest.fail('concurrent read'))
    release.set();worker.join(5)
    assert not worker.is_alive() and len(reads)==1
    assert other['reason_code']=='HOLDOUT_ALREADY_RESERVED'
    assert results[0]['holdout_status']=='HOLDOUT_PASSED'


@pytest.mark.parametrize('when',['reserved','consumed'])
def test_process_crash_never_restores_available(setup,when):
    root,q,data=setup;payload=root/'synthetic-input.json';payload.write_text(json.dumps(q))
    script='''
import os,json,sys
import app.research.holdout as module
q=json.load(open(sys.argv[2]));when=sys.argv[3]
original=module._append
def append(*args,**kwargs):
 row=original(*args,**kwargs)
 if when=='reserved' and row['to_state']=='RESERVED':os._exit(61)
 return row
module._append=append
def read():os._exit(62)
module.run_holdout(q,directory=sys.argv[1],verify_prerequisites=lambda q:None,read_data=read,evaluate=lambda *args:[])
'''
    proc=subprocess.run([sys.executable,'-c',script,str(root),str(payload),when],
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),timeout=20)
    assert proc.returncode==(61 if when=='reserved' else 62)
    states=recover_holdout_store(root)
    assert states[reservation_key(q)]==('BLOCKED' if when=='reserved' else 'CONSUMED')
    result=run(setup,read_data=lambda:pytest.fail('reopened'))
    assert result['reason_code']==('HOLDOUT_BLOCKED' if when=='reserved' else 'HOLDOUT_ALREADY_CONSUMED')


def test_wrong_dataset_consumes_attempt(setup):
    result=run(setup,read_data=lambda:{'net':[99]})
    assert result['reason_code']=='HOLDOUT_DATASET_INVALID'
    assert recover_holdout_store(setup[0])[reservation_key(setup[1])]=='CONSUMED'


def test_parameter_mutation_consumes_attempt(setup):
    def evaluate(data,params):params['direction']=-1;return data['net']
    assert run(setup,evaluate=evaluate)['reason_code']=='POST_ACCESS_MUTATION'
    assert recover_holdout_store(setup[0])[reservation_key(setup[1])]=='CONSUMED'


def test_manual_note_preserves_state_ordinal_and_replay(setup):
    root,q,data=setup;first=run(setup)
    append_recovery_note(root,reservation_key(q),actor='operator',note='Synthetic verification note.')
    assert verify_holdout_report(root,reservation_key(q))==first
    assert run(setup)['reason_code']=='HOLDOUT_ALREADY_CONSUMED'


def test_row_deletion_and_receipt_orphan_refuse(setup):
    root,q,data=setup;run(setup)
    with sqlite3.connect(root/'access.sqlite') as conn:
        with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM access_ledger')
        conn.execute('DROP TRIGGER access_no_delete')
        conn.execute('DELETE FROM access_ledger WHERE seq=3')
        conn.execute("CREATE TRIGGER access_no_delete BEFORE DELETE ON access_ledger BEGIN SELECT RAISE(ABORT, 'append only'); END")
    assert run(setup,read_data=lambda:pytest.fail('opened'))['reason_code']=='ACCESS_LEDGER_TAMPERED'


def test_missing_final_report_is_not_reconstructed(setup):
    root,q,data=setup;run(setup);path=root/f'report-{reservation_key(q)}.json';path.unlink()
    with pytest.raises(OSError):verify_holdout_report(root,reservation_key(q))
    assert not path.exists()
    assert run(setup)['reason_code']=='HOLDOUT_ALREADY_CONSUMED'


def test_resealed_status_change_fails_replay(setup):
    root,q,data=setup;run(setup);path=root/f'report-{reservation_key(q)}.json';value=json.loads(path.read_text())
    value['holdout_status']='HOLDOUT_FAILED';value['holdout_report_hash']=_hash({k:v for k,v in value.items() if k!='holdout_report_hash'})
    path.write_bytes(_canonical(value))
    with pytest.raises(ValueError):verify_holdout_report(root,reservation_key(q))


def test_close_failure_releases_lock_and_preserves_completed_result(setup,monkeypatch):
    import app.research.holdout as module
    from urllib.parse import quote
    real=module._connect
    class Connection(sqlite3.Connection):
        def close(self):raise sqlite3.OperationalError('injected close failure')
    def connect(root):return sqlite3.connect('file:'+quote(str(root/'access.sqlite'),safe='/')+'?mode=rw',
        uri=True,isolation_level=None,timeout=0,factory=Connection)
    monkeypatch.setattr(module,'_connect',connect)
    result=run(setup)
    monkeypatch.setattr(module,'_connect',real)
    assert result['holdout_status']=='HOLDOUT_PASSED'
    assert verify_holdout_report(setup[0],reservation_key(setup[1]))==result


@pytest.mark.parametrize('transition',['RESERVED','CONSUMED'])
def test_after_commit_error_is_resolved_without_stale_state(setup,monkeypatch,transition):
    import app.research.holdout as module
    from urllib.parse import quote
    class Connection(sqlite3.Connection):
        pending=None
        def execute(self,sql,*args):
            result=super().execute(sql,*args)
            if sql.startswith('INSERT INTO access_ledger'):self.pending=json.loads(args[0][2])['to_state']
            if sql=='COMMIT' and self.pending==transition:
                self.pending=None
                raise sqlite3.OperationalError('injected after durable COMMIT')
            return result
    def connect(root):return sqlite3.connect('file:'+quote(str(root/'access.sqlite'),safe='/')+'?mode=rw',
        uri=True,isolation_level=None,timeout=0,factory=Connection)
    monkeypatch.setattr(module,'_connect',connect)
    reads=[]
    result=run(setup,read_data=lambda:(reads.append(1) or setup[2]))
    assert result['holdout_status']=='HOLDOUT_PASSED' and result['holdout_access_state']=='COMPLETED'
    assert reads==[1]


@pytest.mark.parametrize('transition',['RESERVED','CONSUMED'])
def test_connection_lost_after_commit_requires_reconciliation_without_read(setup,monkeypatch,transition):
    import app.research.holdout as module
    from urllib.parse import quote
    original=module._connect
    class Connection(sqlite3.Connection):
        pending=None
        def execute(self,sql,*args):
            result=super().execute(sql,*args)
            if sql.startswith('INSERT INTO access_ledger'):self.pending=json.loads(args[0][2])['to_state']
            if sql=='COMMIT' and self.pending==transition:
                super().close()
                raise sqlite3.OperationalError('lost connection after durable COMMIT')
            return result
    def connect(root):return sqlite3.connect('file:'+quote(str(root/'access.sqlite'),safe='/')+'?mode=rw',
        uri=True,isolation_level=None,timeout=0,factory=Connection)
    monkeypatch.setattr(module,'_connect',connect)
    with pytest.raises(module.HoldoutCommitOutcomeUnknown):run(setup,read_data=lambda:pytest.fail('opened'))
    monkeypatch.setattr(module,'_connect',original)
    states=recover_holdout_store(setup[0])
    assert states[reservation_key(setup[1])]==('BLOCKED' if transition=='RESERVED' else 'CONSUMED')
    assert run(setup,read_data=lambda:pytest.fail('reopened'))['holdout_status']=='INVALID'
