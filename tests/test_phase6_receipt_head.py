import copy
import pytest
pytest.importorskip('cryptography', reason='Install the phase6-anchor extra')
from test_phase6_receipt_append import setup, req, KEY
from app.research.phase6_anchor_validation import _hash, verify_anchors
from app.research.phase6_receipt_head import challenged_head, HeadRefused


def fixture():
    a,db,s3=setup(); appended=a.append(req())
    calls=[]
    def get_item(**kw):
        calls.append(kw)
        return {'Item':copy.deepcopy(db.item)}
    db.get_item=get_item
    trust=dict(schema_version='p6.anchor_trust.v1',service_identity='test',endpoint_id='synthetic',
        algorithm='Ed25519',public_key_hex=KEY.public_key().public_bytes_raw().hex(),
        not_before_utc_ms=0,not_after_utc_ms=10000,max_head_age_ms=100,max_clock_skew_ms=0)
    args=dict(dynamodb=db,s3=s3,table_arn=a._state.table,bucket='test-bucket',owner='123456789012',
        run_id='a'*64,references=[appended['retained_version']],trust=trust,expected_trust_hash=_hash(trust),
        signing_key=KEY,nonce='e'*64,clock=lambda:1001)
    return args,appended,calls


def test_real_local_append_lookup_head_verifier_path():
    args,a,calls=fixture(); out=challenged_head(**args)
    q=dict(trust=args['trust'],expected_trust_hash=args['expected_trust_hash'],expected_run_id=args['run_id'],
        expected_subjects=[dict(subject_kind='RESERVATION',subject_hash=args['run_id'])],
        receipts=[a['receipt']],head=out['head'],challenge=dict(nonce=args['nonce'],issued_utc_ms=1001),now_utc_ms=1001)
    result=verify_anchors(q)
    assert result['cryptographic_status']=='VALID' and result['head_status']=='MATCHED'
    assert out['runtime_accepted'] is False
    assert len(calls)==2 and all(x['ConsistentRead'] is True for x in calls)
    assert len(args['dynamodb'].calls)==2 # Only the earlier append wrote.


@pytest.mark.parametrize('field,value',[('state',{'S':'PENDING'}),('head_seq',{'N':'2'}),
    ('head_hash',{'S':'f'*64}),('version_id',{'S':'v2'}),('prefix_until_ms',{'N':'9999999999999'})])
def test_state_disagreement_no_head(field,value):
    args,_,_=fixture(); args['dynamodb'].item[field]=value
    with pytest.raises(HeadRefused): challenged_head(**args)


def test_concurrent_state_change_refused():
    args,_,calls=fixture(); original=args['dynamodb'].get_item
    def changing(**kw):
        result=original(**kw)
        if len(calls)==2: result['Item']['state']={'S':'PENDING'}
        return result
    args['dynamodb'].get_item=changing
    with pytest.raises(HeadRefused): challenged_head(**args)


@pytest.mark.parametrize('fault',['body','short','mode','version'])
def test_storage_corruption_no_head(fault):
    args,_,_=fixture(); args['s3'].fault=fault
    with pytest.raises(HeadRefused): challenged_head(**args)


@pytest.mark.parametrize('times',[[1001,1000],[1001,1200],[1001,True]])
def test_clock_invalid_no_head(times):
    args,_,_=fixture(); it=iter(times); args['clock']=lambda:next(it)
    with pytest.raises(HeadRefused): challenged_head(**args)


def test_trust_mismatch_before_database_read():
    args,_,calls=fixture(); args['expected_trust_hash']='f'*64
    with pytest.raises(HeadRefused): challenged_head(**args)
    assert not calls


@pytest.mark.parametrize('case',['valid','backwards_receipt_time','duplicate_subject'])
def test_complete_two_receipt_prefix(case):
    from test_phase6_s3_retention import S3
    class History(S3):
        def __init__(self): super().__init__(); self.objects={}
        def put_object(self,**kw):
            out=super().put_object(**kw); self.objects[kw['Key']]=copy.deepcopy(kw); return out
        def get_object(self,**kw):
            self.saved=self.objects[kw['Key']]; return super().get_object(**kw)
        def get_object_retention(self,**kw):
            self.saved=self.objects[kw['Key']]; return super().get_object_retention(**kw)
    args,_,_=fixture()
    s3=History(); a,db,_=setup(s3=s3); first=a.append(req())
    second_request=req(2,_hash(first['receipt']))
    if case=='duplicate_subject': second_request['subject_hash']='a'*64
    b,_,_=setup(db=db,s3=s3,clock=lambda:999 if case=='backwards_receipt_time' else 1001)
    second=b.append(second_request)
    db.get_item=lambda **kw: {'Item':copy.deepcopy(db.item)}
    args.update(dynamodb=db,s3=s3,references=[first['retained_version'],second['retained_version']],clock=lambda:1002)
    if case=='valid':
        assert challenged_head(**args)['head']['anchor_seq']==2
    else:
        with pytest.raises(HeadRefused): challenged_head(**args)


@pytest.mark.parametrize('fault',[None,'missing','corrupt','pending'])
def test_indexed_head_resolves_persisted_versions(fault):
    from app.research.phase6_receipt_head import indexed_challenged_head
    args,_,_=fixture(); db=args['dynamodb']; original=db.get_item
    def route(**kw):
        return db.index_table.get_item(**kw) if 'entry_id' in kw['Key'] else original(**kw)
    db.get_item=route
    if fault=='missing': db.index_table.rows.clear()
    if fault=='corrupt': next(iter(db.index_table.rows.values()))['payload_hash']={'S':'f'*64}
    if fault=='pending': db.item['state']={'S':'PENDING'}
    del args['references']
    args['index_table_arn']='arn:aws:dynamodb:eu-north-1:123456789012:table/test-index'
    if fault:
        with pytest.raises(HeadRefused): indexed_challenged_head(**args)
    else:
        out=indexed_challenged_head(**args)
        assert out['head']['anchor_seq']==1 and out['runtime_accepted'] is False
        assert db.index_table.reads[0]['ConsistentRead'] is True
