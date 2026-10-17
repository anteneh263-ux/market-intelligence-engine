import copy
import itertools
import json
import ssl
import pytest
from test_phase6_receipt_service import service
from test_phase6_receipt_http import event
from app.research import phase6_receipt_client as module
from app.research.phase6_object_store import ObjectWriter
from app.research.phase6_anchor_validation import _hash


def client(tmp_path,monkeypatch,fault=None):
    collected={}
    def build(**kw):
        from app.research.phase6_receipt_service import build_receipt_service
        collected['trust']=copy.deepcopy(kw['trust']);return build_receipt_service(**kw)
    h,db,index,s3=service(builder=build);calls=[]
    # This integration fixture retains multiple keys; the lower-level S3 fake
    # deliberately models only one write and cannot verify a receipt prefix.
    objects={};put=s3.put_object;get=s3.get_object;retention=s3.get_object_retention
    def put_many(**kw):
        result=put(**kw);objects[kw['Key']]=copy.deepcopy(kw);return result
    def get_many(**kw):s3.saved=objects[kw['Key']];return get(**kw)
    def retained_many(**kw):s3.saved=objects[kw['Key']];return retention(**kw)
    s3.put_object=put_many;s3.get_object=get_many;s3.get_object_retention=retained_many
    def request(**kw):
        calls.append(kw['path'])
        r=h.handle(event(kw['path'],json.loads(kw['body'])))
        response=dict(status_code=r['statusCode'],body=r['body'],receipt_verified=False,retry_allowed=False)
        if fault=='unknown' and kw['path']=='/append':raise TimeoutError('response lost after commit')
        if fault=='signature' and kw['path']=='/append':
            body=json.loads(response['body']);body['receipt']['signature']='0'*128;response['body']=json.dumps(body)
        if fault=='nonce' and kw['path']=='/head':
            body=json.loads(response['body']);body['head']['nonce']='0'*64;response['body']=json.dumps(body)
        return response
    monkeypatch.setattr(module,'request_once',request)
    root=tmp_path/'client';root.mkdir();w=ObjectWriter(str(root),owner='test',run_id='a'*64,max_bytes=1000000)
    ticks=itertools.count(1)
    c=module.VerifiedReceiptClient(w,run_id='a'*64,owner='test',trust=collected['trust'],expected_trust_hash=_hash(collected['trust']),
        clock=lambda:dict(utc_ms=1000,monotonic_ns=next(ticks),clock_session_id='test'),port=1,context=ssl.create_default_context(),hostname='synthetic',server_cert_sha256='a'*64)
    return c,calls,db,index,root


def test_verified_chain_advances_only_after_signed_challenged_head(tmp_path,monkeypatch):
    c,calls,db,index,root=client(tmp_path,monkeypatch)
    first=c.append(subject_kind='RESERVATION',subject_hash='a'*64)
    second=c.append(subject_kind='GENESIS',subject_hash='b'*64)
    head=c.head(challenge=dict(nonce='c'*64,issued_utc_ms=1000))
    assert len(first['receipts'])==1 and len(second['receipts'])==2 and head['head']['nonce']=='c'*64
    assert calls==['/append','/head','/append','/head','/head']
    saved=json.loads((root/(c._head+'.blob')).read_text())
    assert saved['kind']=='verification' and saved['data']['result']['cryptographic_status']=='VALID'
    c.close()


@pytest.mark.parametrize('fault',['unknown','signature','nonce'])
def test_unknown_or_invalid_response_never_advances_or_retries(tmp_path,monkeypatch,fault):
    c,calls,db,index,root=client(tmp_path,monkeypatch,fault)
    with pytest.raises((TimeoutError,ValueError)):c.append(subject_kind='RESERVATION',subject_hash='a'*64)
    assert c.stopped and c._receipts==[] and index.puts==1
    before=list(calls)
    with pytest.raises(ValueError):c.append(subject_kind='RESERVATION',subject_hash='a'*64)
    assert calls==before
    c.close()


def test_invalid_clock_refused_before_remote_mutation(tmp_path,monkeypatch):
    c,calls,db,index,root=client(tmp_path,monkeypatch)
    c._clock=lambda:dict(utc_ms=True,monotonic_ns=1,clock_session_id='test')
    with pytest.raises(ValueError):c.append(subject_kind='RESERVATION',subject_hash='a'*64)
    assert not calls and not db.calls and index.puts==0
    c.close()


@pytest.mark.parametrize('fault',['port','context','hostname','pin','trust'])
def test_invalid_configuration_does_not_claim_writer(tmp_path,monkeypatch,fault):
    c,calls,db,index,root=client(tmp_path,monkeypatch);trust=copy.deepcopy(c._trust);c.close()
    target=tmp_path/'unused';target.mkdir();writer=ObjectWriter(str(target),owner='test',run_id='a'*64,max_bytes=1000000)
    config=dict(port=1,context=ssl.create_default_context(),hostname='synthetic',server_cert_sha256='a'*64)
    if fault=='trust':trust['max_head_age_ms']=0
    else:config[{'port':'port','context':'context','hostname':'hostname','pin':'server_cert_sha256'}[fault]]={'port':True,'context':None,'hostname':'bad\r\nheader','pin':'bad'}[fault]
    with pytest.raises(Exception):module.VerifiedReceiptClient(writer,run_id='a'*64,owner='test',trust=trust,expected_trust_hash=_hash(trust),clock=lambda:None,**config)
    assert writer._claim is None and list(target.iterdir())==[target/'writer.json']
    writer.close()


@pytest.mark.parametrize('point',['before_request','after_remote_write','reentry'])
def test_publication_and_reentry_failures_are_terminal(tmp_path,monkeypatch,point):
    c,calls,db,index,root=client(tmp_path,monkeypatch)
    original=c._writer.put;count=0
    if point!='reentry':
        def fail(raw,**kwargs):
            nonlocal count
            count+=1
            if count==(2 if point=='before_request' else 3):raise OSError('uncertain local publication')
            return original(raw,**kwargs)
        monkeypatch.setattr(c._writer,'put',fail)
    else:
        request=module.request_once
        def recurse(**kwargs):
            try:c.append(subject_kind='RESERVATION',subject_hash='a'*64)
            except ValueError:pass
            return request(**kwargs)
        monkeypatch.setattr(module,'request_once',recurse)
    with pytest.raises((ValueError,OSError)):c.append(subject_kind='RESERVATION',subject_hash='a'*64)
    assert c.stopped and c._receipts==[]
    assert index.puts==(0 if point=='before_request' else 1)
    c.close()
