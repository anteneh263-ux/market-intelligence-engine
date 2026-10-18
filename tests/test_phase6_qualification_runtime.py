"""Actual CLI assembly with real local TLS; substituted native FFI and AWS only."""
import copy
import hashlib
import itertools
import json
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from app.research import phase6_qualification_runtime as runtime
from app.research import phase6_clockbound as clocks
from app.research import phase6_receipt_host as hosts
from app.research.phase6_anchor_validation import _hash
from app.research.phase6_source_intake import encoded
from test_phase6_receipt_mtls import tls
from test_phase6_receipt_service import service
from test_phase6_receipt_append import KEY


@pytest.fixture
def config(tmp_path, tls):
    digests, contexts = tls
    now = int(time.time() * 1000)
    ca = (tmp_path / 'ca.pem').read_text()
    ca_hash = hashlib.sha256(x509.load_pem_x509_certificate(ca.encode()).public_bytes(serialization.Encoding.DER)).hexdigest()
    c = dict(schema_version='p6.aws_receipt_qualification.v1', qualification_id=runtime.QUALIFICATION,
        account_id=runtime.ACCOUNT, region=runtime.REGION, ca_pem=ca, ca_sha256=ca_hash,
        library_path='/opt/mie/libclockbound.so', library_sha256='a' * 64,
        trust=dict(schema_version='p6.anchor_trust.v1', service_identity=runtime.PREFIX + '-receipt',
            endpoint_id=runtime.PREFIX + '-ssm-mtls-8443', algorithm='Ed25519',
            public_key_hex=KEY.public_key().public_bytes_raw().hex(), not_before_utc_ms=now-1000,
            not_after_utc_ms=now+120000, max_head_age_ms=5000, max_clock_skew_ms=100))
    materials = {}
    for i, (role, fixture, roots) in enumerate([('source', 'client', ['clock','client','result']),
                                               ('receipt', 'server', ['clock','result'])], 1):
        c[role] = dict(role_arn='arn:aws:iam::' + runtime.ACCOUNT + ':role/' + runtime.PREFIX + '-' + role,
            instance_id='i-' + str(i)*17,
            secret_arn='arn:aws:secretsmanager:' + runtime.REGION + ':' + runtime.ACCOUNT + ':secret:'
                + runtime.PREFIX + '/' + role + '-identity-ABCDEF',
            secret_version_id=str(i)*32, certificate_sha256=digests[fixture],
            roots={name:str(tmp_path / (role + '-' + name)) for name in roots})
        for root in c[role]['roots'].values(): Path(root).mkdir()
        materials[role] = dict(certificate_pem=(tmp_path/(fixture+'.pem')).read_text(),
                               private_key_pem=(tmp_path/(fixture+'.key')).read_text())
    materials['receipt']['signing_private_key_hex']=bytes(range(32)).hex()
    return c, materials, contexts, now


def clients(c, role, material):
    arn='arn:aws:sts::'+runtime.ACCOUNT+':assumed-role/'+c[role]['role_arn'].rsplit('/',1)[1]+'/'+c[role]['instance_id']
    seen=[]
    def secret(**kw):
        seen.append(kw)
        assert kw == dict(SecretId=c[role]['secret_arn'], VersionId=c[role]['secret_version_id'])
        return dict(ARN=kw['SecretId'], VersionId=kw['VersionId'], SecretString=json.dumps(material))
    return SimpleNamespace(get_caller_identity=lambda:dict(Account=runtime.ACCOUNT,Arn=arn)), SimpleNamespace(get_secret_value=secret), seen


@pytest.mark.parametrize('fault', ['account','region','role_alias','same_instance','same_cert','roots','nested_root',
                                   'h025','secret_alias','boolean_clock','excess_window','extra'])
def test_bad_config_refused(config, fault):
    c,_,_,_=config
    if fault=='account': c['account_id']='123456789012'
    if fault=='region': c['region']='us-east-1'
    if fault=='role_alias': c['source']['role_arn']='arn:aws:iam::'+runtime.ACCOUNT+':root'
    if fault=='same_instance': c['source']['instance_id']=c['receipt']['instance_id']
    if fault=='same_cert': c['source']['certificate_sha256']=c['receipt']['certificate_sha256']
    if fault=='roots': c['source']['roots']['result']=c['source']['roots']['clock']
    if fault=='nested_root': c['source']['roots']['result']=c['source']['roots']['clock']+'/child'
    if fault=='h025': c['source']['roots']['result']='/tmp/H-025/result'
    if fault=='secret_alias': c['source']['secret_arn']='latest'
    if fault=='boolean_clock': c['trust']['max_clock_skew_ms']=True
    if fault=='excess_window': c['trust']['not_after_utc_ms']=c['trust']['not_before_utc_ms']+3600001
    if fault=='extra': c['activate_live']=True
    with pytest.raises(Exception): runtime.validate_config(c)


