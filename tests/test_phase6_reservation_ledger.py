import hashlib,json,os
from concurrent.futures import ThreadPoolExecutor
import pytest
from app.research.phase6_reservation_ledger import ReservationLedger, inspect_reservations
from app.research.phase6_validation import canonical_hash

H='1'*64

def setup(tmp_path):
    root=tmp_path/'registry';root.mkdir();evidence=tmp_path/'evidence';evidence.mkdir()
    ledger=ReservationLedger(str(root),owner='test',registry_identity=H,max_bytes=100000)
    return ledger,root,evidence

def inputs(root,evidence,start=60000):
    p=dict(schema_version='p6.plan.v1',profile='p6.shadow.diagnostic.v1',owner='test',provider='synthetic',instrument='TEST:USD',timeframe_ms=60000,start_ms=start,end_ms=start+180000,warmup_bars=0,max_lateness_ms=1000,max_clock_skew_ms=10,max_decision_delay_ms=100,max_quote_delay_ms=100,tick_size='0.01',quantity_step='0.01',initial_cash='1000',max_target='0.5',fee_bps='10',slippage_bps='5',max_drawdown='0.2',intake_limit_bytes=10000)
    for k in ('program_context_hash','known_lineage_hash','strategy_code_hash','params_hash','method_contract_hash','anchor_trust_hash'):p[k]=H
    a=dict(schema_version='p6.authorization.v1',plan_hash=canonical_hash(p),owner='test',evidence_root=str(evidence),registry_root=str(root),anchor_endpoint_id='synthetic',approved_utc_ms=1,approval_reference='synthetic-only')
    return p,a

def reserve(l,root,evidence,start=60000):
    p,a=inputs(root,evidence,start);return l.reserve(p,a,reserved_utc_ms=2)

def test_consecutive_durable_bindings_and_inspection(tmp_path):
    l,root,evidence=setup(tmp_path);first=reserve(l,root,evidence);second=reserve(l,root,evidence,120000)
    assert first['reservation']['sequence']==1 and first['reservation']['previous_reservation_hash']=='0'*64
    assert second['reservation']['previous_reservation_hash']==first['run_id'] and second['reservation']['sequence']==2
    assert second['run_id']==canonical_hash(second['reservation'])
    assert second['status']=='RESERVED_UNANCHORED' and second['complete_family_N'] is None and not second['runtime_accepted']
    assert json.loads((root/'reservation-00000002.json').read_bytes())==second['reservation']
    snap=l.snapshot();l.close();before={p.name:p.read_bytes() for p in root.iterdir()}
    r=inspect_reservations(str(root),header=snap['header'],inventory=snap['inventory'])
    assert r['technical_verification']=='LOCAL_ONLY' and not r['runtime_accepted']
    assert before=={p.name:p.read_bytes() for p in root.iterdir()}
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):ReservationLedger(str(root),owner='test',registry_identity=H,max_bytes=100000)

def test_duplicate_consumption_terminal(tmp_path):
    l,r,e=setup(tmp_path);reserve(l,r,e)
    with pytest.raises(ValueError,match='DUPLICATE_PLAN'):reserve(l,r,e)
    assert l.snapshot()['lifecycle']=='INCOMPLETE' and len(l.snapshot()['acknowledged_reservations'])==1
    with pytest.raises(ValueError,match='REGISTRY_STOPPED'):reserve(l,r,e,120000)

@pytest.mark.parametrize('field,value',[('owner','other'),('plan_hash','2'*64),('approved_utc_ms',True),('approval_reference',''),('extra','forbidden')])
def test_invalid_authorization(tmp_path,field,value):
    l,r,e=setup(tmp_path);p,a=inputs(r,e);a[field]=value
    with pytest.raises(ValueError,match='AUTHORIZATION_INVALID'):l.reserve(p,a,reserved_utc_ms=2)
    assert len(list(r.iterdir()))==1

@pytest.mark.parametrize('reserved',[0,60000,True,-1,2**63])
def test_timing_before_start_and_after_approval(tmp_path,reserved):
    l,r,e=setup(tmp_path);p,a=inputs(r,e)
    with pytest.raises(ValueError,match='CLOCK_INVALID'):l.reserve(p,a,reserved_utc_ms=reserved)

def test_foreign_or_nested_evidence_root(tmp_path):
    l,r,e=setup(tmp_path);p,a=inputs(r,e);nested=r/'nested';nested.mkdir();a['evidence_root']=str(nested)
    with pytest.raises(ValueError):l.reserve(p,a,reserved_utc_ms=2)
    assert not (r/'reservation-00000001.json').exists()

