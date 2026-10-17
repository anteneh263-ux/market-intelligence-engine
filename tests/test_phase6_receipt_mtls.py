import datetime
import hashlib
import json
import socket
import ssl
import threading
import pytest
pytest.importorskip('cryptography',reason='Install phase6-anchor')
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from app.research.phase6_receipt_mtls import ReceiptMtlsBoundary
from test_phase6_receipt_service import service
from test_phase6_receipt_state import req


@pytest.fixture
def tls(tmp_path):
    now=datetime.datetime.now(datetime.timezone.utc)
    ca_key=ec.generate_private_key(ec.SECP256R1())
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'synthetic-test-ca')])
    def issue(subject,key,ca=False):
        builder=(x509.CertificateBuilder().subject_name(subject).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=ca,path_length=None),critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=False,
                data_encipherment=False,key_agreement=False,key_cert_sign=ca,crl_sign=ca,
                encipher_only=False,decipher_only=False),critical=True))
        if not ca:builder=builder.add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False)
        return builder.sign(ca_key,hashes.SHA256())
    ca=issue(name,ca_key,True);(tmp_path/'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    digests={}
    for who in ('server','client','other'):
        key=ec.generate_private_key(ec.SECP256R1())
        cert=issue(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,who)]),key)
        (tmp_path/(who+'.pem')).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (tmp_path/(who+'.key')).write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        digests[who]=hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    server=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server.minimum_version=ssl.TLSVersion.TLSv1_2
    server.verify_mode=ssl.CERT_REQUIRED;server.load_verify_locations(tmp_path/'ca.pem')
    server.load_cert_chain(tmp_path/'server.pem',tmp_path/'server.key')
    def exchange(boundary,*,who='client',path='/append',body=None,method='POST',server_call=None,client_call=None):
        client=ssl.create_default_context(cafile=str(tmp_path/'ca.pem'))
        if who:client.load_cert_chain(tmp_path/(who+'.pem'),tmp_path/(who+'.key'))
        left,right=socket.socketpair();left.settimeout(3);right.settimeout(3);answers=[];errors=[]
        def worker():
            try:
                with server.wrap_socket(left,server_side=True) as conn:
                    answers.append(server_call(conn) if server_call else boundary.handle(conn,method=method,path=path,body=json.dumps(req()) if body is None else body))
            except ssl.SSLError as exc:errors.append(str(exc))
        thread=threading.Thread(target=worker);thread.start()
        try:
            with client.wrap_socket(right,server_hostname='localhost') as conn:
                if client_call:client_call(conn)
        except ssl.SSLError as exc:errors.append(str(exc))
        finally:
            thread.join(timeout=5);left.close();right.close()
        assert not thread.is_alive()
        return answers,errors
    return digests,exchange


def test_real_tls_append_uses_existing_storage_operations(tls):
    digests,exchange=tls;old,db,index,s3=service()
    boundary=ReceiptMtlsBoundary(client_runs={digests['client']:['a'*64]},**{k[1:]:v for k,v in old.handlers.items()})
    answers,errors=exchange(boundary)
    assert not errors and answers[0]['statusCode']==200
    assert index.puts==1 and sum(x[0]=='put' for x in s3.calls)==1


@pytest.mark.parametrize('fault',['other_cert','no_cert','wrong_run','get','duplicate_json','fake_iam'])
def test_tls_and_run_failures_never_reach_operations(tls,fault):
    digests,exchange=tls;seen=[]
    boundary=ReceiptMtlsBoundary(client_runs={digests['client']:['b'*64 if fault=='wrong_run' else 'a'*64]},
        append=lambda q:seen.append(q),lookup=lambda q:seen.append(q),head=lambda q:seen.append(q))
    kwargs={}
    if fault=='other_cert':kwargs['who']='other'
    if fault=='no_cert':kwargs['who']=None
    if fault=='get':kwargs['method']='GET'
    if fault=='duplicate_json':kwargs['body']='{"run_id":"a","run_id":"b"}'
    if fault=='fake_iam':kwargs['body']='{"requestContext":{"authorizer":{"iam":{"userArn":"trusted"}}}}'
    answers,errors=exchange(boundary,**kwargs)
    assert not seen
    if fault=='no_cert':assert errors and not answers
    else:assert answers[0]['statusCode'] in (400,403)


def test_plain_socket_or_json_cannot_supply_tls_identity():
    seen=[];b=ReceiptMtlsBoundary(client_runs={'a'*64:['a'*64]},append=seen.append,lookup=seen.append,head=seen.append)
    assert b.handle({'peer_certificate':'a'*64},method='POST',path='/append',body=json.dumps(req()))['statusCode']==403
    assert not seen


@pytest.mark.parametrize('path,payload',[('/lookup',{'request':req(),'nonce':'c'*64}),('/head',{'run_id':'a'*64,'nonce':'c'*64})])
def test_run_authorization_covers_read_only_routes(tls,path,payload):
    digests,exchange=tls;seen=[]
    b=ReceiptMtlsBoundary(client_runs={digests['client']:['b'*64]},append=seen.append,lookup=seen.append,head=seen.append)
    answers,errors=exchange(b,path=path,body=json.dumps(payload))
    assert not errors and answers[0]['statusCode']==403 and not seen


def test_tls_append_lookup_and_head_preserve_original_receipt(tls):
    digests,exchange=tls;old,db,index,s3=service()
    b=ReceiptMtlsBoundary(client_runs={digests['client']:['a'*64]},**{k[1:]:v for k,v in old.handlers.items()})
    appended,errors=exchange(b);assert not errors
    lookup,errors=exchange(b,path='/lookup',body=json.dumps({'request':req(),'nonce':'c'*64}));assert not errors
    head,errors=exchange(b,path='/head',body=json.dumps({'run_id':'a'*64,'nonce':'d'*64}));assert not errors
    assert lookup[0]['statusCode']==head[0]['statusCode']==200
    assert json.loads(appended[0]['body'])['receipt']==json.loads(lookup[0]['body'])['receipt']
    assert json.loads(head[0]['body'])['head']['nonce']=='d'*64 and index.puts==1
