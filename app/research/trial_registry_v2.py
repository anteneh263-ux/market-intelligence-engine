"""Isolated new-store Phase5 v2 protocol. No v1 migration or implicit store creation."""
from contextlib import contextmanager
import copy
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from . import registry_inspection as base
from .ledger_verification import _checked_verify
from .attempt_fingerprint import check_trial_preflight
from .timing_rows import check_timing_rows

__all__ = ['initialize_trial_registry', 'execute_trial', 'CommitOutcomeUnknown']


class CommitOutcomeUnknown(RuntimeError):
    """Commit outcome cannot be proven; reconcile the registry, never blindly retry."""


_RS = 'wp5_0a2.registry.v2'
_AS = 'wp5_0a2.anchor.v2'
_PS = 'wp5_0a2.pending.v2'
_PENDING = 'pending_anchor_update'
_EXTRA = {
    'RESERVATION': ('REGISTRY_OPEN', 'LEDGER_CHAIN_PRECHECK', 'ATOMIC_RESERVATION'),
    'TIMING_VALIDATION': ('REGISTRY_OPEN', 'LEDGER_CHAIN_PRECHECK', 'TRIAL_TARGET', 'TIMING_ROWS'),
    'OUTCOME_APPEND': ('REGISTRY_OPEN', 'LEDGER_CHAIN_PRECHECK', 'TRIAL_TARGET', 'OUTCOME_RECORD', 'ATOMIC_STATUS_APPEND'),
    'VERIFICATION_REPLAY': ('REGISTRY_OPEN', 'LEDGER_CHAIN_PRECHECK', 'REPLAY_ELIGIBILITY'),
    'REGISTRY_STATE': ('REGISTRY_OPEN', 'LEDGER_CHAIN_PRECHECK'),
}


def _identity(r, a):
    return base._hash(dict(registry_schema_version=_RS, registry_path=r, anchor_path=a, anchor_schema_version=_AS))


def _seal(value, key):
    value[key] = base._hash({k: v for k, v in value.items() if k != key})
    return value