@pytest.mark.parametrize('fault', ['root_identity','wrong_version','wrong_arn','wrong_certificate','wrong_key','wrong_signing_key','extra_secret','extra_certificate','extra_key','extra_ca'])
def test_preflight_failure_never_creates_store(config, monkeypatch, fault):
    c,materials,_,_=config
    role='receipt';material=materials[role]
    sts,sm,seen=clients(c,role,material)
    if fault=='root_identity': sts.get_caller_identity=lambda:dict(Account=runtime.ACCOUNT,Arn='arn:aws:iam::'+runtime.ACCOUNT+':root')
    if fault=='wrong_certificate': material['certificate_pem']=materials['source']['certificate_pem']
    if fault=='wrong_key': material['private_key_pem']=materials['source']['private_key_pem']
    if fault=='wrong_signing_key': material['signing_private_key_hex']='f'*64
    if fault=='extra_secret': material['extra']='do-not-print'
    if fault=='extra_certificate': material['certificate_pem']+=materials['source']['certificate_pem']
    if fault=='extra_key': material['private_key_pem']+=materials['source']['private_key_pem']
    if fault=='extra_ca': c['ca_pem']+=c['ca_pem']
    if fault in ('wrong_version','wrong_arn'):
        original=sm.get_secret_value
        def wrong(**kw):
            result=original(**kw);result['VersionId' if fault=='wrong_version' else 'ARN']='wrong';return result
        sm.get_secret_value=wrong
    with pytest.raises(Exception):runtime.run(c,role,sts=sts,secrets_client=sm)
    assert all(not list(Path(p).iterdir()) for p in c[role]['roots'].values())
    if fault=='root_identity':assert not seen


def test_config_hash_duplicate_json_and_symlink_refused(config,tmp_path):
    c,_,_,_=config;p=tmp_path/'configuration.json';p.write_bytes(encoded(c))
    h=hashlib.sha256(p.read_bytes()).hexdigest();assert runtime.read_config(str(p),h)==c
    with pytest.raises(Exception):runtime.read_config(str(p),'0'*64)
    link=tmp_path/'config-link';link.symlink_to(p)
    with pytest.raises(Exception):runtime.read_config(str(link),h)
    with pytest.raises(Exception):runtime.parse(b'{"a":1,"a":2}')


