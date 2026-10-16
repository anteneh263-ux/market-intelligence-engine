"""Pure preflight for proposed receipt append requests; no storage or authority."""
from app.research.phase6_anchor_validation import (
    _Refused, _snapshot, _shape, _need, _hex, _integer, _token, _hash,
)


def validate_append_request(request):
    """Return a detached validated request and its commitment, or refuse.

    The service must separately authenticate callers, check current durable state,
    sign and retain a receipt. A valid request is never an append acknowledgement.
    """
    result = {
        'schema_version': 'p6.anchor_append_preflight.v1',
        'status': 'INVALID', 'reason_code': 'SCHEMA_INVALID',
        'request': None, 'request_hash': None, 'idempotency_key': None,
        'runtime_accepted': False,
    }
    try:
        q = _snapshot(request)
        _shape(q, 'schema_version run_id anchor_seq subject_kind subject_hash previous_anchor_hash service_identity')
        _need(q['schema_version'] == 'p6.anchor_append_request.v1')
        _need(_hex(q['run_id']) and _hex(q['subject_hash'])
              and _hex(q['previous_anchor_hash']) and _token(q['service_identity']))
        _need(_integer(q['anchor_seq']) and q['anchor_seq'] > 0)
        seq = q['anchor_seq']
        kind = 'RESERVATION' if seq == 1 else 'GENESIS' if seq == 2 else 'CHECKPOINT'
        _need(type(q['subject_kind']) is str and q['subject_kind'] == kind,
              'SEQUENCE_INVALID')
        _need(seq != 1 or q['subject_hash'] == q['run_id'], 'IDENTITY_MISMATCH')
        _need((q['previous_anchor_hash'] == '0' * 64) == (seq == 1),
              'SEQUENCE_INVALID')
    except _Refused as exc:
        result['reason_code'] = str(exc)
        return result
    result.update(status='VALID', reason_code=None, request=q, request_hash=_hash(q),
                  idempotency_key={'run_id': q['run_id'], 'anchor_seq': seq})
    return result
