"""Pure signed-anchor validation. No signer, transport, retention or run authority."""
import hashlib
import json
import re


class _Refused(Exception):
    pass


def _need(condition, reason='SCHEMA_INVALID'):
    if not condition:
        raise _Refused(reason)


def _snapshot(value, depth=0, budget=None):
    # Copy only plain bounded primitives, never caller-defined deepcopy hooks.
    if budget is None:
        budget = [300000]
    budget[0] -= 1
    _need(depth <= 8 and budget[0] >= 0)
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        _need(0 <= value < 2**63)
        return value
    if type(value) is str:
        _need(len(value) <= 256 and value.isascii())
        return value
    if type(value) is list:
        _need(len(value) <= 10000)
        try:
            return [_snapshot(value[i], depth + 1, budget) for i in range(len(value))]
        except IndexError:
            raise _Refused('SCHEMA_INVALID') from None
    if type(value) is dict:
        _need(len(value) <= 20)
        try:
            result = {}
            for key, item in value.items():
                _need(type(key) is str and len(key) <= 128 and key.isascii())
                result[key] = _snapshot(item, depth + 1, budget)
            return result
        except RuntimeError:
            raise _Refused('SCHEMA_INVALID') from None
    raise _Refused('SCHEMA_INVALID')


def _shape(value, keys):
    _need(type(value) is dict and set(value) == set(keys.split()))


def _hex(value, length=64):
    return type(value) is str and len(value) == length and re.fullmatch('[0-9a-f]+', value) is not None


def _integer(value):
    return type(value) is int and 0 <= value < 2**63


def _token(value):
    return type(value) is str and re.fullmatch('[A-Za-z0-9_.:-]{1,128}', value) is not None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _kind(index):
    return 'RESERVATION' if index == 0 else 'GENESIS' if index == 1 else 'CHECKPOINT'


def _trust_schema(t):
    _shape(t, 'schema_version service_identity endpoint_id algorithm public_key_hex not_before_utc_ms not_after_utc_ms max_head_age_ms max_clock_skew_ms')
    _need(t['schema_version'] == 'p6.anchor_trust.v1' and t['algorithm'] == 'Ed25519'
          and _token(t['service_identity']) and _token(t['endpoint_id']) and _hex(t['public_key_hex']))
    _need(all(_integer(t[k]) for k in ('not_before_utc_ms', 'not_after_utc_ms', 'max_head_age_ms', 'max_clock_skew_ms')))
    _need(t['not_before_utc_ms'] < t['not_after_utc_ms'] and t['max_head_age_ms'] > 0)


def _schema(q):
    _shape(q, 'trust expected_trust_hash expected_run_id expected_subjects receipts head challenge now_utc_ms')
    _need(_hex(q['expected_trust_hash']) and _hex(q['expected_run_id']) and _integer(q['now_utc_ms']))
    t = q['trust']
    _trust_schema(t)
    _need(type(q['receipts']) is list and type(q['expected_subjects']) is list
          and 1 <= len(q['receipts']) <= 10000 and len(q['receipts']) == len(q['expected_subjects']))
    for subject in q['expected_subjects']:
        _shape(subject, 'subject_kind subject_hash')
        _need(type(subject['subject_kind']) is str and subject['subject_kind'] in ('RESERVATION', 'GENESIS', 'CHECKPOINT') and _hex(subject['subject_hash']))
    for receipt in q['receipts']:
        _shape(receipt, 'schema_version run_id anchor_seq subject_kind subject_hash previous_anchor_hash retained_utc_ms service_identity signature')
        _need(receipt['schema_version'] == 'p6.anchor_receipt.v1' and _hex(receipt['run_id'])
              and _integer(receipt['anchor_seq']) and receipt['anchor_seq'] > 0
              and type(receipt['subject_kind']) is str and receipt['subject_kind'] in ('RESERVATION', 'GENESIS', 'CHECKPOINT')
              and _hex(receipt['subject_hash']) and _hex(receipt['previous_anchor_hash'])
              and _integer(receipt['retained_utc_ms']) and _token(receipt['service_identity'])
              and _hex(receipt['signature'], 128))
    if q['head'] is None:
        _need(q['challenge'] is None)
    else:
        h, c = q['head'], q['challenge']
        _shape(h, 'schema_version run_id anchor_seq anchor_hash service_identity nonce observed_utc_ms retention_until_ms signature')
        _need(h['schema_version'] == 'p6.anchor_head.v1' and _hex(h['run_id'])
              and _integer(h['anchor_seq']) and h['anchor_seq'] > 0 and _hex(h['anchor_hash'])
              and _token(h['service_identity']) and _hex(h['nonce'])
              and _integer(h['observed_utc_ms']) and _integer(h['retention_until_ms']) and _hex(h['signature'], 128))
        _shape(c, 'nonce issued_utc_ms')
        _need(_hex(c['nonce']) and _integer(c['issued_utc_ms']))


