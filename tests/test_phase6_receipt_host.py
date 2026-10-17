import itertools
import json
import socket
import threading
import pytest
from test_phase6_receipt_mtls import tls
from test_phase6_receipt_service import service
from test_phase6_receipt_state import req
from app.research import phase6_clockbound as clock_module
from app.research.phase6_receipt_host import ClockBoundReceiptHost
from app.research.phase6_receipt_connection import request_once
from app.research.phase6_object_store import ObjectWriter
from app.research.phase6_anchor_validation import _hash


class Native:
    value=(999000000,1001000000,1)
    def __init__(self,*a):self.closed=False
    def interval(self):return self.value
    def close(self):self.closed=True


def native(monkeypatch):
    monkeypatch.setattr(clock_module,'_Native',Native)
    monkeypatch.setattr(clock_module.time,'CLOCK_BOOTTIME',7,raising=False)
    ticks=itertools.count(1000000,1000)
    monkeypatch.setattr(clock_module.time,'clock_gettime_ns',lambda _:next(ticks))
    monkeypatch.setattr(clock_module.time,'monotonic_ns',lambda:next(ticks))


def host(tmp_path,monkeypatch,client_digest):
    native(monkeypatch)
    def builder(**config):
        for k in ('clock','domain_name','principal_arns'):config.pop(k)
        config['trust']['max_clock_skew_ms']=100;config['expected_trust_hash']=_hash(config['trust'])
        root=tmp_path/'clock';root.mkdir()
        writer=ObjectWriter(str(root),owner=config['owner'],run_id='a'*64,max_bytes=1000000)
        return ClockBoundReceiptHost(writer,library_path='/synthetic/libclockbound.so',library_sha256='a'*64,
            client_runs={client_digest:['a'*64]},**config)
    return service(builder=builder)


def test_tcp_tls_http_native_clock_to_retained_receipt(tmp_path,monkeypatch,tls):
    digests,connect=tls;errors=[];out=[]
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen(1);port=listener.getsockname()[1]
        def worker():
            try:
                h,db,index,s3=host(tmp_path,monkeypatch,digests['client'])
                out.append(h.serve(listener,context=connect.server_context,hostname='localhost',max_requests=1))
                assert h.stopped and h._clock.native.closed and index.puts==1
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=worker);thread.start()
        try:
            result=request_once(port=port,context=connect.client_context(),hostname='localhost',server_cert_sha256=digests['server'],path='/append',body=json.dumps(req()))
        finally:thread.join(timeout=15)
    assert not thread.is_alive() and not errors
    assert result['status_code']==200 and not result['receipt_verified']
    assert out[0]['clock_observations']==3
    root=tmp_path/'clock';last=json.loads((root/(out[0]['clock_head']+'.blob')).read_text())
    assert last['sequence']==3 and last['sample']['probe']['source_identity']=='clockbound-2.0.3-'+'a'*64


def test_native_failure_stops_host_before_storage(tmp_path,monkeypatch):
    h,db,index,s3=host(tmp_path,monkeypatch,'a'*64)
    h._clock.native.value=(999000000,1001000000,2)
    with pytest.raises(ValueError):h._utc()
    assert h.stopped and not db.calls and index.puts==0 and not s3.calls
    with pytest.raises(ValueError):h._utc()
    h.close()


def test_clock_evidence_failure_stops_before_returning_time(tmp_path,monkeypatch):
    h,db,index,s3=host(tmp_path,monkeypatch,'a'*64)
    def fail(*a,**kw):raise OSError('unknown clock evidence write')
    monkeypatch.setattr(h._writer,'put',fail)
    with pytest.raises(OSError):h._utc()
    assert h.stopped and h._clock.stopped and not db.calls
    h.close()


def test_verified_client_through_real_tcp_tls_host(tmp_path,monkeypatch,tls):
    from app.research.phase6_receipt_client import VerifiedReceiptClient
    digests,connect=tls;errors=[];out=[]
    config,_,_,_=service(builder=lambda **kw:kw)
    trust=config['trust'];trust['max_clock_skew_ms']=100
    root=tmp_path/'client';root.mkdir();writer=ObjectWriter(str(root),owner='test',run_id='a'*64,max_bytes=1000000)
    ticks=itertools.count(1)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen(1);port=listener.getsockname()[1]
        def worker():
            try:
                h,db,index,s3=host(tmp_path,monkeypatch,digests['client'])
                out.append(h.serve(listener,context=connect.server_context,hostname='localhost',max_requests=2))
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=worker);thread.start()
        c=VerifiedReceiptClient(writer,run_id='a'*64,owner='test',trust=trust,expected_trust_hash=_hash(trust),
            clock=lambda:dict(utc_ms=1000,monotonic_ns=next(ticks),clock_session_id='client'),
            port=port,context=connect.client_context(),hostname='localhost',server_cert_sha256=digests['server'])
        try:package=c.append(subject_kind='RESERVATION',subject_hash='a'*64)
        finally:c.close();thread.join(timeout=15)
    assert not thread.is_alive() and not errors
    assert package['head']['anchor_hash']==_hash(package['receipts'][0])
    assert out[0]['clock_observations']==5
    recorded=[json.loads(f.read_text()) for f in root.glob('*.blob')]
    assert any(x['kind']=='verification' and x['data']['result']['head_status']=='MATCHED' for x in recorded)


def test_clock_trace_failure_after_commit_cannot_acknowledge(tmp_path,monkeypatch):
    from app.research.phase6_receipt_append import AppendUncertain
    h,db,index,s3=host(tmp_path,monkeypatch,'a'*64);original=h._writer.put;count=0
    def fail(raw,**kwargs):
        nonlocal count
        count+=1
        if count==3:raise OSError('final clock trace publication unknown')
        return original(raw,**kwargs)
    monkeypatch.setattr(h._writer,'put',fail)
    with pytest.raises(AppendUncertain):h._boundary._dispatch.handlers['/append'](req())
    assert db.item['state']=={'S':'COMMITTED'} and index.puts==1 and h.stopped
    h.close()
