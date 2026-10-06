"""Pure attempt identity; repeated identities never suppress reservations.

These internal helpers neither allocate a trial nor authorize its execution.
"""
import hashlib
import json
import re

from app.research.request_preflight import check_request_preflight

__all__ = ['check_attempt_fingerprint', 'check_trial_preflight']
_FIELDS = frozenset(('research_program_id', 'experiment_id', 'ordered_input_hashes',
                     'method_hash', 'intended_dataset_zone'))


def _hex64(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _failure():
    return {'check_id': 'ATTEMPT_FINGERPRINT', 'status': 'FAIL',
            'reason_code': 'ATTEMPT_FINGERPRINT_INVALID', 'attempt_fingerprint': None}


def check_attempt_fingerprint(record, *, research_program_id, experiment_id):
    """Validate five-field identity content and return its canonical SHA-256.

    This defensive helper's failures have one reason. In the composed path,
    request schema validation owns malformed field types and extra fields first.
    """
    if not _hex64(research_program_id) or not _hex64(experiment_id):
        return _failure()
    if type(record) is not dict or any(type(key) is not str for key in record):
        return _failure()
    if set(record) != _FIELDS:
        return _failure()
    if not all(_hex64(record[key]) for key in ('research_program_id', 'experiment_id', 'method_hash')):
        return _failure()
    if record['research_program_id'] != research_program_id or record['experiment_id'] != experiment_id:
        return _failure()
    hashes = record['ordered_input_hashes']
    if type(hashes) is not list or not hashes or not all(_hex64(value) for value in hashes):
        return _failure()
    zone = record['intended_dataset_zone']
    if type(zone) is not str or zone not in ('TRAIN', 'VAL', 'HOLDOUT'):
        return _failure()
    body = {key: record[key] for key in _FIELDS}
    canonical = json.dumps(body, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=False, allow_nan=False).encode('utf-8')
    return {'check_id': 'ATTEMPT_FINGERPRINT', 'status': 'PASS', 'reason_code': None,
            'attempt_fingerprint': hashlib.sha256(canonical).hexdigest()}


def check_trial_preflight(request):
    """Compose the existing request prefix and reservation fingerprint check.

    This is not the public registry report: no registry or downstream check runs.
    Non-reservation modes never receive an attempt fingerprint.
    """
    prefix = check_request_preflight(request)
    result = {'checks': [dict(check) for check in prefix['checks']],
              'reason_code': prefix['reason_code'], 'attempt_fingerprint': None}
    mode = None
    if type(request) is dict:
        for key, value in request.items():
            if type(key) is str and key == 'mode':
                mode = value
                break
    if type(mode) is not str or mode != 'RESERVATION':
        return result
    if prefix['reason_code'] is not None:
        result['checks'].append({'check_id': 'ATTEMPT_FINGERPRINT', 'status': 'NOT_EVALUATED'})
        return result
    fingerprint = check_attempt_fingerprint(request['attempt_fingerprint_record'],
        research_program_id=request['research_program_id'], experiment_id=request['experiment_id'])
    result['checks'].append({'check_id': 'ATTEMPT_FINGERPRINT', 'status': fingerprint['status']})
    result['reason_code'] = fingerprint['reason_code']
    result['attempt_fingerprint'] = fingerprint['attempt_fingerprint']
    return result
