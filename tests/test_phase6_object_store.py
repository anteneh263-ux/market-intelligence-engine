import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from app.research.phase6_object_store import ObjectWriter, inspect_objects

RUN='1'*64

def header(limit=100):
    return dict(schema_version='p6.object_store.v1',owner='synthetic',run_id=RUN,max_bytes=limit)

def writer(root,limit=100):
    return ObjectWriter(str(root),owner='synthetic',run_id=RUN,max_bytes=limit)

def inspect(root,objects,limit=100):
    return inspect_objects(str(root),header=header(limit),objects=objects)

def saved(root,data=b'abc'):
    w=writer(root);h=w.put(data);w.close();return h

def test_publication_and_read_only_inspection(tmp_path):
    w=writer(tmp_path);h=w.put(b'abc');assert h==hashlib.sha256(b'abc').hexdigest()
    assert w.put(b'abc')==h
    w.close();before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    assert inspect(tmp_path,{h:3})==dict(schema_version='p6.object_inspection.v1',structural_status='VALID',technical_verification='LOCAL_ONLY',object_count=1,total_bytes=3,runtime_accepted=False)
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(ValueError,match='WRITER_STOPPED'):w.put(b'd')
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):writer(tmp_path)

def test_active_writer_lock(tmp_path):
    w=writer(tmp_path)
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):writer(tmp_path)
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):inspect(tmp_path,{})
    w.close()

def test_no_implicit_root(tmp_path):
    p=tmp_path/'absent'
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):writer(p)
    assert not p.exists()

@pytest.mark.parametrize('name',['H-025','h025','h_025'])
def test_frozen_root_refused(tmp_path,name):
    p=tmp_path/name;p.mkdir()
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):writer(p)
    assert list(p.iterdir())==[]

def test_nonempty_root_preserved(tmp_path):
    p=tmp_path/'old';p.write_bytes(b'keep')
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):writer(tmp_path)
    assert p.read_bytes()==b'keep' and list(tmp_path.iterdir())==[p]

def test_symlink_root_and_ancestor(tmp_path):
    real=tmp_path/'real';real.mkdir();child=real/'child';child.mkdir()
    link=tmp_path/'link';link.symlink_to(real,target_is_directory=True)
    for p in (link,link/'child'):
        with pytest.raises(ValueError,match='STORAGE_REFUSED'):writer(p)
    assert list(child.iterdir())==[]

@pytest.mark.parametrize('path',['relative','/private/tmp/../tmp','/private/tmp/'])
def test_noncanonical_path(path):
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):writer(path)

def test_budget_and_empty_blob(tmp_path):
    w=writer(tmp_path,3);h=w.put(b'abc');z=w.put(b'');assert w.put(b'abc')==h
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):w.put(b'x')
    with pytest.raises(ValueError,match='WRITER_STOPPED'):w.put(b'')
    w.close();assert inspect(tmp_path,{h:3,z:0},3)['total_bytes']==3

@pytest.mark.parametrize('kind',['tamper','missing','extra','symlink','hardlink','directory','fifo'])
def test_corruption(tmp_path,kind):
    h=saved(tmp_path);p=tmp_path/(h+'.blob')
    if kind=='tamper':p.chmod(0o600);p.write_bytes(b'xyz')
    elif kind=='missing':p.unlink()
    elif kind=='extra':(tmp_path/'extra').write_bytes(b'x')
    elif kind=='symlink':p.unlink();p.symlink_to(tmp_path/'writer.json')
    elif kind=='hardlink':os.link(p,tmp_path.parent/(tmp_path.name+'-link'))
    elif kind=='directory':p.unlink();p.mkdir()
    elif kind=='fifo':p.unlink();os.mkfifo(p)
    with pytest.raises(ValueError,match='INTEGRITY_FAILURE'):inspect(tmp_path,{h:3})

def test_header_tamper(tmp_path):
    h=saved(tmp_path);p=tmp_path/'writer.json';p.chmod(0o600);p.write_text(json.dumps(header()))
    with pytest.raises(ValueError,match='INTEGRITY_FAILURE'):inspect(tmp_path,{h:3})

def test_expected_inventory_not_inferred(tmp_path):
    h=saved(tmp_path)
    with pytest.raises(ValueError,match='INTEGRITY_FAILURE'):inspect(tmp_path,{})
    with pytest.raises(ValueError,match='INTEGRITY_FAILURE'):inspect(tmp_path,{h:2})
    with pytest.raises(ValueError,match='INPUT_INVALID'):inspect(tmp_path,{'../x':3})

def test_fsync_failure_preserves_and_poison(tmp_path,monkeypatch):
    w=writer(tmp_path)
    def fail(fd):raise OSError('synthetic fsync fault')
    monkeypatch.setattr(os,'fsync',fail)
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):w.put(b'abc')
    with pytest.raises(ValueError,match='WRITER_STOPPED'):w.put(b'abc')
    w.close();assert len(list(tmp_path.iterdir()))==2

def test_short_write_is_completed(tmp_path,monkeypatch):
    w=writer(tmp_path);original=os.write
    monkeypatch.setattr(os,'write',lambda fd,data:original(fd,data[:1]))
    h=w.put(b'abc');w.close();assert inspect(tmp_path,{h:3})['total_bytes']==3

