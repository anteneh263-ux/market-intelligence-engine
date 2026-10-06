"""Synthetic registry inspection; no live scientific evidence."""
import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote
import pytest
from app.research.phase5_trial_registry import initialize_registry
from app.research.registry_inspection import inspect_registry


def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def digest(x):return hashlib.sha256(canonical(x)).hexdigest()


@pytest.fixture
def files(tmp_path):
    # Resolve /var to /private/var on macOS; the inspection requires canonical paths.
    root=tmp_path.resolve();r=root/'registry.sqlite';a=root/'registry.anchor'
    initialize_registry(str(r),str(a))
    return r,a,Path(str(a)+'.lock')


def snapshot(root):
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def call(files):return inspect_registry(str(files[0]),str(files[1]))
def refuse(files,reason):
    before=snapshot(files[0].parent)
    result=call(files)
    assert result==dict(inspection_status='REFUSED',reason_code=reason,registry_identity=None,
                        pending_detected=None,ledger_verification='NOT_PERFORMED')
    assert snapshot(files[0].parent)==before


def sql(path,statement,args=()):
    with sqlite3.connect(str(path)) as c:c.execute(statement,args)


def test_fresh_read_only(files):
    before=snapshot(files[0].parent)
    result=call(files)
    assert result==dict(inspection_status='STRUCTURE_VERIFIED',reason_code=None,
        registry_identity=json.loads(files[1].read_bytes())['registry_identity'],
        pending_detected=False,ledger_verification='NOT_PERFORMED')
    assert call(files)==result
    assert snapshot(files[0].parent)==before


@pytest.mark.parametrize('index',[0,2])
@pytest.mark.parametrize('action',['missing','zero','junk'])
def test_bad_db(files,index,action):
    p=files[index]
    if action=='missing':p.unlink()
    else:p.write_bytes(b'' if action=='zero' else b'not SQLite')
    refuse(files,'REGISTRY_CORRUPT')


@pytest.mark.parametrize('action',['missing','junk','hash','sequence','identity','extra','newline','duplicate'])
def test_bad_anchor(files,action):
    a=files[1];obj=json.loads(a.read_bytes())
    if action=='missing':a.unlink()
    elif action=='junk':a.write_bytes(b'{')
    elif action=='newline':a.write_bytes(a.read_bytes()+b'\n')
    elif action=='duplicate':a.write_bytes(b'{"schema_version":1,"schema_version":2}')
    else:
        if action=='hash':obj['document_hash']='f'*64
        if action=='sequence':obj['document_sequence']=True
        if action=='identity':obj['registry_identity']='f'*64
        if action=='extra':obj['extra']=1
        if action!='hash':obj['document_hash']=digest({k:v for k,v in obj.items() if k!='document_hash'})
        a.write_bytes(canonical(obj))
    refuse(files,'REGISTRY_SCHEMA_UNSUPPORTED')


@pytest.mark.parametrize('statement',[
    'DROP TRIGGER trial_ledger_no_update',
    'CREATE TABLE extra (x)',
    "UPDATE registry_state SET state_value='PENDING_ANCHOR_CREATE'",
    "INSERT INTO registry_state VALUES ('extra','x')",
    "INSERT INTO registry_meta VALUES ('extra','x')"])
def test_bad_schema_or_meta(files,statement):
    sql(files[0],statement);refuse(files,'REGISTRY_SCHEMA_UNSUPPORTED')


@pytest.mark.parametrize('statement',[
    "UPDATE registry_mutex SET sentinel_value='wrong'",
    'DELETE FROM registry_mutex',
    'CREATE TABLE extra (x)'])
def test_bad_mutex(files,statement):
    sql(files[2],statement);refuse(files,'REGISTRY_CORRUPT')


def test_pending_is_stop_not_recovery(files):
    sql(files[0],"INSERT INTO registry_meta VALUES ('pending_anchor_update','not validated here')")
    before=snapshot(files[0].parent);result=call(files)
    assert result['inspection_status']=='RECOVERY_REQUIRED'
    assert result['pending_detected'] is True and result['reason_code'] is None
    assert result['ledger_verification']=='NOT_PERFORMED'
    assert snapshot(files[0].parent)==before


@pytest.mark.parametrize('suffix',['-wal','-shm','-journal'])
def test_sidecars_refused(files,suffix):
    Path(str(files[2])+suffix).write_bytes(b'preserve')
    refuse(files,'REGISTRY_CORRUPT')


