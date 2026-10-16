import copy
import io
from types import SimpleNamespace
from datetime import timedelta
import pytest
pytest.importorskip('cryptography', reason='Install the phase6-anchor extra')
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app.research.phase6_anchor_validation import _canonical
from app.research.phase6_s3_retention import S3ReceiptRetention, RetentionUncertain

KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
UNTIL = 1000 + 30 * 86400000


def receipt():
    q = dict(schema_version='p6.anchor_receipt.v1', run_id='a'*64, anchor_seq=1,
             subject_kind='RESERVATION', subject_hash='a'*64, previous_anchor_hash='0'*64,
             retained_utc_ms=1000, service_identity='test')
    q['signature'] = KEY.sign(_canonical(q)).hex()
    return q


class S3:
    def __init__(self, fault=None):
        self.meta = SimpleNamespace(config=SimpleNamespace(retries={'total_max_attempts': 1}))
        self.fault, self.calls = fault, []

    def put_object(self, **kw):
        self.calls.append(('put', kw))
        self.saved = kw
        if self.fault == 'put':
            raise TimeoutError('write may have succeeded')
        return {'VersionId': None if self.fault == 'missing' else 'v1'}

    def get_object(self, **kw):
        self.calls.append(('get', kw))
        if self.fault == 'get':
            raise TimeoutError('unreadable')
        self.stream = io.BytesIO(self.saved['Body'] + (b'x' if self.fault == 'body' else b''))
        return {'VersionId': 'v2' if self.fault == 'version' else 'v1', 'Body': self.stream}

    def get_object_retention(self, **kw):
        self.calls.append(('retention', kw))
        if self.fault == 'retention':
            raise TimeoutError('unreadable')
        end = self.saved['ObjectLockRetainUntilDate']
        if self.fault == 'short':
            end -= timedelta(milliseconds=1)
        if self.fault == 'naive':
            end = end.replace(tzinfo=None)
        return {'Retention': {'Mode': 'GOVERNANCE' if self.fault == 'mode' else 'COMPLIANCE',
                              'RetainUntilDate': end}}


def adapter(s3):
    return S3ReceiptRetention(s3, bucket='test-receipts', owner='123456789012',
        service_identity='test', public_key_hex=KEY.public_key().public_bytes_raw().hex())


def test_verified_exact_version_only_and_no_runtime_authority():
    s3 = S3()
    q = receipt()
    before = copy.deepcopy(q)
    result = adapter(s3).retain(q, retain_until_utc_ms=UNTIL)
    assert q == before
    assert result['version_id'] == 'v1' and result['runtime_accepted'] is False
    assert [c[0] for c in s3.calls] == ['put', 'get', 'retention']
    assert s3.saved['IfNoneMatch'] == '*'
    assert s3.saved['ChecksumAlgorithm'] == 'SHA256'
    assert all(c[1]['ExpectedBucketOwner'] == '123456789012' for c in s3.calls)
    assert all(c[1]['VersionId'] == 'v1' for c in s3.calls[1:])
    assert s3.stream.closed


@pytest.mark.parametrize('fault', ['put', 'missing', 'get', 'body', 'version', 'retention', 'short', 'naive', 'mode'])
def test_uncertain_write_never_acknowledged_or_retried(fault):
    s3 = S3(fault)
    a = adapter(s3)
    with pytest.raises(RetentionUncertain):
        a.retain(receipt(), retain_until_utc_ms=UNTIL)
    calls = len(s3.calls)
    with pytest.raises(RetentionUncertain):
        a.retain(receipt(), retain_until_utc_ms=UNTIL)
    assert len(s3.calls) == calls
    assert sum(c[0] == 'put' for c in s3.calls) == 1


def test_second_write_after_success_also_forbidden():
    s3 = S3()
    a = adapter(s3)
    a.retain(receipt(), retain_until_utc_ms=UNTIL)
    with pytest.raises(RetentionUncertain):
        a.retain(receipt(), retain_until_utc_ms=UNTIL)
    assert len(s3.calls) == 3


@pytest.mark.parametrize('field,value', [('signature', '0'*128), ('service_identity', 'other'),
    ('anchor_seq', True), ('subject_hash', 'b'*64), ('retained_utc_ms', -1), ('extra', 1)])
def test_bad_receipt_does_not_touch_storage(field, value):
    s3 = S3()
    q = receipt()
    q[field] = value
    with pytest.raises(Exception):
        adapter(s3).retain(q, retain_until_utc_ms=UNTIL)
    assert not s3.calls


@pytest.mark.parametrize('until', [True, UNTIL-1, 2**63, 0])
def test_invalid_retention_no_write(until):
    s3 = S3()
    with pytest.raises(ValueError):
        adapter(s3).retain(receipt(), retain_until_utc_ms=until)
    assert not s3.calls


def test_sdk_retries_must_be_disabled():
    s3 = S3()
    s3.meta.config.retries = {'total_max_attempts': 2}
    with pytest.raises(ValueError):
        adapter(s3)
    assert not s3.calls


def test_inherited_adapter_refuses_before_storage(monkeypatch):
    import app.research.phase6_s3_retention as module
    s3 = S3()
    a = adapter(s3)
    monkeypatch.setattr(module.os, 'getpid', lambda: a._pid + 1)
    with pytest.raises(RetentionUncertain):
        a.retain(receipt(), retain_until_utc_ms=UNTIL)
    assert not s3.calls
