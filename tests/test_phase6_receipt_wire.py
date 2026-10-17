import json
import pytest
from test_phase6_receipt_mtls import tls
from test_phase6_receipt_service import service
from test_phase6_receipt_state import req
from app.research.phase6_receipt_mtls import ReceiptMtlsBoundary
from app.research.phase6_receipt_wire import exchange,serve_one,_IO,_length


def test_real_tls_http_append_to_actual_operations(tls):
    digests,connect=tls;old,db,index,s3=service();out=[]
    boundary=ReceiptMtlsBoundary(client_runs={digests['client']:['a'*64]},**{k[1:]:v for k,v in old.handlers.items()})
    answers,errors=connect(boundary,server_call=lambda conn:serve_one(conn,boundary=boundary,expected_host='localhost'),
        client_call=lambda conn:out.append(exchange(conn,expected_hostname='localhost',server_cert_sha256=digests['server'],path='/append',body=json.dumps(req()))))
    assert not errors and out[0]['status_code']==200 and not out[0]['receipt_verified'] and not out[0]['retry_allowed']
    assert json.loads(out[0]['body'])['receipt'] and index.puts==1


class Bytes:
    def __init__(self,data):self.data=data
    def settimeout(self,t):assert 0<t<=10
    def recv(self,n):out=self.data[:n];self.data=self.data[n:];return out


@pytest.mark.parametrize('header',[
    b'POST /append HTTP/1.1\r\nContent-Length: 1\r\ncontent-length: 1\r\n\r\n',
    b'POST /append HTTP/1.1\r\n Content-Length: 1\r\n\r\n',
    b'POST /append HTTP/1.1\r\nX: a\tb\r\n\r\n',
    b'x'*8193,b'incomplete'])
def test_header_smuggling_and_limits_refused(header):
    with pytest.raises(ValueError):_IO(Bytes(header)).header()


@pytest.mark.parametrize('length',['0','-1','+1','01','1,1','16385','99999999'])
def test_invalid_lengths_refused(length):
    with pytest.raises(ValueError):_length({'content-length':length},16384)


def test_server_pin_rejected_before_request_sent(tls):
    digests,connect=tls;received=[]
    def server(conn):
        try:received.append(conn.recv(1))
        except OSError:received.append(b'')
    def client(conn):
        with pytest.raises(ValueError):exchange(conn,expected_hostname='localhost',server_cert_sha256='0'*64,path='/append',body=json.dumps(req()))
    connect(None,server_call=server,client_call=client)
    assert received==[b'']


def test_absolute_deadline_cannot_be_extended_by_short_reads(monkeypatch):
    from app.research import phase6_receipt_wire as module
    values=iter([0,1,11]);monkeypatch.setattr(module.time,'monotonic',lambda:next(values))
    with pytest.raises(ValueError):module._IO(Bytes(b'ab')).read(2)


@pytest.mark.parametrize('extra',[b'Transfer-Encoding: chunked\r\n',b'Expect: 100-continue\r\n',b'Host: other\r\n'])
def test_forbidden_http_headers_do_not_invoke_service(tls,extra):
    digests,connect=tls;seen=[];refused=[]
    class Boundary:
        def handle(self,*a,**k):seen.append(True)
    def server(conn):
        try:serve_one(conn,boundary=Boundary(),expected_host='localhost')
        except ValueError:refused.append(True)
    def client(conn):
        data=b'POST /append HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n'+extra+b'\r\n{}'
        conn.sendall(data)
        try:conn.recv(1)
        except OSError:pass
    connect(None,server_call=server,client_call=client)
    assert refused==[True] and not seen


@pytest.mark.parametrize('side',['server','client'])
def test_cleanup_failure_does_not_mask_primary_refusal(side):
    class Broken:
        def close(self):raise OSError('cleanup')
    with pytest.raises(ValueError,match='RECEIPT_WIRE_REFUSED'):
        if side=='server':serve_one(Broken(),boundary=None,expected_host='localhost')
        else:exchange(Broken(),expected_hostname='localhost',server_cert_sha256='0'*64,path='/append',body='{}')


@pytest.mark.parametrize('side',['server','client'])
def test_cleanup_failure_after_success_is_reported(monkeypatch,side):
    import hashlib
    from types import SimpleNamespace
    from app.research import phase6_receipt_wire as module
    request=b'POST /append HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}'
    response=b'HTTP/1.1 200 Result\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}'
    class Synthetic(Bytes):
        server_side=side=='server'
        context=SimpleNamespace(verify_mode=module.ssl.CERT_REQUIRED,check_hostname=True)
        server_hostname='localhost'
        def version(self):return 'TLSv1.3'
        def getpeercert(self,**kw):return b'synthetic'
        def sendall(self,data):pass
        def close(self):raise OSError('cleanup')
    monkeypatch.setattr(module.ssl,'SSLSocket',Synthetic)
    conn=Synthetic(request if side=='server' else response)
    with pytest.raises(OSError,match='cleanup'):
        if side=='server':serve_one(conn,boundary=SimpleNamespace(handle=lambda *a,**kw:{'statusCode':200,'body':'{}'}),expected_host='localhost')
        else:exchange(conn,expected_hostname='localhost',server_cert_sha256=hashlib.sha256(b'synthetic').hexdigest(),path='/append',body='{}')
