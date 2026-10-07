"""Internal contract binding only; no registry operation or trust attestation."""
import hashlib
import json
import re

__all__ = ['check_contract_binding']


def _hex64(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _json_tree(value, ancestors):
    """Reject non-JSON values before serialization, without custom callbacks."""
    kind = type(value)
    if value is None or kind in (str, int, bool, float):
        return
    if kind not in (dict, list):
        raise ValueError('not a JSON value')
    identity = id(value)
    if identity in ancestors:
        raise ValueError('cyclic JSON')
    ancestors.add(identity)
    try:
        if kind is dict:
            for key, child in value.items():
                if type(key) is not str:
                    raise ValueError('not a JSON key')
                _json_tree(child, ancestors)
        else:
            for child in value:
                _json_tree(child, ancestors)
    finally:
        ancestors.remove(identity)


def _result(reason=None):
    return {'check_id': 'CONTRACT_BINDING', 'status': 'FAIL' if reason else 'PASS',
            'reason_code': reason}


def check_contract_binding(contract_record, *, contract_hash, research_program_id, experiment_id):
    """Bind JSON contract bytes to caller identities; never validate provenance.

    Intended after request schema validation in RESERVATION/TIMING_VALIDATION.
    Returns one internal check result, not a public registry report.
    """
    invalid = 'CONTRACT_BINDING_INVALID'
    if not all(_hex64(v) for v in (contract_hash, research_program_id, experiment_id)):
        return _result(invalid)
    if type(contract_record) is not dict:
        return _result(invalid)
    try:
        _json_tree(contract_record, set())
        if contract_record.get('schema_version') != 'wp5_0a1.contract.v4':
            return _result(invalid)
        if contract_record.get('contract_status') != 'VALID':
            return _result(invalid)
        embedded = contract_record.get('contract_hash')
        if not _hex64(embedded):
            return _result(invalid)
        body = {key: value for key, value in contract_record.items() if key != 'contract_hash'}
        encoded = json.dumps(body, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False).encode('utf-8')
        computed = hashlib.sha256(encoded).hexdigest()
    except (TypeError, ValueError, OverflowError, RecursionError):
        return _result(invalid)
    if computed != embedded or computed != contract_hash:
        return _result(invalid)
    program = contract_record.get('research_program_id')
    if not _hex64(program):
        return _result(invalid)
    if program != research_program_id:
        return _result('WRONG_RESEARCH_PROGRAM_ID')
    experiment = contract_record.get('experiment_id')
    if not _hex64(experiment) or experiment != experiment_id:
        return _result(invalid)
    if contract_record.get('timing_ledger_schema_version') != 'wp5_0.timing_ledger_row.v1':
        return _result('TIMING_SCHEMA_UNSUPPORTED')
    return _result()
