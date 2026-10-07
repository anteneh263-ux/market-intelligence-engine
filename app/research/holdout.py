"""Single-use synthetic/authorized holdout protocol. No implicit roots or resume."""
from contextlib import contextmanager
import fcntl
from contextlib import suppress
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from urllib.parse import quote

from .registry_inspection import _canonical,_hash,_hex
from .validation_plan import snapshot,require,finite,read_artifact
from .walkforward import _Store
from .monte_carlo import _metrics

class HoldoutCommitOutcomeUnknown(RuntimeError):
    """Transition may be durable; reconcile preserved evidence, never retry blindly."""


_SCHEMA='wp5_6.store.v1'
_DDL=(
 'CREATE TABLE access_ledger (dataset_hash TEXT NOT NULL, seq INTEGER NOT NULL, row_json TEXT NOT NULL, PRIMARY KEY (dataset_hash, seq))',
 "CREATE TRIGGER access_no_update BEFORE UPDATE ON access_ledger BEGIN SELECT RAISE(ABORT, 'append only'); END",
 "CREATE TRIGGER access_no_delete BEFORE DELETE ON access_ledger BEGIN SELECT RAISE(ABORT, 'append only'); END",
)
_FIELDS={'schema_version','dataset_hash','seq','strategy_identity','experiment_id','trial_id','reservation_key',
         'from_state','to_state','utc_ms','actor','evaluation_hash','note','prev_row_hash','row_hash'}


class _Directory(_Store):
    def __init__(self,directory):
        path=Path(directory)
        require(path.is_absolute() and path.resolve()==path and not path.is_symlink() and
                'H025' not in re.sub('[^A-Z0-9]','',str(path).upper()),'ACCESS_LEDGER_TAMPERED')
        self.fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        self.path=path


def _connect(root):
    path=root/'access.sqlite'
    require(path.is_file() and not path.is_symlink(),'ACCESS_LEDGER_TAMPERED')
    conn=sqlite3.connect('file:'+quote(str(path),safe='/')+'?mode=rw',uri=True,isolation_level=None,timeout=0)
    conn.execute('PRAGMA synchronous=FULL')
    return conn


def initialize_holdout_store(directory):
    store=_Store(directory)
    try:
        store.put('FORMAT.json',dict(schema_version=_SCHEMA,store_identity=_hash(str(Path(directory)))))
        for name in ('access.lock','access.sqlite'):
            fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=store.fd)
            os.fsync(fd);os.close(fd)
        os.fsync(store.fd)
        conn=_connect(Path(directory))
        try:
            conn.execute('BEGIN IMMEDIATE')
            for ddl in _DDL:conn.execute(ddl)
            conn.execute('COMMIT')
        finally:conn.close()
        os.fsync(store.fd)
    finally:store.close()


def reservation_key(request):
    return _hash({k:request[k] for k in ('strategy_identity','experiment_id','holdout_dataset_hash')})