def test_missing_evidence_not_created(tmp_path):
    l,r,e=setup(tmp_path);p,a=inputs(r,e);a['evidence_root']=str(tmp_path/'missing')
    with pytest.raises(ValueError,match='AUTHORIZATION_INVALID'):l.reserve(p,a,reserved_utc_ms=2)
    assert not (tmp_path/'missing').exists()

@pytest.mark.parametrize('nth',[1,2,3])
@pytest.mark.parametrize('after',[False,True])
def test_every_publication_failure_terminal(tmp_path,monkeypatch,nth,after):
    import app.research.phase6_reservation_ledger as m
    l,r,e=setup(tmp_path);original=m._publish;calls=[]
    def fail(*args):
        calls.append(1)
        if len(calls)==nth and not after:raise OSError('before publication')
        result=original(*args)
        if len(calls)==nth and after:raise OSError('after publication')
        return result
    monkeypatch.setattr(m,'_publish',fail)
    with pytest.raises((ValueError,OSError)):reserve(l,r,e)
    assert l.snapshot()['lifecycle']=='INCOMPLETE' and l.snapshot()['acknowledged_reservations']==[]
    with pytest.raises(ValueError,match='REGISTRY_STOPPED'):reserve(l,r,e)
    if nth==3 and after:assert (r/'reservation-00000001.json').exists()

def test_snapshot_and_input_isolation(tmp_path):
    l,r,e=setup(tmp_path);p,a=inputs(r,e);result=l.reserve(p,a,reserved_utc_ms=2);p['owner']='other';a['owner']='other';result['reservation']['sequence']=8
    snap=l.snapshot();snap['inventory'].clear();snap['acknowledged_reservations'].clear()
    assert len(l.snapshot()['acknowledged_reservations'])==1 and l.snapshot()['inventory'];l.close()

def test_competing_writer_and_active_inspection(tmp_path):
    l,r,e=setup(tmp_path)
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):ReservationLedger(str(r),owner='test',registry_identity=H,max_bytes=100000)
    s=l.snapshot()
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):inspect_reservations(str(r),header=s['header'],inventory=s['inventory'])
    reserve(l,r,e);l.close()

def test_foreign_thread_and_process(tmp_path):
    l,r,e=setup(tmp_path)
    def attempt():
        for action in (lambda:reserve(l,r,e),l.close):
            with pytest.raises(ValueError,match='REGISTRY_STOPPED'):action()
    with ThreadPoolExecutor(max_workers=1) as pool:pool.submit(attempt).result()
    pid=os.fork()
    if pid==0:
        try:attempt()
        except BaseException:os._exit(3)
        os._exit(0)
    _,status=os.waitpid(pid,0);assert os.waitstatus_to_exitcode(status)==0
    reserve(l,r,e);l.close()

@pytest.mark.parametrize('kind',['missing','extra','tamper','symlink'])
def test_inspection_rejects_corruption(tmp_path,kind):
    l,r,e=setup(tmp_path);reserve(l,r,e);s=l.snapshot();l.close();p=r/'reservation-00000001.json'
    if kind=='missing':p.unlink()
    elif kind=='extra':(r/'extra').write_text('x')
    elif kind=='tamper':p.chmod(0o600);p.write_bytes(b'{}')
    else:p.unlink();p.symlink_to(r/'registry.json')
    with pytest.raises(ValueError,match='INTEGRITY_FAILURE'):inspect_reservations(str(r),header=s['header'],inventory=s['inventory'])

def test_root_substitution_during_final_verification_not_acknowledged(tmp_path,monkeypatch):
    import app.research.phase6_reservation_ledger as m
    l,r,e=setup(tmp_path);original=m._verify_files;calls=0
    def swap(*args):
        nonlocal calls
        calls+=1;original(*args)
        if calls==2:r.rename(tmp_path/'moved');r.mkdir()
    monkeypatch.setattr(m,'_verify_files',swap)
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):reserve(l,r,e)
    assert l.snapshot()['lifecycle']=='INCOMPLETE' and not l.snapshot()['acknowledged_reservations']
    assert not list(r.iterdir())

def test_final_verification_reentry_cannot_acknowledge(tmp_path,monkeypatch):
    import app.research.phase6_reservation_ledger as m
    l,r,e=setup(tmp_path);original=m._verify_files;calls=0
    def nested(*args):
        nonlocal calls
        calls+=1;original(*args)
        if calls==2:
            with pytest.raises(ValueError,match='REGISTRY_STOPPED'):l.close()
    monkeypatch.setattr(m,'_verify_files',nested)
    with pytest.raises(ValueError,match='REGISTRY_STOPPED'):reserve(l,r,e)
    assert not l.snapshot()['acknowledged_reservations']