def test_symlinks_and_h025_refused(files):
    link=files[0].parent/'link';link.symlink_to(files[0])
    assert inspect_registry(str(link),str(files[1]))['inspection_status']=='REFUSED'
    assert inspect_registry(str(files[0].parent/'H-025'),str(files[1]))['inspection_status']=='REFUSED'


def test_uri_escaping_and_order_and_cleanup(tmp_path,monkeypatch):
    import app.research.registry_inspection as module
    root=(tmp_path/'name ?mode=rwc#%').resolve();root.mkdir()
    r=root/'registry';a=root/'anchor';initialize_registry(str(r),str(a))
    real=sqlite3.connect;connections=[];events=[]
    def connect(database,**kwargs):
        events.append(('connect',database,kwargs))
        c=real(database,**kwargs);connections.append(c)
        c.set_trace_callback(lambda statement:events.append(('sql',database,statement)))
        return c
    monkeypatch.setattr(module.sqlite3,'connect',connect)
    result=inspect_registry(str(r),str(a));assert result['inspection_status']=='STRUCTURE_VERIFIED'
    uris=[e[1] for e in events if e[0]=='connect']
    assert uris==['file:'+quote(str(a)+'.lock',safe='/')+'?mode=rw',
                  'file:'+quote(str(r),safe='/')+'?mode=ro']
    for e in events:
        if e[0]=='connect':assert e[2]==dict(uri=True,isolation_level=None,timeout=0)
    registry_sql=[e[2] for e in events if e[0]=='sql' and e[1]==uris[1]]
    integrity=registry_sql.index('PRAGMA integrity_check')
    structure=next(i for i,s in enumerate(registry_sql) if 'sqlite_master' in s)
    meta=next(i for i,s in enumerate(registry_sql) if 'FROM registry_meta' in s)
    assert integrity<structure<meta
    mutex_sql=[e[2] for e in events if e[0]=='sql' and e[1]==uris[0]]
    begin=mutex_sql.index('BEGIN DEFERRED')
    assert any('FROM registry_mutex' in s for s in mutex_sql[begin+1:])
    for c in connections:
        with pytest.raises(sqlite3.ProgrammingError):c.execute('SELECT 1')


def test_deleted_mutex_at_connect_is_not_recreated(files,monkeypatch):
    import app.research.registry_inspection as module
    real=sqlite3.connect
    def connect(database,**kw):
        if database.endswith('mode=rw'):files[2].unlink()
        return real(database,**kw)
    monkeypatch.setattr(module.sqlite3,'connect',connect)
    assert call(files)['reason_code']=='REGISTRY_CORRUPT'
    assert not files[2].exists()


def test_o3_busy_reason_and_cleanup(files,monkeypatch):
    import app.research.registry_inspection as module
    real=sqlite3.connect;blocker=real(str(files[2]),isolation_level=None);opened=[]
    class Connection(sqlite3.Connection):
        def execute(self,statement,*args):
            if statement=='BEGIN DEFERRED':blocker.execute('BEGIN EXCLUSIVE')
            return super().execute(statement,*args)
    def connect(database,**kw):
        c=real(database,**kw,factory=Connection);opened.append(c);return c
    monkeypatch.setattr(module.sqlite3,'connect',connect)
    try:
        result=call(files)
        assert result['reason_code']=='RESERVATION_RACE'
        for c in opened:
            with pytest.raises(sqlite3.ProgrammingError):c.execute('SELECT 1')
    finally:blocker.close()


@pytest.mark.parametrize('row',[{}, {'row_type':'unknown'}, {'row_hash':'bad'}])
def test_bad_rows(files,row):
    sql(files[0],'INSERT INTO trial_ledger VALUES (?,?,?)',('a'*64,1,json.dumps(row)))
    refuse(files,'REGISTRY_CORRUPT')


def test_nonempty_anchor_is_structural_not_ledger_verification(files):
    a=json.loads(files[1].read_bytes());program='a'*64
    entry=dict(reservation_high_water=1,chain_high_water=1,head_row_hash='b'*64)
    entry['binding_hash']=digest(dict(research_program_id=program,registry_identity=a['registry_identity'],**entry))
    a['programs']={program:entry};a['document_sequence']=2
    a['document_hash']=digest({k:v for k,v in a.items() if k!='document_hash'})
    files[1].write_bytes(canonical(a))
    # Intentionally no corresponding ledger row: this layer must not claim consistency.
    result=call(files)
    assert result['inspection_status']=='STRUCTURE_VERIFIED'
    assert result['ledger_verification']=='NOT_PERFORMED'


