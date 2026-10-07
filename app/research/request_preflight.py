"""Pure internal request prefix. A passing prefix never authorizes execution."""
import math
import re

from app.research.contract_binding import check_contract_binding

__all__ = ['check_request_preflight']

_FIELDS = {
    'RESERVATION': frozenset(('mode', 'research_program_id', 'experiment_id',
                             'contract_hash', 'contract_record', 'attempt_fingerprint_record')),
    'TIMING_VALIDATION': frozenset(('mode', 'research_program_id', 'experiment_id',
                                  'contract_hash', 'contract_record', 'trial_id', 'timing_ledger_rows')),
    'OUTCOME_APPEND': frozenset(('mode', 'research_program_id', 'trial_id', 'trial_outcome', 'result_hash')),
    'VERIFICATION_REPLAY': frozenset(('mode', 'research_program_id', 'verification_replay_record',
                                    'performance_data_accessed', 'append_reservation_requested')),
    'REGISTRY_STATE': frozenset(('mode', 'research_program_id')),
}
_ALL_FIELDS = frozenset().union(*_FIELDS.values())
_ASSIGNED = frozenset(('trial_id', 'reservation_sequence', 'reservation_utc_ms', 'N', 'N_total',
                       'row_hash', 'previous_row_hash', 'verification_replay_id',
                       'outcome_sequence', 'outcome_utc_ms'))
_BOUND_MODES = ('RESERVATION', 'TIMING_VALIDATION')
_FINGERPRINT_FIELDS = frozenset(('research_program_id', 'experiment_id', 'ordered_input_hashes',
                                'method_hash', 'intended_dataset_zone'))
_REPLAY_FIELDS = frozenset(('replay_of_trial_id', 'original_input_hashes',
                           'original_method_hash', 'original_result_hash'))


def _hex(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _hash_list(value):
    return type(value) is list and all(_hex(element) for element in value)


def _scan(request, mode):
    """Return assigned-field and structural flags without user callbacks.

    Iterative traversal handles cycles without Python recursion. Contract binding
    owns only the actual top-level contract subtree in the two bound modes.
    """
    assigned = malformed = False
    active = set()
    stack = [(request, 0, False)]
    while stack:
        node, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(node))
            continue
        kind = type(node)
        if kind in (dict, list):
            if id(node) in active:
                malformed = True
                continue
            active.add(id(node))
            stack.append((node, depth, True))
            if kind is list:
                stack.extend((v, depth + 1, False) for v in node)
                continue
            for key, value in node.items():
                if type(key) is not str:
                    malformed = True
                    stack.append((value, depth + 1, False))
                    continue
                if depth == 0 and key == 'contract_record' and mode in _BOUND_MODES:
                    continue
                allowed_trial = depth == 0 and key == 'trial_id' and mode in (
                    'TIMING_VALIDATION', 'OUTCOME_APPEND')
                if key in _ASSIGNED and not allowed_trial:
                    assigned = True
                stack.append((value, depth + 1, False))
        elif node is None or kind in (str, int, bool):
            continue
        elif kind is float:
            if not math.isfinite(node):
                malformed = True
        else:
            malformed = True
    return assigned, malformed


def _schema(request, mode, malformed):
    fields = _FIELDS[mode]
    # Foreign top-level fields take deterministic precedence over other schema errors.
    if any(type(k) is str and k not in fields and k in _ALL_FIELDS for k in request):
        return 'OUT_OF_MODE_FIELD'
    if malformed or set(request) != fields:
        return 'INPUT_SCHEMA_INVALID'
    for key in ('research_program_id', 'experiment_id', 'contract_hash', 'trial_id', 'result_hash'):
        if key in fields and not _hex(request[key]):
            return 'INPUT_SCHEMA_INVALID'
    valid = True
    if mode == 'RESERVATION':
        value = request['attempt_fingerprint_record']
        valid = (type(value) is dict and set(value) == _FINGERPRINT_FIELDS
                 and all(_hex(value[k]) for k in ('research_program_id', 'experiment_id', 'method_hash'))
                 and _hash_list(value['ordered_input_hashes'])
                 and type(value['intended_dataset_zone']) is str
                 and value['intended_dataset_zone'] in ('TRAIN', 'VAL', 'HOLDOUT'))
    elif mode == 'TIMING_VALIDATION':
        valid = type(request['timing_ledger_rows']) is list
    elif mode == 'OUTCOME_APPEND':
        value = request['trial_outcome']
        valid = type(value) is str and value in ('COMPLETED_VALID', 'COMPLETED_INVALID', 'CRASHED')
    elif mode == 'VERIFICATION_REPLAY':
        value = request['verification_replay_record']
        valid = (type(value) is dict and set(value) == _REPLAY_FIELDS
                 and all(_hex(value[k]) for k in ('replay_of_trial_id', 'original_method_hash',
                                                  'original_result_hash'))
                 and _hash_list(value['original_input_hashes'])
                 and type(request['performance_data_accessed']) is bool
                 and type(request['append_reservation_requested']) is bool)
    return None if valid else 'INPUT_SCHEMA_INVALID'


def check_request_preflight(request):
    """Run only the declared prefix; no fingerprint, eligibility or registry checks."""
    mode = None
    if type(request) is dict:
        # Avoid invoking equality methods on arbitrary Python mapping keys.
        for key, value in request.items():
            if type(key) is str and key == 'mode':
                mode = value
                break
    if type(mode) is not str or mode not in _FIELDS:
        return {'checks': [{'check_id': 'INPUT_MODE', 'status': 'FAIL'}],
                'reason_code': 'INPUT_MODE_INVALID'}
    names = ['INPUT_MODE', 'FORBIDDEN_ASSIGNED_FIELDS', 'REQUEST_SCHEMA']
    if mode in _BOUND_MODES:
        names.append('CONTRACT_BINDING')
    checks = [{'check_id': name, 'status': 'NOT_EVALUATED'} for name in names]
    checks[0]['status'] = 'PASS'
    assigned, malformed = _scan(request, mode)
    if assigned:
        checks[1]['status'] = 'FAIL'
        return {'checks': checks, 'reason_code': 'CALLER_SUPPLIED_RESERVATION_FIELD'}
    checks[1]['status'] = 'PASS'
    reason = _schema(request, mode, malformed)
    if reason:
        checks[2]['status'] = 'FAIL'
        return {'checks': checks, 'reason_code': reason}
    checks[2]['status'] = 'PASS'
    if mode in _BOUND_MODES:
        binding = check_contract_binding(request['contract_record'],
            contract_hash=request['contract_hash'], research_program_id=request['research_program_id'],
            experiment_id=request['experiment_id'])
        checks[3]['status'] = binding['status']
        return {'checks': checks, 'reason_code': binding['reason_code']}
    return {'checks': checks, 'reason_code': None}