@pytest.mark.parametrize('fault',[None,'lost_reply','clock','tampered_store','footer_failure'])
def test_actual_runtime_assembly(config,monkeypatch,fault):
    c,materials,contexts,now=config
    ticks=itertools.count(1)
    class Native:
        def __init__(self,*a):pass
        def interval(self):return ((now-1)*1000000,(now+1)*1000000,2 if fault=='clock' else 1)
        def close(self):pass
    monkeypatch.setattr(clocks,'_Native',Native)
    monkeypatch.setattr(clocks.time,'CLOCK_BOOTTIME',7,raising=False)
    tick=lambda:1000000+next(ticks)*100
    monkeypatch.setattr(clocks.time,'clock_gettime_ns',lambda _:tick())
    monkeypatch.setattr(clocks.time,'monotonic_ns',tick)
    monkeypatch.setattr(runtime,'HOSTNAME','localhost')
    monkeypatch.setattr(runtime,'tls_context',lambda c,role,m:contexts.server_context if role=='receipt' else contexts.client_context())
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    monkeypatch.setattr(runtime,'PORT',port)
    _,db,index,s3=service();objects={};put=s3.put_object;get=s3.get_object;retention=s3.get_object_retention
    def put_many(**kw):
        result=put(**kw);objects[kw['Key']]=copy.deepcopy(kw);return result
    def get_many(**kw):s3.saved=objects[kw['Key']];return get(**kw)
    def retained_many(**kw):s3.saved=objects[kw['Key']];return retention(**kw)
    s3.put_object=put_many;s3.get_object=get_many;s3.get_object_retention=retained_many
    ready=threading.Event();out=[];errors=[];accept=hosts.accept_once
    def accepting(*a,**kw):
        ready.set();kw['idle_timeout_seconds']=3;return accept(*a,**kw)
    monkeypatch.setattr(hosts,'accept_once',accepting)
    if fault=='lost_reply':
        from app.research import phase6_receipt_client as cm
        original=cm.request_once
        def lost(**kw):
            response=original(**kw)
            if kw['path']=='/append':raise TimeoutError('unknown response')
            return response
        monkeypatch.setattr(cm,'request_once',lost)
    if fault=='tampered_store':
        original=runtime.inspect_writer
        def inspect(writer):
            if writer.root==c['source']['roots']['clock']:(Path(writer.root)/'unexpected').write_text('tamper')
            return original(writer)
        monkeypatch.setattr(runtime,'inspect_writer',inspect)
    if fault=='footer_failure':
        original=runtime.ObjectWriter.put
        def object_put(writer,raw,**kw):
            if json.loads(raw).get('schema_version')=='p6.aws_receipt_qualification_result.v1' and writer.root==c['source']['roots']['result']:
                raise OSError('unknown result write')
            return original(writer,raw,**kw)
        monkeypatch.setattr(runtime.ObjectWriter,'put',object_put)
    def server():
        try:
            sts,sm,_=clients(c,'receipt',materials['receipt'])
            out.append(runtime.run(c,'receipt',sts=sts,secrets_client=sm,dynamodb=db,s3=s3))
        except BaseException as exc:errors.append(exc);ready.set()
    thread=threading.Thread(target=server);thread.start();assert ready.wait(5)
    sts,sm,_=clients(c,'source',materials['source'])
    try:
        if fault:
            expected={'clock':(clocks.ClockRefused,'Unsynchronized'),
                      'lost_reply':(TimeoutError,'unknown response'),
                      'tampered_store':(ValueError,'INTEGRITY_FAILURE'),
                      'footer_failure':(OSError,'unknown result write')}[fault]
            with pytest.raises(expected[0],match=expected[1]):runtime.run(c,'source',sts=sts,secrets_client=sm)
        else:
            result=runtime.run(c,'source',sts=sts,secrets_client=sm)
            assert result['status']=='ROUNDTRIP_VERIFIED' and not result['runtime_accepted']
            report=json.loads((Path(c['source']['roots']['result'])/(result['report_hash']+'.blob')).read_text())
            assert report['detail']['verified_subjects']==3 and not report['prospective_evidence']
            assert all(v['inspection']['structural_status']=='VALID' for v in report['inventories'].values())
            fixture=runtime.subjects(_hash(c))[0]
            assert report['run_id']==_hash(fixture)
    finally:thread.join(timeout=10)
    assert not thread.is_alive()
    if fault in ('clock','lost_reply'):assert errors
    else:assert not errors and out[0]['status']=='REQUESTS_SERVED'
    if fault=='lost_reply':assert index.puts==1 and db.item['state']=={'S':'COMMITTED'}
    if fault=='clock':assert not db.calls
    if fault in (None,'tampered_store','footer_failure'):assert index.puts==3 and len(db.calls)==6


def test_cli_failure_does_not_print_secret(config,monkeypatch,capsys):
    monkeypatch.setattr(runtime.platform,'system',lambda:'Linux')
    def secret_error(*a,**kw):raise ValueError('secret-that-must-not-appear')
    monkeypatch.setattr(runtime,'read_config',secret_error)
    assert runtime.main(['--config','/tmp/config','--sha256','a'*64,'--role','source'])==1
    output=capsys.readouterr()
    assert 'secret-that-must-not-appear' not in output.out+output.err
    assert json.loads(output.out)['status']=='INCOMPLETE'


def test_native_cleanup_cannot_mask_claim_failure(config,monkeypatch):
    c,_,_,_=config
    class Native:
        def __init__(self,**kw):pass
        def close(self):raise OSError('secondary close')
    class Writer:
        def claim(self):raise ValueError('primary claim')
    monkeypatch.setattr(runtime,'ClockBoundClock',Native)
    with pytest.raises(ValueError,match='primary claim'):runtime.RecordedClock(c,Writer())


def test_snapshot_global_budget_rejects_shared_expansion():
    from app.research.phase6_qualification_runtime import snapshot
    branch={'leaf':'x'}
    for _ in range(7):
        branch={str(i):branch for i in range(64)}
    with pytest.raises(ValueError,match='QUALIFICATION_REFUSED'):
        snapshot(branch)
    with pytest.raises(ValueError,match='QUALIFICATION_REFUSED'):
        snapshot({str(i):'x'*16384 for i in range(5)})
