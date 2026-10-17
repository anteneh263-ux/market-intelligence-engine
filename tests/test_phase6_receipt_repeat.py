import pytest
pytest.importorskip('cryptography', reason='Install the phase6-anchor extra')
from test_phase6_receipt_head import fixture
from test_phase6_receipt_state import req
from app.research.phase6_receipt_repeat import original_receipt, RepeatRefused


def inputs():
    args,appended,_=fixture(); db=args['dynamodb']; original=db.get_item
    db.get_item=lambda **kw: db.index_table.get_item(**kw) if 'entry_id' in kw['Key'] else original(**kw)
    del args['references']; del args['run_id']
    args.update(request=req(),index_table_arn='arn:aws:dynamodb:eu-north-1:123456789012:table/test-index')
    return args,appended


def test_repeat_returns_exact_original_without_writes():
    args,appended=inputs(); db=args['dynamodb']; s3=args['s3']
    writes=len(db.calls),db.index_table.puts,sum(x[0]=='put' for x in s3.calls)
    first=original_receipt(**args); second=original_receipt(**args)
    assert first['receipt']==second['receipt']==appended['receipt']
    assert not first['runtime_accepted'] and not first['recovery_authorized']
    assert writes==(len(db.calls),db.index_table.puts,sum(x[0]=='put' for x in s3.calls))


@pytest.mark.parametrize('fault',['missing','pending','conflict','storage','trust'])
def test_repeat_refuses_without_any_new_write(fault):
    args,_=inputs(); db=args['dynamodb']; s3=args['s3']
    if fault=='missing': db.index_table.rows.clear()
    if fault=='pending': db.item['state']={'S':'PENDING'}
    if fault=='conflict': args['request']['service_identity']='other'
    if fault=='storage': s3.fault='body'
    if fault=='trust': args['expected_trust_hash']='f'*64
    with pytest.raises(RepeatRefused): original_receipt(**args)
    assert len(db.calls)==2 and db.index_table.puts==1
    assert sum(x[0]=='put' for x in s3.calls)==1


def test_slow_lookup_refuses_final_ack():
    args,_=inputs(); times=iter([1001,1001,1001,1200]); args['clock']=lambda:next(times)
    with pytest.raises(RepeatRefused): original_receipt(**args)