def reservation():
    return dict(research_program_id='a'*64,experiment_id='b'*64,trial_id='c'*64,
        attempt_fingerprint='d'*64,reservation_sequence=1,reservation_utc_ms=1000,
        intended_dataset_zone='TRAIN',ordered_input_hashes_hash='e'*64,method_hash='f'*64,
        previous_row_hash='0'*64,row_hash='1'*64)


def test_nonempty_rows_structure_without_chain_claim(files):
    first=reservation()
    second=dict(research_program_id='a'*64,trial_id='c'*64,outcome_sequence=1,outcome_utc_ms=2000,
        trial_outcome='CRASHED',result_hash='b'*64,previous_row_hash='1'*64,
        row_type='TRIAL_OUTCOME',row_hash='2'*64)
    for i,row in enumerate([first,second]):
        sql(files[0],'INSERT INTO trial_ledger VALUES (?,?,?)',('a'*64,i,json.dumps(row)))
    before=snapshot(files[0].parent);result=call(files)
    assert result['inspection_status']=='STRUCTURE_VERIFIED'
    assert result['ledger_verification']=='NOT_PERFORMED'
    assert snapshot(files[0].parent)==before


@pytest.mark.parametrize('field,value',[('reservation_sequence',True),('reservation_utc_ms',-1),
    ('method_hash','BAD'),('intended_dataset_zone','OTHER'),('research_program_id','b'*64)])
def test_bad_reservation_fields(files,field,value):
    row=reservation();row[field]=value
    sql(files[0],'INSERT INTO trial_ledger VALUES (?,?,?)',('a'*64,1,json.dumps(row)))
    refuse(files,'REGISTRY_CORRUPT')


@pytest.mark.parametrize('field,value',[('reservation_high_water',0),('chain_high_water',True),
    ('binding_hash','f'*64),('head_row_hash','BAD')])
def test_bad_program_anchor(files,field,value):
    a=json.loads(files[1].read_bytes());program='a'*64
    entry=dict(reservation_high_water=1,chain_high_water=1,head_row_hash='b'*64)
    entry['binding_hash']=digest(dict(research_program_id=program,registry_identity=a['registry_identity'],**entry))
    entry[field]=value;a['programs']={program:entry};a['document_sequence']=2
    a['document_hash']=digest({k:v for k,v in a.items() if k!='document_hash'})
    files[1].write_bytes(canonical(a));refuse(files,'REGISTRY_SCHEMA_UNSUPPORTED')


def test_wrong_registry_identity_with_golden_schema(files):
    with sqlite3.connect(str(files[0])) as c:
        ddl=c.execute("SELECT sql FROM sqlite_master WHERE name='registry_meta_no_update'").fetchone()[0]
        c.execute('DROP TRIGGER registry_meta_no_update')
        c.execute("UPDATE registry_meta SET meta_value=? WHERE meta_key='registry_identity'",('f'*64,))
        c.execute(ddl)
    refuse(files,'REGISTRY_SCHEMA_UNSUPPORTED')


def test_wal_mode_without_sidecars_refused(files):
    with sqlite3.connect(str(files[0])) as c:c.execute('PRAGMA journal_mode=WAL')
    c.close()
    assert not Path(str(files[0])+'-wal').exists()
    refuse(files,'REGISTRY_CORRUPT')


def test_first_ledger_row_has_zero_based_chain_index(files):
    sql(files[0],'INSERT INTO trial_ledger VALUES (?,?,?)',('a'*64,0,json.dumps(reservation())))
    assert call(files)['inspection_status']=='STRUCTURE_VERIFIED'


def test_negative_chain_index_refused(files):
    sql(files[0],'INSERT INTO trial_ledger VALUES (?,?,?)',('a'*64,-1,json.dumps(reservation())))
    refuse(files,'REGISTRY_CORRUPT')


def test_extra_table_sharing_trigger_name_refused(files):
    with sqlite3.connect(str(files[0])) as c:
        ddl=c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='trial_ledger_no_update'").fetchone()[0]
        c.execute('DROP TRIGGER trial_ledger_no_update')
        c.execute('CREATE TABLE trial_ledger_no_update (x)')
        c.execute(ddl)
    refuse(files,'REGISTRY_SCHEMA_UNSUPPORTED')