def _verify(conn,store):
    fmt=json.loads(store.read('FORMAT.json'))
    require(fmt==dict(schema_version=_SCHEMA,store_identity=_hash(str(store.path))), 'ACCESS_LEDGER_TAMPERED')
    actual={row[0] for row in conn.execute("SELECT sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
    require(actual==set(_DDL) and conn.execute('PRAGMA integrity_check').fetchall()==[('ok',)],'ACCESS_LEDGER_TAMPERED')
    heads={};states={};seen=set();rows=[];receipts=set();strategies=set()
    for dataset,seq,raw in conn.execute('SELECT dataset_hash,seq,row_json FROM access_ledger ORDER BY dataset_hash,seq'):
        row=json.loads(raw)
        require(type(row) is dict and set(row)==_FIELDS and _canonical(row).decode()==raw,'ACCESS_LEDGER_TAMPERED')
        require(row['schema_version']=='wp5_6.row.v1' and row['dataset_hash']==dataset and row['seq']==seq and
                all(_hex(row[k]) for k in ('dataset_hash','strategy_identity','experiment_id','trial_id','reservation_key','prev_row_hash','row_hash')),
                'ACCESS_LEDGER_TAMPERED')
        require(type(row['utc_ms']) is int and row['utc_ms']>=0 and type(row['actor']) is str and row['actor'] and
                (row['note'] is None or type(row['note']) is str),'ACCESS_LEDGER_TAMPERED')
        previous=heads.get(dataset)
        genesis=_hash(dict(schema_version=_SCHEMA,store_identity=fmt['store_identity'],dataset_hash=dataset))
        require(seq==(previous['seq']+1 if previous else 1) and row['prev_row_hash']==(previous['row_hash'] if previous else genesis) and
                (previous is None or row['utc_ms']>=previous['utc_ms']) and
                row['row_hash']==_hash({k:v for k,v in row.items() if k!='row_hash'}),'ACCESS_LEDGER_TAMPERED')
        key=row['reservation_key'];before=states.get(key)
        identity=dict(strategy_identity=row['strategy_identity'],experiment_id=row['experiment_id'],holdout_dataset_hash=dataset)
        require(key==reservation_key(identity),'ACCESS_LEDGER_TAMPERED')
        source=before['to_state'] if before else 'AVAILABLE';target=row['to_state']
        require(row['from_state']==source,'ACCESS_LEDGER_TAMPERED')
        if row['note'] is not None:
            require(before is not None and target==source and row['evaluation_hash'] is None,'ACCESS_LEDGER_TAMPERED')
        else:
            require((source,target) in {('AVAILABLE','RESERVED'),('RESERVED','BLOCKED'),('RESERVED','CONSUMED'),('CONSUMED','COMPLETED')},
                    'ACCESS_LEDGER_TAMPERED')
            require((_hex(row['evaluation_hash']) if target=='COMPLETED' else row['evaluation_hash'] is None),'ACCESS_LEDGER_TAMPERED')
        if target=='RESERVED' and source=='AVAILABLE':
            pair=(row['strategy_identity'],dataset)
            require(pair not in strategies,'ACCESS_LEDGER_TAMPERED');strategies.add(pair)
        if before:
            require(all(row[k]==before[k] for k in ('strategy_identity','experiment_id','trial_id')),'ACCESS_LEDGER_TAMPERED')
        name=f"row-{dataset}-{seq}.json"
        require(store.read(name)==_canonical(row),'ACCESS_LEDGER_TAMPERED')
        receipts.add(name);heads[dataset]=row;states[key]=row;rows.append(row)
    require({name for name in os.listdir(store.fd) if name.startswith('row-')}==receipts,'ACCESS_LEDGER_TAMPERED')
    return fmt,heads,states,rows


@contextmanager
def _locked(directory):
    store=_Directory(directory);lock=None;conn=None
    try:
        lock=os.open('access.lock',os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=store.fd)
        require(stat.S_ISREG(os.fstat(lock).st_mode),'ACCESS_LEDGER_TAMPERED')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise ValueError('HOLDOUT_ALREADY_RESERVED') from exc
        conn=_connect(store.path)
        _verify(conn,store)
        yield conn,store
    finally:
        if conn is not None:
            try:conn.close()
            except sqlite3.Error:
                with suppress(sqlite3.Error):sqlite3.Connection.close(conn)
        if lock is not None:
            with suppress(OSError):fcntl.flock(lock,fcntl.LOCK_UN)
            with suppress(OSError):os.close(lock)
        with suppress(OSError):store.close()


def _append(conn,store,q,to_state,*,evaluation_hash=None,note=None):
    conn.execute('BEGIN IMMEDIATE')
    try:
        fmt,heads,states,rows=_verify(conn,store)
        dataset=q['holdout_dataset_hash'];key=reservation_key(q);previous=heads.get(dataset);before=states.get(key)
        source=before['to_state'] if before else 'AVAILABLE'
        if before:
            require(all(q[k]==before[k] for k in ('strategy_identity','experiment_id','trial_id')),'STATE_TRANSITION_FORBIDDEN')
        require((_hex(evaluation_hash) if to_state=='COMPLETED' and note is None else evaluation_hash is None),'STATE_TRANSITION_FORBIDDEN')
        if note is None:
            require((source,to_state) in {('AVAILABLE','RESERVED'),('RESERVED','BLOCKED'),('RESERVED','CONSUMED'),('CONSUMED','COMPLETED')},
                    'STATE_TRANSITION_FORBIDDEN')
            if source=='AVAILABLE':
                require(not any(r['strategy_identity']==q['strategy_identity'] and r['dataset_hash']==dataset for r in rows),
                        'HOLDOUT_ALREADY_CONSUMED')
        else:require(before is not None and source==to_state,'STATE_TRANSITION_FORBIDDEN')
        now=time.time_ns()//1000000
        require(previous is None or now>=previous['utc_ms'],'STATE_TRANSITION_FORBIDDEN')
        row=dict(schema_version='wp5_6.row.v1',dataset_hash=dataset,seq=previous['seq']+1 if previous else 1,
            strategy_identity=q['strategy_identity'],experiment_id=q['experiment_id'],trial_id=q['trial_id'],
            reservation_key=key,from_state=source,to_state=to_state,utc_ms=now,actor=q['actor'],evaluation_hash=evaluation_hash,note=note,
            prev_row_hash=previous['row_hash'] if previous else _hash(dict(schema_version=_SCHEMA,store_identity=fmt['store_identity'],dataset_hash=dataset)))
        row['row_hash']=_hash(row)
        store.put(f"row-{dataset}-{row['seq']}.json",row)
        conn.execute('INSERT INTO access_ledger VALUES (?,?,?)',(dataset,row['seq'],_canonical(row).decode()))
        try:
            conn.execute('COMMIT')
        except sqlite3.Error as exc:
            try:
                active=conn.in_transaction
            except sqlite3.Error:
                raise HoldoutCommitOutcomeUnknown('Holdout transition commit unknown; reconcile before any retry') from exc
            if active:raise
            try:
                persisted=conn.execute('SELECT row_json FROM access_ledger WHERE dataset_hash=? AND seq=?',
                                       (dataset,row['seq'])).fetchone()
                require(persisted is not None and persisted[0]==_canonical(row).decode(),'ACCESS_LEDGER_TAMPERED')
                _verify(conn,store)
            except (sqlite3.Error,ValueError,OSError) as probe_error:
                raise HoldoutCommitOutcomeUnknown('Holdout transition commit unknown; reconcile before any retry') from probe_error
        return row
    except BaseException:
        with suppress(sqlite3.Error):
            if conn.in_transaction:conn.execute('ROLLBACK')
        raise


def _recover(conn,store):
    states=_verify(conn,store)[2]
    for row in states.values():
        if row['to_state']=='RESERVED':
            q={k:row[k] for k in ('strategy_identity','experiment_id','trial_id')}
            q.update(holdout_dataset_hash=row['dataset_hash'],actor='recovery')
            _append(conn,store,q,'BLOCKED')


def recover_holdout_store(directory):
    with _locked(directory) as (conn,store):
        _recover(conn,store)
        return {k:v['to_state'] for k,v in _verify(conn,store)[2].items()}


def append_recovery_note(directory, key, *, actor, note):
    require(type(actor) is str and actor and type(note) is str and note.strip(),'STATE_TRANSITION_FORBIDDEN')
    with _locked(directory) as (conn,store):
        _recover(conn,store);states=_verify(conn,store)[2];require(key in states,'ACCESS_WITHOUT_RESERVATION')
        row=states[key];q={k:row[k] for k in ('strategy_identity','experiment_id','trial_id')}
        q.update(holdout_dataset_hash=row['dataset_hash'],actor=actor)
        return _append(conn,store,q,row['to_state'],note=note)


def _validate_request(request):
    q=snapshot(request)
    require(type(q) is dict and set(q)=={'strategy_identity','research_program_id','experiment_id','trial_id',
        'holdout_dataset_hash','parameters','param_hash','decision_rule','actor'},'STRATEGY_IDENTITY_INVALID')
    require(all(_hex(q[k]) for k in ('strategy_identity','research_program_id','experiment_id','trial_id','holdout_dataset_hash','param_hash')) and
        type(q['parameters']) is dict and _hash(q['parameters'])==q['param_hash'] and
        type(q['actor']) is str and q['actor'],'STRATEGY_IDENTITY_INVALID')
    rule=q['decision_rule']
    require(type(rule) is dict and set(rule)=={'min_total_return','max_drawdown'} and
        all(finite(v) for v in rule.values()) and 0<=rule['max_drawdown']<=1,'PREREQ_NOT_MET')
    return q


def run_holdout(request, *, directory, verify_prerequisites, read_data, evaluate):
    """Trusted verifier/evaluator are supplied by outer governed orchestration.

    Consumer data is not opened until CONSUMED authorization is durable. Returns
    INVALID on ordinary failures, never retries, resets, or reopens a used key.
    """
    failure=dict(schema_version='wp5_6.report.v1',holdout_status='INVALID',reason_code=None,
                 holdout_access_state=None,evaluation_ordinal=None,decision=None,metrics=None)
    try:
        with _locked(directory) as (conn,store):
            _recover(conn,store)
            q=_validate_request(request);before=_hash(request);key=reservation_key(q)
            try:
                verify_prerequisites(snapshot(q))
            except (ValueError,TypeError,KeyError,OSError,OverflowError,RecursionError) as exc:
                raise ValueError('PREREQ_NOT_MET') from exc
            rows=_verify(conn,store)[3]
            earlier=[r for r in rows if r['strategy_identity']==q['strategy_identity'] and r['dataset_hash']==q['holdout_dataset_hash']]
            if earlier:
                state=earlier[-1]['to_state'];failure['holdout_access_state']=state;failure['evaluation_ordinal']=1
                raise ValueError('HOLDOUT_BLOCKED' if state=='BLOCKED' else 'HOLDOUT_ALREADY_CONSUMED')
            reserved=_append(conn,store,q,'RESERVED')
            failure.update(holdout_access_state='RESERVED',evaluation_ordinal=1)
            try:
                consumed=_append(conn,store,q,'CONSUMED')
                failure['holdout_access_state']='CONSUMED'
                data=snapshot(read_data())
                require(_hash(data)==q['holdout_dataset_hash'],'HOLDOUT_DATASET_INVALID')
                params=snapshot(q['parameters']);returns=snapshot(evaluate(data,params))
                require(_hash(request)==before and _hash(params)==q['param_hash'],'POST_ACCESS_MUTATION')
                require(type(returns) is list and returns and all(finite(v) for v in returns),'HOLDOUT_DATASET_INVALID')
                metrics=_metrics(returns);rule=q['decision_rule']
                passed=not metrics['ruined'] and metrics['total_return']>=rule['min_total_return'] and metrics['max_drawdown']<=rule['max_drawdown']
                core=dict(request=q,metrics=metrics,net_returns=returns,returns_hash=_hash(returns),decision='HOLDOUT_PASSED' if passed else 'HOLDOUT_FAILED')
                store.put(f'evaluation-{key}.json',core)
                completed=_append(conn,store,q,'COMPLETED',evaluation_hash=_hash(core))
                failure['holdout_access_state']='COMPLETED'
                report=_make_report(core,reserved,consumed,completed)
                store.put(f'report-{key}.json',report)
                return report
            except HoldoutCommitOutcomeUnknown:
                raise
            except Exception:
                if failure['holdout_access_state']=='RESERVED':
                    _append(conn,store,q,'BLOCKED');failure['holdout_access_state']='BLOCKED'
                raise
    except (ValueError,OSError,sqlite3.Error,TypeError,KeyError,OverflowError,RecursionError) as exc:
        allowed={'ACCESS_LEDGER_TAMPERED','PREREQ_NOT_MET','APPLICABILITY_CHANGED_POST_RESULTS','STRATEGY_IDENTITY_INVALID',
            'HOLDOUT_DATASET_INVALID','HOLDOUT_ALREADY_RESERVED','HOLDOUT_ALREADY_CONSUMED','HOLDOUT_BLOCKED',
            'ACCESS_WITHOUT_RESERVATION','STATE_TRANSITION_FORBIDDEN','POST_ACCESS_MUTATION'}
        failure['reason_code']=str(exc) if str(exc) in allowed else 'ACCESS_LEDGER_TAMPERED'
        failure['holdout_report_hash']=_hash(failure)
        return failure


def verify_holdout_report(directory,key):
    """Verification only: no evaluator/reader parameters, no new ledger rows."""
    with _locked(directory) as (conn,store):
        rows=_verify(conn,store)[3]
        matches=[r for r in rows if r['reservation_key']==key and r['to_state']=='COMPLETED' and r['note'] is None]
        require(len(matches)==1,'HOLDOUT_HASH_MISMATCH');row=matches[0]
        core=json.loads(store.read(f'evaluation-{key}.json'));report=json.loads(store.read(f'report-{key}.json'))
        require(_hash(core)==row['evaluation_hash'] and type(core) is dict and
                set(core)=={'request','metrics','net_returns','returns_hash','decision'},'HOLDOUT_HASH_MISMATCH')
        q=_validate_request(core['request'])
        require(reservation_key(q)==key and type(core['net_returns']) is list and core['net_returns'] and
                all(finite(v) for v in core['net_returns']) and _hash(core['net_returns'])==core['returns_hash'],
                'HOLDOUT_HASH_MISMATCH')
        metrics=_metrics(core['net_returns']);rule=q['decision_rule']
        passed=not metrics['ruined'] and metrics['total_return']>=rule['min_total_return'] and metrics['max_drawdown']<=rule['max_drawdown']
        require(core['metrics']==metrics and core['decision']==('HOLDOUT_PASSED' if passed else 'HOLDOUT_FAILED'),
                'HOLDOUT_HASH_MISMATCH')
        prior=[r for r in rows if r['reservation_key']==key and r['seq']<row['seq'] and r['note'] is None]
        require(len(prior)==2 and prior[0]['to_state']=='RESERVED' and prior[1]['to_state']=='CONSUMED',
                'HOLDOUT_HASH_MISMATCH')
        require(report==_make_report(core,prior[0],prior[1],row),'HOLDOUT_HASH_MISMATCH')
        return report


def _make_report(core,reserved,consumed,completed):
    q=core['request']
    report=dict(schema_version='wp5_6.report.v1',holdout_status=core['decision'],reason_code=None,
        holdout_access_state='COMPLETED',evaluation_ordinal=1,decision=core['decision'],metrics=core['metrics'],
        reservation_key=reservation_key(q),strategy_identity=q['strategy_identity'],holdout_dataset_hash=q['holdout_dataset_hash'],
        evaluation_hash=_hash(core),access_audit=dict(reserved_utc_ms=reserved['utc_ms'],
            read_authorized_utc_ms=consumed['utc_ms'],completed_utc_ms=completed['utc_ms'],actor=q['actor'],
            prior_access_count=0,ledger_chain_hash=completed['row_hash']))
    report['holdout_report_hash']=_hash(report)
    return report