def _signatures(q):
    try:
        from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        raise _Refused('DEPENDENCY_UNAVAILABLE') from None
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(q['trust']['public_key_hex']))
        documents = q['receipts'] + ([] if q['head'] is None else [q['head']])
        for document in documents:
            unsigned = {k: v for k, v in document.items() if k != 'signature'}
            key.verify(bytes.fromhex(document['signature']), _canonical(unsigned))
    except UnsupportedAlgorithm:
        raise _Refused('DEPENDENCY_UNAVAILABLE') from None
    except (InvalidSignature, ValueError):
        raise _Refused('SIGNATURE_INVALID') from None


def _validate(q):
    _schema(q)
    t, receipts, h = q['trust'], q['receipts'], q['head']
    _need(_hash(t) == q['expected_trust_hash'], 'TRUST_MISMATCH')
    for receipt, subject in zip(receipts, q['expected_subjects']):
        _need(receipt['run_id'] == q['expected_run_id'] and receipt['service_identity'] == t['service_identity']
              and receipt['subject_kind'] == subject['subject_kind'] and receipt['subject_hash'] == subject['subject_hash'], 'IDENTITY_MISMATCH')
    if h is not None:
        _need(h['run_id'] == q['expected_run_id'] and h['service_identity'] == t['service_identity']
              and h['nonce'] == q['challenge']['nonce'], 'IDENTITY_MISMATCH')
    _signatures(q)
    previous = '0' * 64
    for i, receipt in enumerate(receipts):
        _need(receipt['anchor_seq'] == i + 1 and receipt['subject_kind'] == _kind(i)
              and receipt['previous_anchor_hash'] == previous, 'SEQUENCE_INVALID')
        previous = _hash(receipt)
    _need(len({s['subject_hash'] for s in q['expected_subjects']}) == len(receipts), 'SEQUENCE_INVALID')
    if h is not None:
        _need(h['anchor_seq'] == len(receipts) and h['anchor_hash'] == previous, 'SEQUENCE_INVALID')
    now, skew = q['now_utc_ms'], t['max_clock_skew_ms']
    low, high = t['not_before_utc_ms'], t['not_after_utc_ms']
    _need(low <= now <= high, 'CLOCK_INVALID')
    last_time = low
    for receipt in receipts:
        retained = receipt['retained_utc_ms']
        _need(last_time <= retained <= high and retained <= now + skew, 'CLOCK_INVALID')
        last_time = retained
    if h is not None:
        issued, observed = q['challenge']['issued_utc_ms'], h['observed_utc_ms']
        age = t['max_head_age_ms']
        _need(low <= issued <= now and now - issued <= age and low <= observed <= high
              and issued - skew <= observed <= now + skew and now - observed <= age
              and observed >= last_time, 'CLOCK_INVALID')
        _need(h['retention_until_ms'] > max(now, observed), 'RETENTION_EXPIRED')
    return previous


def verify_anchors(request):
    """Validate supplied evidence only; authentic service/clock inputs remain upstream."""
    result = dict(schema_version='p6.anchor_validation.v1', cryptographic_status='INVALID',
        reason_code=None, head_status='NOT_VALIDATED', anchor_count=0, last_anchor_hash=None,
        runtime_accepted=False, external_service_verification='UPSTREAM_REQUIRED')
    try:
        request = _snapshot(request)
        last = _validate(request)
    except _Refused as exc:
        result['reason_code'] = str(exc)
        return result
    result.update(cryptographic_status='VALID', head_status='OFFLINE' if request['head'] is None else 'MATCHED',
                  anchor_count=len(request['receipts']), last_anchor_hash=last)
    return result