def _publish(path, document):
    """Durable replacement under the installation mutex; never rewrite in place."""
    payload = base._canonical(document)
    fd, temporary = tempfile.mkstemp(prefix='.phase5-anchor-', dir=str(Path(path).parent))
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(str(Path(path).parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _anchor(path, identity):
    raw = base._read(path)
    document = base._json(raw)
    reason = 'REGISTRY_SCHEMA_UNSUPPORTED'
    base._require(type(document) is dict and set(document) == {
        'schema_version', 'registry_identity', 'document_sequence', 'programs', 'document_hash'}, reason)
    base._require(document['schema_version'] == _AS and document['registry_identity'] == identity
        and base._integer(document['document_sequence'], 1) and type(document['programs']) is dict, reason)
    base._require(document['document_hash'] == base._hash({k:v for k,v in document.items() if k != 'document_hash'})
                  and raw == base._canonical(document), reason)
    for program, entry in document['programs'].items():
        base._require(base._hex(program) and type(entry) is dict and set(entry) == {
            'reservation_high_water', 'chain_high_water', 'head_row_hash', 'binding_hash'}, reason)
        base._require(base._integer(entry['reservation_high_water'], 1)
            and base._integer(entry['chain_high_water'], 1) and base._hex(entry['head_row_hash']), reason)
        binding = dict(research_program_id=program, registry_identity=identity,
                       **{k:v for k,v in entry.items() if k != 'binding_hash'})
        base._require(entry['binding_hash'] == base._hash(binding), reason)
    return document


def initialize_trial_registry(registry_path, anchor_path):
    """Create fresh v2 files only. Partial initialization is never auto-repaired."""
    r, a = base._path(registry_path), base._path(anchor_path)
    m = a + '.lock'
    base._require(len({r,a,m}) == 3 and all(not os.path.lexists(p) for p in (r,a,m)), 'REGISTRY_SCHEMA_UNSUPPORTED')
    # Validate parent chains before creating anything; no implicit directories.
    for p in (r,a,m):
        parent = Path(p).parent
        base._require(parent.resolve() == parent and parent.is_dir(), 'REGISTRY_SCHEMA_UNSUPPORTED')
    mutex = registry = None
    try:
        fd = os.open(m, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        mutex = base._connect(m, 'rw')
        mutex.execute('PRAGMA synchronous=FULL')
        mutex.execute('BEGIN EXCLUSIVE')
        mutex.execute(base._MUTEX_DDL)
        mutex.execute('INSERT INTO registry_mutex VALUES (?,?)', base._SENTINEL[0])
        mutex.execute('COMMIT')
        mutex.execute('BEGIN EXCLUSIVE')
        fd = os.open(r, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        registry = base._connect(r, 'rw')
        registry.execute('PRAGMA synchronous=FULL')
        registry.execute('BEGIN EXCLUSIVE')
        for ddl in (*base._TABLES.values(), *base._TRIGGERS.values()):
            registry.execute(ddl)
        identity = _identity(r,a)
        registry.executemany('INSERT INTO registry_meta VALUES (?,?)',
                            [('registry_schema_version',_RS),('registry_identity',identity)])
        registry.execute("INSERT INTO registry_state VALUES ('init_state','PENDING_ANCHOR_CREATE')")
        registry.execute('COMMIT')
        document = _seal(dict(schema_version=_AS, registry_identity=identity, document_sequence=1, programs={}), 'document_hash')
        _publish(a,document)
        registry.execute('BEGIN IMMEDIATE')
        registry.execute("UPDATE registry_state SET state_value='COMPLETE' WHERE state_key='init_state'")
        registry.execute('COMMIT')
    finally:
        if registry is not None:
            registry.close()
        if mutex is not None:
            mutex.close()


@contextmanager
def _open(registry_path, anchor_path, writer):
    r,a = base._path(registry_path),base._path(anchor_path)
    m = a + '.lock'
    base._regular(r,'REGISTRY_CORRUPT')
    base._regular(m,'REGISTRY_CORRUPT')
    base._db_preflight(m)
    mutex = registry = None
    try:
        mutex = base._connect(m,'rw')
        if not writer:
            mutex.execute('PRAGMA query_only=ON')
        base._require(base._objects(mutex) == {('table','registry_mutex'):base._MUTEX_DDL},'REGISTRY_CORRUPT')
        mutex.execute('BEGIN EXCLUSIVE' if writer else 'BEGIN DEFERRED')
        base._require(mutex.execute('SELECT sentinel_key,sentinel_value FROM registry_mutex').fetchall() == base._SENTINEL,'REGISTRY_CORRUPT')
        before = [base._regular(p,'REGISTRY_CORRUPT') for p in (r,a,m)]
        base._require(len({x[:2] for x in before}) == 3,'REGISTRY_CORRUPT')
        base._db_preflight(r)
        registry = base._connect(r,'rw' if writer else 'ro')
        registry.execute('PRAGMA synchronous=FULL' if writer else 'PRAGMA query_only=ON')
        registry.execute('BEGIN IMMEDIATE' if writer else 'BEGIN DEFERRED')
        base._require(registry.execute('PRAGMA integrity_check').fetchall() == [('ok',)],'REGISTRY_CORRUPT')
        expected = {('table',k):v for k,v in base._TABLES.items()}
        expected.update({('trigger',k):v for k,v in base._TRIGGERS.items()})
        base._require(base._objects(registry) == expected,'REGISTRY_SCHEMA_UNSUPPORTED')
        identity = _identity(r,a)
        meta = dict(registry.execute('SELECT meta_key,meta_value FROM registry_meta'))
        base._require(meta == dict(registry_schema_version=_RS,registry_identity=identity),'REGISTRY_SCHEMA_UNSUPPORTED')
        state = dict(registry.execute('SELECT state_key,state_value FROM registry_state'))
        base._require(set(state) in ({'init_state'},{'init_state',_PENDING}) and state.get('init_state') == 'COMPLETE','REGISTRY_SCHEMA_UNSUPPORTED')
        anchor = _anchor(a,identity)
        if _PENDING in state:
            _pending_record(state[_PENDING], identity)
        base._rows(registry)
        # Check identities before any caller mutation. Controlled-path scope only.
        base._require(all(base._regular(p,'REGISTRY_CORRUPT')[:2] == old[:2]
                          for p,old in zip((r,a,m),before)),'REGISTRY_CORRUPT')
        yield registry,anchor,state.get(_PENDING)
    finally:
        # Attempt both closes even if one reports an error. A cleanup exception
        # must not retroactively classify a committed effect as retryable.
        for connection in (registry, mutex):
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    try:
                        sqlite3.Connection.close(connection)
                    except sqlite3.Error:
                        pass


def _target(anchor, record):
    target = copy.deepcopy(anchor)
    program = record['research_program_id']
    entry = dict(reservation_high_water=record['target_reservation_high_water'],
                 chain_high_water=record['target_chain_high_water'], head_row_hash=record['target_head_row_hash'])
    entry['binding_hash'] = base._hash(dict(research_program_id=program,registry_identity=anchor['registry_identity'],**entry))
    target['programs'][program] = entry
    target['document_sequence'] = record['target_document_sequence']
    return _seal(target,'document_hash')


def _clear(connection, raw):
    connection.execute('BEGIN IMMEDIATE')
    try:
        cursor = connection.execute('DELETE FROM registry_state WHERE state_key=? AND state_value=?',(_PENDING,raw))
        base._require(cursor.rowcount == 1,'INTERNAL_STRUCTURAL_FAULT')
        connection.execute('COMMIT')
    except Exception:
        if connection.in_transaction:
            connection.execute('ROLLBACK')
        raise


def _pending_record(raw, identity):
    reason = 'REGISTRY_SCHEMA_UNSUPPORTED'
    try:
        record = base._json(raw)
    except (ValueError, TypeError, RecursionError):
        raise base._Refused(reason) from None
    fields = {'schema_version','operation_type','research_program_id','registry_identity',
        'target_reservation_high_water','target_chain_high_water','target_head_row_hash',
        'target_document_sequence','committed_row_hash','pending_hash'}
    base._require(type(record) is dict and set(record) == fields,reason)
    base._require(record['schema_version'] == _PS and record['operation_type'] in ('RESERVATION','OUTCOME_APPEND')
        and record['registry_identity'] == identity and base._hex(record['research_program_id']),reason)
    base._require(all(base._integer(record[k], 2 if k == 'target_document_sequence' else 1) for k in
        ('target_reservation_high_water','target_chain_high_water','target_document_sequence')),reason)
    base._require(all(base._hex(record[k]) for k in ('target_head_row_hash','committed_row_hash','pending_hash'))
        and record['pending_hash'] == base._hash({k:v for k,v in record.items() if k != 'pending_hash'})
        and base._canonical(record).decode() == raw,reason)
    return record


def _recover(connection, anchor, raw, anchor_path):
    record = _pending_record(raw, anchor['registry_identity'])
    program = record['research_program_id']
    target = _target(anchor,record)
    last = connection.execute('SELECT row_json FROM trial_ledger WHERE research_program_id=? AND chain_index=?',
                              (program,record['target_chain_high_water']-1)).fetchone()
    maximum = connection.execute('SELECT MAX(chain_index) FROM trial_ledger WHERE research_program_id=?',(program,)).fetchone()[0]
    base._require(last is not None and maximum == record['target_chain_high_water']-1,'LEDGER_CHAIN_BROKEN')
    row = base._json(last[0])
    base._require(base._hash({k:v for k,v in row.items() if k != 'row_hash'}) == row['row_hash'] == record['committed_row_hash'] == record['target_head_row_hash']
        and ('RESERVATION' if 'reservation_sequence' in row else 'OUTCOME_APPEND') == record['operation_type'],'LEDGER_CHAIN_BROKEN')
    rows = [base._json(item[0]) for item in connection.execute(
        'SELECT row_json FROM trial_ledger WHERE research_program_id=?',(program,))]
    base._require(sum('reservation_sequence' in item for item in rows) == record['target_reservation_high_water'],
                  'COUNTER_NONMONOTONIC')
    base._require(len(rows) == record['target_chain_high_water'], 'COUNTER_NONMONOTONIC')
    _checked_verify(connection,target,{},program)
    sequence = record['target_document_sequence']
    if sequence == anchor['document_sequence'] + 1:
        prior = anchor['programs'].get(program,dict(reservation_high_water=0,chain_high_water=0))
        base._require(record['target_chain_high_water'] == prior['chain_high_water']+1 and
            record['target_reservation_high_water'] == prior['reservation_high_water']+(record['operation_type']=='RESERVATION'),
            'COUNTER_NONMONOTONIC')
        # Predecessor must be the head named by the previous anchor.
        base._require(row['previous_row_hash'] == prior.get('head_row_hash','0'*64),'LEDGER_CHAIN_BROKEN')
        advance = True
    elif sequence == anchor['document_sequence']:
        base._require(target == anchor,'COUNTER_NONMONOTONIC')
        advance = False
    else:
        raise base._Refused('COUNTER_NONMONOTONIC')
    connection.execute('COMMIT')
    if advance:
        _publish(anchor_path,target)
    _clear(connection,raw)
    connection.execute('BEGIN IMMEDIATE')
    return target


def _find(connection, trial):
    reservation = outcome = None
    for (raw,) in connection.execute('SELECT row_json FROM trial_ledger'):
        row = base._json(raw)
        if row['trial_id'] == trial:
            if 'reservation_sequence' in row:
                reservation = row
            else:
                outcome = row
    return reservation,outcome


def _append(connection, anchor, anchor_path, row, verified, mode):
    _seal(row,'row_hash')
    n = verified['N_total'] + (mode == 'RESERVATION')
    record = _seal(dict(schema_version=_PS,operation_type=mode,research_program_id=row['research_program_id'],
        registry_identity=anchor['registry_identity'],target_reservation_high_water=n,
        target_chain_high_water=verified['chain_high_water']+1,target_head_row_hash=row['row_hash'],
        target_document_sequence=anchor['document_sequence']+1,committed_row_hash=row['row_hash']),'pending_hash')
    raw = base._canonical(record).decode()
    connection.execute('INSERT INTO trial_ledger VALUES (?,?,?)',
        (row['research_program_id'],verified['chain_high_water'],base._canonical(row).decode()))
    connection.execute('INSERT INTO registry_state VALUES (?,?)',(_PENDING,raw))
    try:
        connection.execute('COMMIT')
    except Exception as exc:
        try:
            if connection.in_transaction:
                raise base._Refused('LEDGER_APPEND_FAILED') from exc
            persisted = connection.execute('SELECT row_json FROM trial_ledger WHERE research_program_id=? AND chain_index=?',
                (row['research_program_id'],verified['chain_high_water'])).fetchone()
            pending = connection.execute('SELECT state_value FROM registry_state WHERE state_key=?',(_PENDING,)).fetchone()
            if persisted != (base._canonical(row).decode(),) or pending != (raw,):
                raise CommitOutcomeUnknown('Commit requires reconciliation; do not retry') from exc
        except base._Refused:
            raise
        except Exception as probe_error:
            raise CommitOutcomeUnknown('Commit requires reconciliation; do not retry') from probe_error
    # Requested effect is now durable. Publication/cleanup failure cannot invite retry.
    try:
        _publish(anchor_path,_target(anchor,record))
        _clear(connection,raw)
    except (OSError,sqlite3.Error,base._Refused):
        # The row is already committed. Cleanup cannot turn it into a retryable failure.
        try:
            if connection.in_transaction:
                connection.rollback()
        except (OSError,sqlite3.Error):
            pass
    return n


def execute_trial(request, registry_path, anchor_path):
    """Five-mode v2 dispatcher. Explicit stores, no performance-data capability."""
    prefix = check_trial_preflight(request)
    mode = request.get('mode') if type(request) is dict and type(request.get('mode')) is str else None
    checks = [dict(c) for c in prefix['checks']]
    checks.extend(dict(check_id=k,status='NOT_EVALUATED') for k in _EXTRA.get(mode,()))
    report = dict(schema_version='wp5_0a2.report.v2',mode=mode,counter_status='INVALID',counter_incremented=None,
        N_total=None,ledger_chain_hash=None,checks=checks,reason_code=prefix['reason_code'],result=None)
    if report['reason_code']:
        return _seal(report,'ledger_report_hash')
    index = len(prefix['checks'])
    verified = None
    writer = mode in ('RESERVATION','OUTCOME_APPEND')
    try:
        with _open(registry_path,anchor_path,writer) as (connection,anchor,pending):
            checks[index]['status']='PASS';index+=1
            if pending is not None and writer:
                anchor = _recover(connection,anchor,pending,os.fspath(anchor_path))
                pending = None
            verified = _checked_verify(connection,anchor,{_PENDING:pending} if pending is not None else {},request['research_program_id'])
            checks[index]['status']='PASS';index+=1
            report.update(N_total=verified['N_total'],ledger_chain_hash=verified['ledger_chain_hash'],counter_incremented=False)
            if mode == 'REGISTRY_STATE':
                report.update(counter_status='VERIFIED',result=dict(research_program_id=request['research_program_id'],
                    last_reservation_sequence=verified['N_total'] or None,last_trial_id=verified['last_trial_id'],
                    reservation_count=verified['N_total'],outcome_count=verified['outcome_count']))
            elif mode == 'VERIFICATION_REPLAY':
                replay=request['verification_replay_record']
                trial,outcome=_find(connection,replay['replay_of_trial_id'])
                base._require(trial is not None,'REPLAY_TARGET_NOT_FOUND')
                base._require(trial['research_program_id']==request['research_program_id'],'WRONG_RESEARCH_PROGRAM_ID')
                base._require(base._hash(replay['original_input_hashes'])==trial['ordered_input_hashes_hash'],'REPLAY_INPUT_MISMATCH')
                base._require(replay['original_method_hash']==trial['method_hash'],'REPLAY_METHOD_MISMATCH')
                base._require(outcome is not None and replay['original_result_hash']==outcome['result_hash'],'REPLAY_RESULT_MISMATCH')
                base._require(not request['performance_data_accessed'],'REPLAY_DATA_ACCESS_FORBIDDEN')
                base._require(not request['append_reservation_requested'],'REPLAY_RESERVATION_FORBIDDEN')
                checks[index]['status']='PASS'
                report.update(counter_status='REPLAY_VERIFIED',result=dict(verification_replay_id=base._hash(replay),
                    replay_of_trial_id=replay['replay_of_trial_id'],persisted_result_hash=outcome['result_hash'],replay_verified=True))
            else:
                trial=outcome=None
                if mode != 'RESERVATION':
                    trial,outcome=_find(connection,request['trial_id'])
                    base._require(trial is not None,'TRIAL_TARGET_NOT_FOUND')
                    base._require(trial['research_program_id']==request['research_program_id'],'WRONG_RESEARCH_PROGRAM_ID')
                    if mode == 'TIMING_VALIDATION':
                        base._require(trial['experiment_id']==request['experiment_id'],'TRIAL_EXPERIMENT_MISMATCH')
                    checks[index]['status']='PASS';index+=1
                if mode == 'TIMING_VALIDATION':
                    timing=check_timing_rows(request['timing_ledger_rows'],request['contract_record'],
                                              intended_dataset_zone=trial['intended_dataset_zone'])
                    base._require(timing['status']=='PASS',timing['reason_code'])
                    checks[index]['status']='PASS'
                    report.update(counter_status='VERIFIED',result=dict(trial_id=trial['trial_id'],**timing['result']))
                else:
                    if mode == 'OUTCOME_APPEND':
                        base._require(outcome is None,'OUTCOME_ALREADY_FINAL')
                        checks[index]['status']='PASS';index+=1
                    clock=time.time_ns()//1000000
                    if mode == 'RESERVATION':
                        attempt=request['attempt_fingerprint_record']
                        repeated=any(base._json(raw).get('attempt_fingerprint')==prefix['attempt_fingerprint']
                            for (raw,) in connection.execute('SELECT row_json FROM trial_ledger WHERE research_program_id=?',(request['research_program_id'],)))
                        identity=dict(research_program_id=request['research_program_id'],experiment_id=request['experiment_id'],
                            attempt_fingerprint=prefix['attempt_fingerprint'],reservation_sequence=verified['N_total']+1,reservation_utc_ms=clock)
                        row=dict(**identity,trial_id=base._hash(identity),intended_dataset_zone=attempt['intended_dataset_zone'],
                            ordered_input_hashes_hash=base._hash(attempt['ordered_input_hashes']),method_hash=attempt['method_hash'],
                            previous_row_hash=verified['ledger_chain_hash'])
                    else:
                        row=dict(research_program_id=request['research_program_id'],trial_id=request['trial_id'],
                            outcome_sequence=verified['outcome_count']+1,outcome_utc_ms=clock,trial_outcome=request['trial_outcome'],
                            result_hash=request['result_hash'],previous_row_hash=verified['ledger_chain_hash'],row_type='TRIAL_OUTCOME')
                    n=_append(connection,anchor,os.fspath(anchor_path),row,verified,mode)
                    checks[index]['status']='PASS'
                    if mode == 'RESERVATION':
                        result={k:row[k] for k in ('attempt_fingerprint','trial_id','reservation_sequence','reservation_utc_ms')}
                        result.update(trial_outcome='RESERVED',repeated_attempt_fingerprint=repeated,ledger_row_hash=row['row_hash'])
                    else:
                        result={k:row[k] for k in ('trial_id','trial_outcome','result_hash','outcome_sequence','outcome_utc_ms')}
                        result['ledger_row_hash']=row['row_hash']
                    report.update(counter_status='COMMITTED' if mode=='RESERVATION' else 'VERIFIED',
                        counter_incremented=mode=='RESERVATION',N_total=n,ledger_chain_hash=row['row_hash'],result=result)
    except (base._Refused,OSError,sqlite3.Error,ValueError,TypeError,KeyError) as exc:
        reason=str(exc) if isinstance(exc,base._Refused) else (
            'REGISTRY_BUSY' if isinstance(exc,sqlite3.Error) and getattr(exc,'sqlite_errorcode',0)&0xff in (5,6)
            else 'INTERNAL_STRUCTURAL_FAULT')
        checks[min(index,len(checks)-1)]['status']='FAIL'
        report.update(reason_code=reason,counter_status='INVALID',result=None)
    return _seal(report,'ledger_report_hash')