def test_zero_write_poison(tmp_path,monkeypatch):
    w=writer(tmp_path);monkeypatch.setattr(os,'write',lambda fd,data:0)
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):w.put(b'abc')
    with pytest.raises(ValueError,match='WRITER_STOPPED'):w.put(b'abc')
    w.close();assert (tmp_path/(hashlib.sha256(b'abc').hexdigest()+'.blob')).read_bytes()==b''

def test_root_replacement_refused(tmp_path):
    root=tmp_path/'root';root.mkdir();w=writer(root)
    root.rename(tmp_path/'moved');root.mkdir()
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):w.put(b'abc')
    w.close();assert list(root.iterdir())==[]

def test_process_exit_never_resumes(tmp_path):
    code='from app.research.phase6_object_store import ObjectWriter; import os,sys; w=ObjectWriter(sys.argv[1],owner="synthetic",run_id="1"*64,max_bytes=100); w.put(b"abc"); os._exit(7)'
    result=subprocess.run([sys.executable,'-c',code,str(tmp_path)],cwd=Path(__file__).parents[1],timeout=15)
    assert result.returncode==7
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):writer(tmp_path)
    h=hashlib.sha256(b'abc').hexdigest();assert inspect(tmp_path,{h:3})['technical_verification']=='LOCAL_ONLY'

def test_fork_inherited_writer_refused(tmp_path):
    w=writer(tmp_path);pid=os.fork()
    if pid==0:
        try:w.put(b'bad')
        except ValueError as e:os._exit(0 if str(e)=='WRITER_STOPPED' else 2)
        os._exit(3)
    _,status=os.waitpid(pid,0);assert os.waitstatus_to_exitcode(status)==0
    h=w.put(b'abc');w.close();assert inspect(tmp_path,{h:3})['object_count']==1

def test_child_close_does_not_unlock_parent(tmp_path):
    w=writer(tmp_path);pid=os.fork()
    if pid==0:
        w.close()
        try:inspect(tmp_path,{})
        except ValueError as e:os._exit(0 if str(e)=='STORAGE_REFUSED' else 2)
        os._exit(3)
    _,status=os.waitpid(pid,0);assert os.waitstatus_to_exitcode(status)==0
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):inspect(tmp_path,{})
    h=w.put(b'abc');w.close();assert inspect(tmp_path,{h:3})['object_count']==1

def test_constructor_fsync_failure_keeps_claim(tmp_path,monkeypatch):
    def fail(fd):raise OSError('synthetic constructor failure')
    monkeypatch.setattr(os,'fsync',fail)
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):writer(tmp_path)
    assert (tmp_path/'writer.json').exists()
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):writer(tmp_path)

def test_dedup_tamper_poison(tmp_path):
    w=writer(tmp_path);h=w.put(b'abc');p=tmp_path/(h+'.blob');p.chmod(0o600);p.write_bytes(b'xyz')
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):w.put(b'abc')
    with pytest.raises(ValueError,match='WRITER_STOPPED'):w.put(b'new')
    w.close()

def test_input_error_does_not_write_or_poison(tmp_path):
    w=writer(tmp_path)
    with pytest.raises(ValueError,match='INPUT_INVALID'):w.put('abc')
    assert len(list(tmp_path.iterdir()))==1
    w.put(b'abc');w.close()

@pytest.mark.parametrize('bad',[True,0,-1,2**63,1.0])
def test_bad_budget_no_claim(tmp_path,bad):
    with pytest.raises(ValueError,match='INPUT_INVALID'):writer(tmp_path,bad)
    assert list(tmp_path.iterdir())==[]

def test_actual_process_exit_mid_write_preserves_partial(tmp_path):
    code='''from app.research.phase6_object_store import ObjectWriter
import os,sys
w=ObjectWriter(sys.argv[1],owner="synthetic",run_id="1"*64,max_bytes=100)
original=os.write
def crash(fd,data):
    original(fd,data[:1])
    os._exit(8)
os.write=crash
w.put(b"abc")
'''
    result=subprocess.run([sys.executable,'-c',code,str(tmp_path)],cwd=Path(__file__).parents[1],timeout=15)
    assert result.returncode==8
    h=hashlib.sha256(b'abc').hexdigest();assert (tmp_path/(h+'.blob')).read_bytes()==b'a'
    with pytest.raises(ValueError,match='INTEGRITY_FAILURE'):inspect(tmp_path,{h:3})
    with pytest.raises(ValueError,match='STORAGE_REFUSED'):writer(tmp_path)

def test_cross_thread_writer_refused_before_io(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    w=writer(tmp_path,1)
    def attempt():
        outcomes=[]
        for action in (lambda:w.put(b'a'),w.close):
            try:action();outcomes.append('ACCEPTED')
            except ValueError as e:outcomes.append(str(e))
        return outcomes
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(attempt).result()==['WRITER_STOPPED','WRITER_STOPPED']
    assert len(list(tmp_path.iterdir()))==1
    h=w.put(b'b');w.close();assert inspect(tmp_path,{h:1},1)['total_bytes']==1
