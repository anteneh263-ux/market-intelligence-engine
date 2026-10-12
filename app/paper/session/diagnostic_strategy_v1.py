"""Fixed alternating diagnostic targets; pure state, no execution authority."""
import hashlib
import json
import re

PARAMS = 'p6.diagnostic_params.v1'
STATE = 'p6.diagnostic_state.v1'
ZERO = '0' * 64


def _need(ok, reason='INPUT_INVALID'):
    if not ok:
        raise ValueError(reason)


def _integer(value):
    return type(value) is int and 0 <= value < 2**63


def _hash(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _record(value):
    # Copy plain primitives only, before any schema or identity checks.
    _need(type(value) is dict and len(value) <= 4)
    result = {}
    try:
        for key, item in value.items():
            _need(type(key) is str and key.isascii() and len(key) <= 64)
            if type(item) is list:
                item = list(item)
                _need(len(item) == 2 and all(type(x) is str and x.isascii() and len(x) <= 64 for x in item))
            else:
                _need(type(item) in (str, int))
                if type(item) is str:
                    _need(item.isascii() and len(item) <= 128)
                else:
                    _need(_integer(item))
            result[key] = item
    except (RuntimeError, IndexError):
        raise ValueError('INPUT_INVALID') from None
    return result


def _params(value):
    value = _record(value)
    _need(set(value) == {'schema_version', 'warmup_bars', 'targets'})
    _need(value['schema_version'] == PARAMS and _integer(value['warmup_bars'])
          and type(value['targets']) is list and value['targets'] == ['0.25', '0.75'])
    return value


def _state(value):
    value = _record(value)
    _need(set(value) == {'schema_version', 'params_hash', 'bar_count', 'last_segment_hash'})
    _need(value['schema_version'] == STATE and _hash(value['params_hash'])
          and _hash(value['last_segment_hash']) and _integer(value['bar_count']))
    _need((value['last_segment_hash'] == ZERO) == (value['bar_count'] == 0))
    return value


def encode(record):
    record = _record(record)
    _need(type(record.get('schema_version')) is str)
    if record['schema_version'] == PARAMS:
        record = _params(record)
    elif record['schema_version'] == STATE:
        record = _state(record)
    else:
        raise ValueError('INPUT_INVALID')
    return json.dumps(record, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def decode(raw):
    _need(type(raw) is bytes and len(raw) <= 4096)
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result)
            result[key] = value
        return result
    def invalid(*args):
        raise ValueError('INPUT_INVALID')
    try:
        record = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                            parse_float=invalid, parse_constant=invalid)
        _need(encode(record) == raw)
        return record
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('INPUT_INVALID') from None


def initial(params):
    params = _params(params)
    return dict(schema_version=STATE, params_hash=hashlib.sha256(encode(params)).hexdigest(),
                bar_count=0, last_segment_hash=ZERO)


def advance(state, params, *, segment_seq, segment_hash):
    state, params = _state(state), _params(params)
    _need(_integer(segment_seq) and _hash(segment_hash))
    _need(state['params_hash'] == hashlib.sha256(encode(params)).hexdigest(), 'IDENTITY_MISMATCH')
    _need(segment_seq == state['bar_count'] + 1 and segment_hash != ZERO
          and segment_hash != state['last_segment_hash'], 'SEQUENCE_INVALID')
    state.update(bar_count=segment_seq, last_segment_hash=segment_hash)
    warmup = segment_seq <= params['warmup_bars']
    target = None if warmup else params['targets'][(segment_seq - params['warmup_bars'] - 1) % 2]
    return dict(state=state, kind='WARMUP' if warmup else 'DECISION', target=target)
