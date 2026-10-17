import copy
import json
import pytest
pytest.importorskip('cryptography', reason='Install the phase6-anchor extra')
from test_phase6_receipt_http import event, ARN
from test_phase6_receipt_state import DB, req
from test_phase6_receipt_index import Table
from test_phase6_s3_retention import S3
from test_phase6_receipt_append import KEY
from app.research.phase6_anchor_validation import _hash
from app.research.phase6_receipt_service import build_receipt_service


def service(clock=lambda:1000):
    db=DB(); index=Table(); s3=S3(); db.put_item=index.put_item
    db.get_item=lambda **kw: index.get_item(**kw) if 'entry_id' in kw['Key'] else {'Item':copy.deepcopy(db.item)}
    trust=dict(schema_version='p6.anchor_trust.v1',service_identity='test',endpoint_id='synthetic',algorithm='Ed25519',
        public_key_hex=KEY.public_key().public_bytes_raw().hex(),not_before_utc_ms=0,not_after_utc_ms=10000,max_head_age_ms=100,max_clock_skew_ms=0)
    bound=build_receipt_service(dynamodb=db,s3=s3,table_arn='arn:aws:dynamodb:eu-north-1:123456789012:table/test-state',
        index_table_arn='arn:aws:dynamodb:eu-north-1:123456789012:table/test-index',bucket='test-bucket',owner='123456789012',
        trust=trust,expected_trust_hash=_hash(trust),signing_key=KEY,clock=clock,
        domain_name='test.lambda-url.eu-north-1.on.aws',principal_arns=[ARN])
    return bound,db,index,s3


def test_http_append_then_lookup_and_head_use_actual_components():
    h,db,index,s3=service()
    written=h.handle(event()); assert written['statusCode']==200
    original=json.loads(written['body'])['receipt']
    lookup=h.handle(event('/lookup',{'request':req(),'nonce':'a'*64}))
    head=h.handle(event('/head',{'run_id':'a'*64,'nonce':'b'*64}))
    assert lookup['statusCode']==head['statusCode']==200
    assert json.loads(lookup['body'])['receipt']==original
    assert json.loads(head['body'])['head']['nonce']=='b'*64
    assert len(db.calls)==2 and index.puts==1 and sum(x[0]=='put' for x in s3.calls)==1


def test_repeat_append_cannot_overwrite_and_original_can_be_read():
    h,db,index,s3=service(); first=h.handle(event())
    assert first['statusCode']==200 and h.handle(event())['statusCode']==409
    out=h.handle(event('/lookup',{'request':req(),'nonce':'a'*64}))
    assert out['statusCode']==200 and json.loads(out['body'])['receipt']==json.loads(first['body'])['receipt']
    assert index.puts==1 and sum(x[0]=='put' for x in s3.calls)==1


def test_unauthorized_cannot_reach_storage():
    h,db,index,s3=service(); e=event(); e['requestContext']['authorizer']=None
    assert h.handle(e)['statusCode']==403
    assert not db.calls and index.puts==0 and not s3.calls


def test_unknown_index_write_blocks_new_request_instances():
    h,db,index,s3=service(); index.timeout=True
    assert h.handle(event())['statusCode']==409
    assert h.handle(event())['statusCode']==409
    assert db.item['state']=={'S':'PENDING'} and index.puts==1
    assert h.handle(event('/lookup',{'request':req(),'nonce':'a'*64}))['statusCode']==409


def test_duplicate_subject_refused_before_new_writes():
    h,db,index,s3=service()
    first=json.loads(h.handle(event())['body'])['receipt']
    q=req(2, _hash(first)); q['subject_hash']=q['run_id']
    assert h.handle(event('/append',q))['statusCode']==409
    assert len(db.calls)==2 and index.puts==1


def test_backwards_clock_between_requests_refused_before_new_writes():
    now=[1000]
    h,db,index,s3=service(clock=lambda:now[0])
    first=json.loads(h.handle(event())['body'])['receipt']
    now[0]=999
    assert h.handle(event('/append',req(2,_hash(first))))['statusCode']==409
    assert len(db.calls)==2 and index.puts==1


def test_valid_successor_is_committed():
    h,db,index,s3=service()
    first=json.loads(h.handle(event())['body'])['receipt']
    assert h.handle(event('/append',req(2,_hash(first))))['statusCode']==200
    assert len(db.calls)==4 and index.puts==2
