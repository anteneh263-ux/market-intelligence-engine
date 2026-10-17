import copy
from types import SimpleNamespace
import pytest
from app.research.phase6_receipt_index import ReceiptVersionIndex, IndexRefused
from test_phase6_receipt_state import req, witness


class Table:
    def __init__(self):
        self.meta=SimpleNamespace(config=SimpleNamespace(retries={'total_max_attempts':1}))
        self.rows={}; self.puts=0; self.reads=[]; self.timeout=False
    def put_item(self,**kw):
        self.puts+=1
        assert kw['ConditionExpression']=='attribute_not_exists(#entry)'
        assert kw['ExpressionAttributeNames']=={'#entry':'entry_id'}
        key=kw['Item']['entry_id']['S']
        if key in self.rows: raise RuntimeError('conditional conflict')
        self.rows[key]=copy.deepcopy(kw['Item'])
        if self.timeout: raise TimeoutError('lost response')
        return {}
    def get_item(self,**kw):
        self.reads.append(kw)
        item=self.rows.get(kw['Key']['entry_id']['S'])
        return {'Item':copy.deepcopy(item)} if item else {}


def index(table):
    return ReceiptVersionIndex(table,table_arn='arn:aws:dynamodb:eu-north-1:123456789012:table/test-index',bucket='test-bucket',service_identity='test')


def test_publish_then_exact_lookup_never_claims_committed():
    t=Table(); i=index(t); q=req(); v=witness(); i.publish(q,v)
    out=i.lookup(q)
    assert out['status']=='FOUND_UNCONFIRMED' and not out['runtime_accepted']
    assert out['record']['reference']==v and t.puts==1
    assert t.reads[0]['ConsistentRead'] is True
    out['record']['reference']['version_id']='tampered'
    assert i.lookup(q)['record']['reference']['version_id']=='version1'


def test_missing_read_does_not_write():
    t=Table(); assert index(t).lookup(req())['status']=='NOT_FOUND'; assert t.puts==0


def test_duplicate_publication_cannot_replace_original():
    t=Table(); index(t).publish(req(),witness()); before=copy.deepcopy(t.rows)
    v=witness(); v['version_id']='replacement'
    with pytest.raises(IndexRefused): index(t).publish(req(),v)
    assert t.rows==before


def test_unknown_publication_only_read_only_lookup_allowed():
    t=Table(); t.timeout=True; i=index(t)
    with pytest.raises(IndexRefused): i.publish(req(),witness())
    with pytest.raises(IndexRefused): i.publish(req(),witness())
    assert t.puts==1 and i.lookup(req())['status']=='FOUND_UNCONFIRMED'


def test_same_sequence_different_request_refused():
    t=Table(); i=index(t); q=req(2,'c'*64); i.publish(q,witness(2))
    q['subject_hash']='f'*64
    with pytest.raises(IndexRefused): i.lookup(q)


@pytest.mark.parametrize('field,value',[('payload',{'S':'{}'}),('payload_hash',{'S':'f'*64}),('entry_id',{'S':'wrong'})])
def test_corrupt_stored_record_refused(field,value):
    t=Table(); i=index(t); i.publish(req(),witness()); next(iter(t.rows.values()))[field]=value
    with pytest.raises(IndexRefused): i.lookup(req())


@pytest.mark.parametrize('field,value',[('key','wrong'),('bucket','wrong-bucket'),('version_id','null'),('retention_until_ms',True)])
def test_invalid_reference_no_write(field,value):
    t=Table(); v=witness(); v[field]=value
    with pytest.raises(IndexRefused): index(t).publish(req(),v)
    assert t.puts==0


def test_forked_writer_refuses(monkeypatch):
    import app.research.phase6_receipt_index as module
    t=Table(); i=index(t); monkeypatch.setattr(module.os,'getpid',lambda:i.pid+1)
    with pytest.raises(IndexRefused): i.publish(req(),witness())
    assert t.puts==0