def test_real_process_exit_mid_reservation_preserves_partial(tmp_path):
    import subprocess,sys
    from pathlib import Path
    r=tmp_path/'registry';e=tmp_path/'evidence';r.mkdir();e.mkdir()
    code='''import os,runpy,sys
import app.research.phase6_reservation_ledger as m
helpers=runpy.run_path("tests/test_phase6_reservation_ledger.py")
l=m.ReservationLedger(sys.argv[1],owner="test",registry_identity="1"*64,max_bytes=100000)
original=m._publish
def crash(fd,name,raw):
    if name.startswith("reservation-"):
        f=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        os.write(f,raw[:10])
        os._exit(17)
    return original(fd,name,raw)
m._publish=crash
p,a=helpers["inputs"](sys.argv[1],sys.argv[2])
l.reserve(p,a,reserved_utc_ms=2)
'''
    result=subprocess.run([sys.executable,'-c',code,str(r),str(e)],cwd=Path(__file__).parents[1],timeout=15)
    assert result.returncode==17 and len((r/'reservation-00000001.json').read_bytes())==10
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):ReservationLedger(str(r),owner='test',registry_identity=H,max_bytes=100000)
    header=json.loads((r/'registry.json').read_bytes())
    with pytest.raises(ValueError,match='INTEGRITY_FAILURE'):inspect_reservations(str(r),header=header,inventory={})

def test_cleanup_failure_keeps_primary_error(tmp_path,monkeypatch):
    l,r,e=setup(tmp_path);original=l._release
    def fail():raise OSError('synthetic close fault')
    monkeypatch.setattr(l,'_release',fail);p,a=inputs(r,e);p['profile']='unsupported'
    with pytest.raises(ValueError,match='PLAN_INVALID'):l.reserve(p,a,reserved_utc_ms=2)
    assert l.snapshot()['lifecycle']=='INCOMPLETE'
    monkeypatch.setattr(l,'_release',original);l.close()

def test_explicit_close_failure_terminal(tmp_path,monkeypatch):
    l,r,e=setup(tmp_path);original=l._release
    def fail():raise OSError('synthetic close fault')
    monkeypatch.setattr(l,'_release',fail)
    with pytest.raises(OSError):l.close()
    with pytest.raises(ValueError,match='REGISTRY_STOPPED'):reserve(l,r,e)
    monkeypatch.setattr(l,'_release',original);l.close()

def test_budget_refuses_before_publication(tmp_path):
    r=tmp_path/'registry';e=tmp_path/'evidence';r.mkdir();e.mkdir()
    l=ReservationLedger(str(r),owner='test',registry_identity=H,max_bytes=1)
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):reserve(l,r,e)
    assert [p.name for p in r.iterdir()]==['registry.json']

@pytest.mark.parametrize('name',['H-025','h025'])
def test_frozen_root_excluded(tmp_path,name):
    r=tmp_path/name;r.mkdir()
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):ReservationLedger(str(r),owner='test',registry_identity=H,max_bytes=100000)
    assert not list(r.iterdir())

def test_symlink_root_and_absent_root_refused(tmp_path):
    real=tmp_path/'real';real.mkdir();link=tmp_path/'link';link.symlink_to(real,target_is_directory=True)
    for r in (link,tmp_path/'missing'):
        with pytest.raises(ValueError,match='STORAGE_REFUSED'):ReservationLedger(str(r),owner='test',registry_identity=H,max_bytes=100000)
    assert not list(real.iterdir()) and not (tmp_path/'missing').exists()

def test_inspector_expected_path_traversal_rejected(tmp_path):
    l,r,e=setup(tmp_path);s=l.snapshot();l.close()
    with pytest.raises(ValueError,match='INPUT_INVALID'):inspect_reservations(str(r),header=s['header'],inventory={'../outside':{'sha256':H,'size':1}})

def test_malformed_custom_input_no_hooks(tmp_path):
    l,r,e=setup(tmp_path)
    class Bad:
        def __deepcopy__(self,*args):raise AssertionError('hook called')
    with pytest.raises(ValueError,match='INPUT_INVALID'):l.reserve(Bad(),{},reserved_utc_ms=2)
    assert l.snapshot()['lifecycle']=='INCOMPLETE'
