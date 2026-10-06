"""Synthetic, independently sealed ledger fixtures; no production evidence."""
import hashlib
import json
import sqlite3
from pathlib import Path
import pytest
from app.research.phase5_trial_registry import initialize_registry
from app.research.ledger_verification import verify_registry_ledger


def canon(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def digest(x):
    return hashlib.sha256(canon(x)).hexdigest()


def seal(row):
    row['row_hash'] = digest({k:v for k,v in row.items() if k != 'row_hash'})
    return row


def reserve(program='a'*64, sequence=1, previous='0'*64):
    row = dict(research_program_id=program, experiment_id='b'*64, attempt_fingerprint='c'*64,
               reservation_sequence=sequence, reservation_utc_ms=1000+sequence,
               intended_dataset_zone='TRAIN', ordered_input_hashes_hash='d'*64,
               method_hash='e'*64, previous_row_hash=previous)
    row['trial_id'] = digest({k:row[k] for k in ('research_program_id','experiment_id',
                            'attempt_fingerprint','reservation_sequence','reservation_utc_ms')})
    return seal(row)


def outcome(row, sequence=1, previous=None):
    return seal(dict(research_program_id=row['research_program_id'], trial_id=row['trial_id'],
        outcome_sequence=sequence, outcome_utc_ms=2000, trial_outcome='COMPLETED_VALID',
        result_hash='f'*64, previous_row_hash=previous or row['row_hash'], row_type='TRIAL_OUTCOME'))


@pytest.fixture
def store(tmp_path):
    r=tmp_path.resolve()/'registry';a=tmp_path.resolve()/'anchor'
    initialize_registry(str(r),str(a))
    return r,a


def write(store, rows, indices=None, anchor_edit=None):
    r,a=store
    with sqlite3.connect(str(r)) as c:
        counters={}
        for pos,row in enumerate(rows):
            p=row['research_program_id'];i=counters.get(p,0);counters[p]=i+1
            c.execute('INSERT INTO trial_ledger VALUES (?,?,?)',(p,i if indices is None else indices[pos],canon(row).decode()))
    document=json.loads(a.read_bytes())
    for p in {r['research_program_id'] for r in rows}:
        chain=[r for r in rows if r['research_program_id']==p]
        entry=dict(reservation_high_water=sum('reservation_sequence' in r for r in chain),
                   chain_high_water=len(chain),head_row_hash=chain[-1]['row_hash'])
        entry['binding_hash']=digest(dict(research_program_id=p,registry_identity=document['registry_identity'],**entry))
        document['programs'][p]=entry
    document['document_sequence']=1+len(rows)
    if anchor_edit: anchor_edit(document)
    for p,e in document['programs'].items():
        e['binding_hash']=digest(dict(research_program_id=p,registry_identity=document['registry_identity'],
                                     **{k:v for k,v in e.items() if k!='binding_hash'}))
    document['document_hash']=digest({k:v for k,v in document.items() if k!='document_hash'})
    a.write_bytes(canon(document))


def check(store, program='a'*64):
    before={p.name:p.read_bytes() for p in store[0].parent.iterdir() if p.is_file()}
    result=verify_registry_ledger(*map(str,store), research_program_id=program)
    assert before=={p.name:p.read_bytes() for p in store[0].parent.iterdir() if p.is_file()}
    return result


def refused(store, reason):
    result=check(store)
    assert result['verification_status']=='REFUSED'
    assert result['reason_code']==reason
    assert result['N_total'] is None and result['ledger_chain_hash'] is None


def test_empty(store):
    result=check(store)
    assert result['verification_status']=='VERIFIED'
    assert result['N_total']==result['outcome_count']==0
    assert result['ledger_chain_hash']=='0'*64
    assert result['last_trial_id'] is None


def test_mixed_chain_counts_reservations_only(store):
    a=reserve();b=outcome(a);c=reserve(sequence=2,previous=b['row_hash']);d=outcome(c,2)
    write(store,[a,b,c,d]);result=check(store)
    assert result['verification_status']=='VERIFIED'
    assert result['N_total']==2 and result['outcome_count']==2
    assert result['ledger_chain_hash']==d['row_hash']
    assert result['last_trial_id']==c['trial_id']


@pytest.mark.parametrize('indices', [[1],[0,2]])
def test_physical_gap(store,indices):
    a=reserve();b=outcome(a);write(store,[a,b][:len(indices)],indices)
    refused(store,'LEDGER_CHAIN_BROKEN')


@pytest.mark.parametrize('field', ['previous_row_hash','row_hash'])
def test_broken_hash(store,field):
    a=reserve();a[field]='f'*64;write(store,[a]);refused(store,'LEDGER_CHAIN_BROKEN')


def test_rehashed_wrong_trial_identity(store):
    a=reserve();a['trial_id']='f'*64;seal(a);write(store,[a]);refused(store,'LEDGER_CHAIN_BROKEN')


@pytest.mark.parametrize('sequence',[2,3])
def test_counter_gap(store,sequence):
    a=reserve(sequence=sequence);write(store,[a]);refused(store,'COUNTER_NONMONOTONIC')


def test_outcome_gap(store):
    a=reserve();b=outcome(a,2);write(store,[a,b]);refused(store,'COUNTER_NONMONOTONIC')


def test_unknown_trial_outcome(store):
    a=reserve();b=outcome(a);b['trial_id']='9'*64;seal(b);write(store,[a,b]);refused(store,'LEDGER_CHAIN_BROKEN')


def test_duplicate_terminal_outcome(store):
    a=reserve();b=outcome(a);c=outcome(a,2,b['row_hash']);write(store,[a,b,c]);refused(store,'LEDGER_CHAIN_BROKEN')


@pytest.mark.parametrize('field,value',[('reservation_high_water',2),('chain_high_water',2),('head_row_hash','9'*64)])
def test_anchor_mismatch(store,field,value):
    write(store,[reserve()],anchor_edit=lambda d:d['programs']['a'*64].update({field:value}))
    refused(store,'COUNTER_NONMONOTONIC')


def test_missing_anchor_program(store):
    write(store,[reserve()],anchor_edit=lambda d:d['programs'].clear());refused(store,'COUNTER_NONMONOTONIC')


def test_other_program_integrity_is_also_checked(store):
    a=reserve();b=reserve('9'*64);b['row_hash']='f'*64
    write(store,[a,b]);refused(store,'LEDGER_CHAIN_BROKEN')


def test_other_program_does_not_inflate_counts(store):
    write(store,[reserve(),reserve('9'*64)])
    assert check(store)['N_total']==1
    assert check(store,'8'*64)['N_total']==0


def test_pending_never_recovered(store):
    with sqlite3.connect(str(store[0])) as c:
        c.execute("INSERT INTO registry_meta VALUES ('pending_anchor_update','pending')")
    refused(store,'COUNTER_NONMONOTONIC')


def test_bad_anchor_and_bad_registry(store):
    store[1].write_bytes(b'{}');refused(store,'REGISTRY_SCHEMA_UNSUPPORTED')


@pytest.mark.parametrize('program',[None,True,'bad','A'*64])
def test_invalid_program(store,program):
    result=check(store,program)
    assert result['verification_status']=='REFUSED' and result['N_total'] is None


def test_chain_failure_precedes_pending(store):
    a=reserve();a['row_hash']='f'*64;write(store,[a])
    with sqlite3.connect(str(store[0])) as c:
        c.execute("INSERT INTO registry_meta VALUES ('pending_anchor_update','pending')")
    refused(store,'LEDGER_CHAIN_BROKEN')


@pytest.mark.parametrize('busy',[False,True])
def test_ledger_io_error_is_not_corruption_and_connections_close(store,monkeypatch,busy):
    import app.research.registry_inspection as module
    real=sqlite3.connect;connections=[]
    class Connection(sqlite3.Connection):
        def execute(self,statement,*args):
            if 'ORDER BY research_program_id, chain_index' in statement:
                exc=sqlite3.OperationalError('injected read failure')
                exc.sqlite_errorcode=sqlite3.SQLITE_BUSY if busy else sqlite3.SQLITE_IOERR
                raise exc
            return super().execute(statement,*args)
    def connect(database,**kwargs):
        c=real(database,**kwargs,factory=Connection);connections.append(c);return c
    monkeypatch.setattr(module.sqlite3,'connect',connect)
    refused(store,'REGISTRY_BUSY' if busy else 'INTERNAL_STRUCTURAL_FAULT')
    for c in connections:
        with pytest.raises(sqlite3.ProgrammingError):c.execute('SELECT 1')
