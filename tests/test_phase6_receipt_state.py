import copy
from types import SimpleNamespace
import pytest
from app.research.phase6_receipt_state import ReceiptStateAttempt, StateUncertain


def req(seq=1, previous='0'*64):
    return dict(schema_version='p6.anchor_append_request.v1',run_id='a'*64,
        anchor_seq=seq,subject_kind='RESERVATION' if seq==1 else 'GENESIS' if seq==2 else 'CHECKPOINT',
        subject_hash='a'*64 if seq==1 else 'b'*64,previous_anchor_hash=previous,service_identity='test')


def witness(seq=1, digest='c'*64, until=9999):
    return dict(schema_version='p6.s3_retained_version.v1',bucket='test-bucket',
        key='receipts/'+'a'*64+'/'+str(seq).zfill(20)+'.json',version_id='version1',
        receipt_hash=digest,retention_until_ms=until,runtime_accepted=False)


class DB:
    """Small conditional-state fake, not an AWS integration emulator."""
    def __init__(self):
        self.meta=SimpleNamespace(config=SimpleNamespace(retries={'total_max_attempts':1}))
        self.item=None
        self.calls=[]
        self.fail=None

    def update_item(self, **kw):
        self.calls.append(copy.deepcopy(kw))
        v=kw['ExpressionAttributeValues']
        claim='ReturnValues' in kw
        old=self.item
        if claim:
            if ':zero' in v:
                assert kw['ConditionExpression']=='attribute_not_exists(#run)'
                if old is not None: raise RuntimeError('conditional conflict')
                old=dict(run_id=kw['Key']['run_id'], head_seq={'N':'0'},head_hash={'S':'0'*64},prefix_until_ms={'N':'0'})
            else:
                assert kw['ConditionExpression']=='#state=:committed AND #seq=:previous AND #hash=:previous_hash AND #service=:service'
                if old is None or any(old[k]!=v[x] for k,x in [('state',':committed'),('head_seq',':previous'),('head_hash',':previous_hash'),('service_identity',':service')]):
                    raise RuntimeError('conditional conflict')
            self.item=copy.deepcopy(old)
            for k,x in [('state',':pending'),('pending_request',':request'),('attempt_id',':attempt'),('expected_receipt_hash',':expected'),('service_identity',':service')]: self.item[k]=v[x]
        else:
            assert kw['ConditionExpression']=='#state=:pending AND pending_request=:request AND attempt_id=:attempt AND expected_receipt_hash=:hash'
            if old is None or any(old[k]!=v[x] for k,x in [('state',':pending'),('pending_request',':request'),('attempt_id',':attempt'),('expected_receipt_hash',':hash')]):
                raise RuntimeError('conditional conflict')
            for k,x in [('state',':committed'),('head_seq',':seq'),('head_hash',':hash'),('prefix_until_ms',':until'),('version_id',':version'),('object_key',':key')]: self.item[k]=v[x]
        if self.fail == ('claim' if claim else 'commit'): raise TimeoutError('response lost after write')
        return {'Attributes':copy.deepcopy(self.item)} if claim else {}


def attempt(db):
    return ReceiptStateAttempt(db,table_arn='arn:aws:dynamodb:eu-north-1:123456789012:table/test-state',bucket='test-bucket',service_identity='test')


def claim(a, seq=1, previous='0'*64, digest='c'*64):
    return a.claim(req(seq,previous),attempt_id='d'*64,receipt_hash=digest)


def test_two_commits_preserve_minimum_prefix_horizon():
    db=DB(); a=attempt(db)
    assert claim(a)['runtime_accepted'] is False
    assert a.commit(witness())['prefix_until_ms']==9999
    b=attempt(db); claim(b,2,'c'*64,'e'*64)
    result=b.commit(witness(2,'e'*64,12000))
    assert result['prefix_until_ms']==9999 and result['runtime_accepted'] is False
    assert db.item['head_seq']=={'N':'2'}


def test_pending_blocks_another_process_attempt():
    db=DB(); claim(attempt(db))
    with pytest.raises(StateUncertain): claim(attempt(db))
    with pytest.raises(StateUncertain): claim(attempt(db),2,'c'*64)
    assert db.item['state']=={'S':'PENDING'}


def test_lost_claim_response_leaves_run_blocked():
    db=DB(); db.fail='claim'; a=attempt(db)
    with pytest.raises(StateUncertain): claim(a)
    n=len(db.calls)
    with pytest.raises(StateUncertain): claim(a)
    with pytest.raises(StateUncertain): a.commit(witness())
    assert len(db.calls)==n and db.item['state']=={'S':'PENDING'}


def test_lost_commit_response_never_retried():
    db=DB(); a=attempt(db); claim(a); db.fail='commit'
    with pytest.raises(StateUncertain): a.commit(witness())
    n=len(db.calls)
    with pytest.raises(StateUncertain): a.commit(witness())
    assert len(db.calls)==n and db.item['state']=={'S':'COMMITTED'}


@pytest.mark.parametrize('field,value',[('bucket','wrong'),('receipt_hash','f'*64),('key','wrong'),('version_id','null'),('retention_until_ms',True),('runtime_accepted',True),('extra',1)])
def test_bad_retention_cannot_publish_head(field,value):
    db=DB(); a=attempt(db); claim(a); v=witness(); v[field]=value
    with pytest.raises(StateUncertain): a.commit(v)
    assert len(db.calls)==1 and db.item['state']=={'S':'PENDING'}


def test_wrong_predecessor_rejected_by_conditional_write():
    db=DB(); a=attempt(db); claim(a); a.commit(witness())
    with pytest.raises(StateUncertain): claim(attempt(db),2,'f'*64)
    assert db.item['head_seq']=={'N':'1'}


def test_commit_without_claim_and_forked_instance_no_io(monkeypatch):
    import app.research.phase6_receipt_state as module
    db=DB(); a=attempt(db)
    with pytest.raises(StateUncertain): a.commit(witness())
    monkeypatch.setattr(module.os,'getpid',lambda:a.pid+1)
    with pytest.raises(StateUncertain): claim(a)
    assert not db.calls


def test_retry_client_rejected():
    db=DB(); db.meta.config.retries['total_max_attempts']=2
    with pytest.raises(ValueError): attempt(db)


@pytest.mark.parametrize('field,value', [('head_seq', {'N':'9'}), ('head_hash', {'S':'f'*64}),
    ('attempt_id', {'S':'f'*64}), ('state', {'S':'COMMITTED'}),
    ('prefix_until_ms', {'N':'-1'}), ('prefix_until_ms', {'N':'01'})])
def test_malformed_claim_response_never_authorizes_commit(field,value):
    class BadResponse(DB):
        def update_item(self, **kw):
            result=super().update_item(**kw)
            result['Attributes'][field]=value
            return result
    db=BadResponse(); a=attempt(db)
    with pytest.raises(StateUncertain): claim(a)
    with pytest.raises(StateUncertain): a.commit(witness())
    assert len(db.calls)==1


def test_changed_pending_claim_cannot_commit():
    db=DB(); a=attempt(db); claim(a)
    db.item['attempt_id']={'S':'f'*64}
    with pytest.raises(StateUncertain): a.commit(witness())
    assert db.item['state']=={'S':'PENDING'}


def test_request_is_detached_before_persistence():
    db=DB(); a=attempt(db); q=req()
    a.claim(q,attempt_id='d'*64,receipt_hash='c'*64)
    q['subject_hash']='f'*64
    assert a.commit(witness())['anchor_seq']==1
