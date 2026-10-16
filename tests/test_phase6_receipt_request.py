import copy
import pytest
from app.research.phase6_receipt_request import validate_append_request


def request(seq=1):
    return dict(schema_version='p6.anchor_append_request.v1', run_id='a'*64,
                anchor_seq=seq, subject_kind='RESERVATION' if seq == 1 else 'GENESIS' if seq == 2 else 'CHECKPOINT',
                subject_hash=('a' if seq == 1 else 'b')*64, previous_anchor_hash='0'*64 if seq == 1 else 'c'*64,
                service_identity='mie:test')


@pytest.mark.parametrize('seq', [1, 2, 3, 10001, 2**63-1])
def test_valid_detached_request(seq):
    q = request(seq)
    before = copy.deepcopy(q)
    result = validate_append_request(q)
    assert result['status'] == 'VALID'
    assert result['runtime_accepted'] is False
    assert result['idempotency_key'] == {'run_id': q['run_id'], 'anchor_seq': seq}
    assert q == before
    q['subject_hash'] = 'd'*64
    assert result['request'] == before


@pytest.mark.parametrize('field,value', [
    ('anchor_seq', True), ('anchor_seq', 0), ('anchor_seq', -1),
    ('anchor_seq', 1.0), ('anchor_seq', 2**63),
    ('run_id', 'A'*64), ('subject_hash', 'b'*63),
    ('previous_anchor_hash', 'x'*64), ('service_identity', 'bad/name'),
    ('service_identity', ''), ('schema_version', 'p6.anchor_receipt.v1'),
    ('subject_kind', []), ('subject_kind', 'GENESIS'),
    ('previous_anchor_hash', 'c'*64),
])
def test_rejects_invalid_fields(field, value):
    q = request()
    q[field] = value
    result = validate_append_request(q)
    assert result['status'] == 'INVALID'
    assert result['request'] is result['request_hash'] is result['idempotency_key'] is None
    assert result['runtime_accepted'] is False


@pytest.mark.parametrize('field', list(request()))
def test_missing_fields(field):
    q = request()
    del q[field]
    assert validate_append_request(q)['status'] == 'INVALID'


@pytest.mark.parametrize('field', ['signature', 'retained_utc_ms', 'retention_until_ms', 'authenticated'])
def test_caller_cannot_supply_service_assertions(field):
    q = request()
    q[field] = 1
    assert validate_append_request(q)['status'] == 'INVALID'


@pytest.mark.parametrize('seq', [2, 3])
def test_later_sequence_requires_predecessor(seq):
    q = request(seq)
    q['previous_anchor_hash'] = '0'*64
    assert validate_append_request(q)['reason_code'] == 'SEQUENCE_INVALID'


def test_same_key_different_content_has_different_commitment():
    a = validate_append_request(request(2))
    q = request(2)
    q['subject_hash'] = 'd'*64
    b = validate_append_request(q)
    assert a['idempotency_key'] == b['idempotency_key']
    assert a['request_hash'] != b['request_hash']
    assert validate_append_request(dict(reversed(list(request(2).items())))) == a


def test_custom_object_hooks_are_not_executed():
    class Hostile(dict):
        def items(self):
            raise AssertionError('untrusted hook')
    assert validate_append_request(Hostile(request()))['status'] == 'INVALID'


def test_reservation_subject_must_equal_run_identity():
    q = request()
    q['subject_hash'] = 'b'*64
    assert validate_append_request(q)['reason_code'] == 'IDENTITY_MISMATCH'
