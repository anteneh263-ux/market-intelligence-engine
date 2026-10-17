import hashlib
import io
from datetime import datetime, timezone, timedelta
import pytest
from app.research.phase6_receipt_lookup import inspect_retained_version, LookupRefused

BODY=b'{"synthetic":"opaque bytes, not a signed receipt"}'
UNTIL=2000


def reference():
    return dict(schema_version='p6.s3_retained_version.v1',bucket='test-bucket',
        key='receipts/'+'a'*64+'/'+'1'.zfill(20)+'.json',version_id='v1',
        receipt_hash=hashlib.sha256(BODY).hexdigest(),retention_until_ms=UNTIL,runtime_accepted=False)


class Reads:
    def __init__(self, fault=None): self.calls=[]; self.fault=fault
    def get_object(self, **kw):
        self.calls.append(('get',kw))
        if self.fault=='timeout': raise TimeoutError()
        body=BODY if self.fault!='body' else BODY+b'x'
        if self.fault=='oversize': body=b'x'*65537
        self.stream=io.BytesIO(body)
        return dict(VersionId='v2' if self.fault=='version' else 'v1',Body=self.stream)
    def get_object_retention(self, **kw):
        self.calls.append(('retention',kw))
        end=datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(milliseconds=UNTIL)
        if self.fault=='short': end-=timedelta(milliseconds=1)
        if self.fault=='naive': end=end.replace(tzinfo=None)
        return {'Retention':dict(Mode='GOVERNANCE' if self.fault=='mode' else 'COMPLIANCE',RetainUntilDate=end)}


def inspect(client,q=None,now=1000):
    return inspect_retained_version(client,q or reference(),owner='123456789012',now_utc_ms=now)


def test_exact_version_read_only_without_signature_or_head_claim():
    c=Reads(); q=reference(); out=inspect(c,q)
    assert out['body']==BODY and out['reference']==q
    assert not out['runtime_accepted'] and not out['signature_verified'] and not out['current_head_verified']
    assert [x[0] for x in c.calls]==['get','retention']
    assert all(x[1]['VersionId']=='v1' and x[1]['ExpectedBucketOwner']=='123456789012' for x in c.calls)
    assert c.stream.closed
    q['version_id']='changed'
    assert out['reference']['version_id']=='v1'


@pytest.mark.parametrize('fault',['timeout','body','version','oversize','short','naive','mode'])
def test_failed_inspection_never_writes(fault):
    c=Reads(fault)
    with pytest.raises(LookupRefused): inspect(c)
    assert len(c.calls)<=2


@pytest.mark.parametrize('field,value',[('key','../bad'),('version_id','null'),('bucket','bad/bucket'),
    ('receipt_hash','A'*64),('retention_until_ms',True),('runtime_accepted',True),('extra',1)])
def test_bad_reference_no_io(field,value):
    c=Reads(); q=reference(); q[field]=value
    with pytest.raises(LookupRefused): inspect(c,q)
    assert not c.calls


@pytest.mark.parametrize('now',[2000,2001,True,-1])
def test_expired_or_invalid_time_no_io(now):
    c=Reads()
    with pytest.raises(LookupRefused): inspect(c,now=now)
    assert not c.calls


def test_lookup_of_actual_append_adapter_output():
    pytest.importorskip('cryptography', reason='Install the phase6-anchor extra')
    from test_phase6_receipt_append import setup, req
    from app.research.phase6_anchor_validation import _canonical
    a,db,s3=setup()
    appended=a.append(req())
    count=len(db.calls)
    result=inspect_retained_version(s3,appended['retained_version'],owner='123456789012',now_utc_ms=1001)
    assert result['body']==_canonical(appended['receipt'])
    assert len(db.calls)==count
    assert [x[0] for x in s3.calls]==['put','get','retention','get','retention']
