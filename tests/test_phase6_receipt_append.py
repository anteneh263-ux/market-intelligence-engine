import copy
import pytest
pytest.importorskip('cryptography', reason='Install the phase6-anchor extra')
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app.research.phase6_anchor_validation import _canonical, _hash
from app.research.phase6_receipt_append import ReceiptAppendAttempt, AppendUncertain
from test_phase6_receipt_state import DB, req
from test_phase6_s3_retention import S3

KEY=Ed25519PrivateKey.from_private_bytes(bytes(range(32)))


def setup(db=None,s3=None,clock=lambda:1000):
    db=db or DB(); s3=s3 or S3()
    a=ReceiptAppendAttempt(dynamodb=db,s3=s3,
        table_arn='arn:aws:dynamodb:eu-north-1:123456789012:table/test-state',
        bucket='test-bucket',owner='123456789012',service_identity='test',
        signing_key=KEY,clock=clock,not_before_ms=0,not_after_ms=10000)
    return a,db,s3


def test_actual_adapters_receive_signed_bound_receipt_in_order():
    a,db,s3=setup(); q=req(); original=copy.deepcopy(q)
    out=a.append(q); receipt=out['receipt']
    unsigned={k:v for k,v in receipt.items() if k!='signature'}
    KEY.public_key().verify(bytes.fromhex(receipt['signature']),_canonical(unsigned))
    assert q==original and out['runtime_accepted'] is False
    assert s3.saved['Body']==_canonical(receipt)
    assert db.item['head_hash']=={'S':_hash(receipt)}
    assert db.item['state']=={'S':'COMMITTED'}
    assert out['retained_version']['receipt_hash']==out['committed']['receipt_hash']==_hash(receipt)
    assert len(db.calls)==2 and len(s3.calls)==3


def test_storage_occurs_only_while_db_pending():
    db=DB()
    class Observe(S3):
        def put_object(self,**kw):
            assert db.item['state']=={'S':'PENDING'}
            return super().put_object(**kw)
    setup(db,Observe())[0].append(req())


@pytest.mark.parametrize('fault',['put','get','body','retention','short','mode'])
def test_storage_failure_leaves_pending_and_no_commit(fault):
    a,db,s3=setup(s3=S3(fault))
    with pytest.raises(AppendUncertain): a.append(req())
    assert len(db.calls)==1 and db.item['state']=={'S':'PENDING'}
    calls=len(s3.calls)
    with pytest.raises(AppendUncertain): a.append(req())
    assert len(s3.calls)==calls


def test_claim_failure_never_touches_s3():
    db=DB(); db.fail='claim'; a,db,s3=setup(db)
    with pytest.raises(AppendUncertain): a.append(req())
    assert not s3.calls


def test_commit_timeout_never_returns_receipt_or_retries():
    db=DB(); db.fail='commit'; a,db,s3=setup(db)
    with pytest.raises(AppendUncertain): a.append(req())
    assert db.item['state']=={'S':'COMMITTED'}
    with pytest.raises(AppendUncertain): a.append(req())
    assert len(db.calls)==2 and len(s3.calls)==3


@pytest.mark.parametrize('now',[True,-1,10001,1.2])
def test_invalid_clock_no_external_calls(now):
    a,db,s3=setup(clock=lambda:now)
    with pytest.raises(AppendUncertain): a.append(req())
    assert not db.calls and not s3.calls


def test_second_request_uses_previous_signed_receipt_hash():
    a,db,_=setup(); first=a.append(req())
    second=setup(db=db,clock=lambda:1001)[0].append(req(2,_hash(first['receipt'])))
    assert second['committed']['anchor_seq']==2
    assert second['committed']['prefix_until_ms']==first['committed']['prefix_until_ms']


def test_caller_cannot_inject_retention_witness():
    a,db,s3=setup(); q=req(); q['retained_version']={}
    with pytest.raises(ValueError): a.append(q)
    assert not db.calls and not s3.calls


def test_expired_previous_prefix_blocks_storage():
    a,db,_=setup(); first=a.append(req())
    db.item['prefix_until_ms']={'N':'1000'}
    b,_,s3=setup(db=db,clock=lambda:1001)
    with pytest.raises(AppendUncertain): b.append(req(2,_hash(first['receipt'])))
    assert not s3.calls and db.item['state']=={'S':'PENDING'}


@pytest.mark.parametrize('samples,commits', [([1000,999],1),([1000,10001],1),([1000,1000,999],2),([1000,1000,10001],2)])
def test_clock_failure_during_append_returns_no_ack(samples,commits):
    times=iter(samples)
    a,db,s3=setup(clock=lambda:next(times))
    with pytest.raises(AppendUncertain): a.append(req())
    assert len(db.calls)==commits
    assert db.item['state']=={'S':'PENDING' if commits==1 else 'COMMITTED'}


def test_signed_outputs_pass_existing_offline_chain_verifier():
    from app.research.phase6_anchor_validation import verify_anchors
    a,db,_=setup(); first=a.append(req())
    b,_,_=setup(db=db,clock=lambda:1001); second=b.append(req(2,_hash(first['receipt'])))
    trust=dict(schema_version='p6.anchor_trust.v1',service_identity='test',endpoint_id='synthetic',
        algorithm='Ed25519',public_key_hex=KEY.public_key().public_bytes_raw().hex(),
        not_before_utc_ms=0,not_after_utc_ms=10000,max_head_age_ms=100,max_clock_skew_ms=0)
    receipts=[first['receipt'],second['receipt']]
    result=verify_anchors(dict(trust=trust,expected_trust_hash=_hash(trust),expected_run_id='a'*64,
        expected_subjects=[dict(subject_kind=x['subject_kind'],subject_hash=x['subject_hash']) for x in receipts],
        receipts=receipts,head=None,challenge=None,now_utc_ms=1001))
    assert result['cryptographic_status']=='VALID' and result['head_status']=='OFFLINE'
    assert result['runtime_accepted'] is False
