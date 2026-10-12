"""Typed WP0 event and immediate-link checks; no journal or runtime authority."""
import re
from app.paper.session.shadow_reducer_v1 import amount
from app.research.phase6_start_validation import _snapshot, _Refused
from app.research.phase6_validation import canonical_hash as H

FIELDS = {
    'MANIFEST_VERIFIED': 'manifest_hash',
    'BAR_ACCEPTED': 'segment_hash receipt_hash bar_hash accepted_prefix_hash',
    'WARMUP': 'strategy_input_hash strategy_output_hash bar_count target callback_started_utc_ms callback_finished_utc_ms callback_started_monotonic_ns callback_finished_monotonic_ns clock_session_id',
    'DECISION': 'strategy_input_hash strategy_output_hash bar_count target callback_started_utc_ms callback_finished_utc_ms callback_started_monotonic_ns callback_finished_monotonic_ns clock_session_id',
    'NO_TRADE': 'decision_event_hash reason_code',
    'ORDER_INTENT': 'decision_event_hash side quantity clock_session_id',
    'SIMULATED_FILL': 'intent_event_hash quote_receipt_hash side quantity price fee',
    'REJECTED': 'intent_event_hash quote_receipt_hash reason_code',
    'CHECKPOINT_PREPARED': 'state_hash strategy_state_hash accepted_prefix_hash last_receipt_seq pending_intent_hash',
    'STOP': 'reason_code pending_intent_hash',
}
STOP_REASONS = frozenset('AUTHORIZATION_INVALID STORAGE_REFUSED RESERVATION_UNKNOWN DATA_GAP REVISION_CONFLICT LAG_EXCEEDED CLOCK_INVALID STRATEGY_INVALID RISK_STOP QUOTE_TIMEOUT EXECUTION_UNKNOWN ANCHOR_UNKNOWN INTEGRITY_FAILURE OPERATOR_STOP'.split())


def _need(ok, reason='SCHEMA_INVALID'):
    if not ok:
        raise _Refused(reason)


def _shape(value, fields):
    _need(type(value) is dict and set(value) == set(fields.split()))


def _integer(value):
    return type(value) is int and 0 <= value < 2**63


def _hash(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _token(value):
    return type(value) is str and re.fullmatch('[A-Za-z0-9_.:-]{1,128}', value) is not None


def _manifest(m):
    _shape(m, 'schema_version run_id segment_id plan_hash engine_version engine_code_hash strategy_code_hash params_hash prior_checkpoint_hash prior_anchor_hash persisted_utc_ms')
    _need(m['schema_version'] == 'p6.session_manifest.v1' and _token(m['engine_version'])
          and _integer(m['persisted_utc_ms']))
    _need(all(_hash(m[k]) for k in ('run_id', 'segment_id', 'plan_hash', 'engine_code_hash',
                                   'strategy_code_hash', 'params_hash', 'prior_checkpoint_hash', 'prior_anchor_hash')))


def _event(e):
    _shape(e, 'schema_version run_id session_id event_seq kind utc_ms monotonic_ns payload_hash previous_event_hash')
    _need(e['schema_version'] == 'p6.event.v1' and type(e['kind']) is str and e['kind'] in FIELDS)
    _need(all(_hash(e[k]) for k in ('run_id', 'session_id', 'payload_hash', 'previous_event_hash')))
    _need(all(_integer(e[k]) for k in ('event_seq', 'utc_ms', 'monotonic_ns')) and e['event_seq'] > 0)


def _payload(kind, p):
    _shape(p, 'schema_version ' + FIELDS[kind])
    _need(p['schema_version'] == 'p6.payload.' + kind.lower() + '.v1')
    for key in FIELDS[kind].split():
        value = p[key]
        if key.endswith('_hash'):
            _need((key == 'pending_intent_hash' and value is None) or _hash(value))
        elif key in ('bar_count', 'last_receipt_seq'):
            _need(_integer(value) and value > 0)
        elif key.startswith('callback_'):
            _need(_integer(value))
        elif key == 'clock_session_id':
            _need(_token(value))
        elif key == 'side':
            _need(type(value) is str and value in ('BUY', 'SELL'))
        elif key == 'reason_code':
            allowed = STOP_REASONS if kind == 'STOP' else {'ZERO_QUANTITY'} if kind == 'NO_TRADE' else {'CASH_CAP_ZERO'}
            _need(type(value) is str and value in allowed)
        elif key == 'target' and kind == 'WARMUP':
            _need(value is None)
        else:
            precision, limit = {'quantity': (8, 10**30), 'price': (9, 3*10**12),
                                'fee': (16, 10**30), 'target': (8, 1)}[key]
            try:
                numeric = amount(value, precision, limit)
            except ValueError:
                raise _Refused('SCHEMA_INVALID') from None
            _need(numeric >= 0 if key in ('fee', 'target') else numeric > 0)


def _check(q):
    _shape(q, 'event payload manifest previous_event')
    e, p, m, previous = (q[k] for k in ('event', 'payload', 'manifest', 'previous_event'))
    _manifest(m)
    _event(e)
    _payload(e['kind'], p)
    if previous is not None:
        _event(previous)
    session = H(m)
    _need(e['run_id'] == m['run_id'] and e['session_id'] == session, 'IDENTITY_MISMATCH')
    if previous is not None:
        _need(previous['run_id'] == m['run_id'] and previous['session_id'] == session, 'IDENTITY_MISMATCH')
    if e['kind'] == 'MANIFEST_VERIFIED':
        _need(p['manifest_hash'] == session, 'IDENTITY_MISMATCH')
    elif e['kind'] == 'BAR_ACCEPTED':
        _need(p['segment_hash'] == m['segment_id'], 'IDENTITY_MISMATCH')
    _need(e['utc_ms'] >= m['persisted_utc_ms'], 'CLOCK_INVALID')
    if previous is not None:
        _need(m['persisted_utc_ms'] <= previous['utc_ms'] <= e['utc_ms']
              and previous['monotonic_ns'] < e['monotonic_ns'], 'CLOCK_INVALID')
    if e['kind'] in ('WARMUP', 'DECISION'):
        _need(m['persisted_utc_ms'] <= p['callback_started_utc_ms'] <= p['callback_finished_utc_ms'] <= e['utc_ms']
              and p['callback_started_monotonic_ns'] <= p['callback_finished_monotonic_ns'] <= e['monotonic_ns'], 'CLOCK_INVALID')
    if previous is None:
        _need(e['event_seq'] == 1 and e['kind'] == 'MANIFEST_VERIFIED', 'SEQUENCE_INVALID')
    else:
        _need(e['event_seq'] == previous['event_seq'] + 1 and e['kind'] != 'MANIFEST_VERIFIED', 'SEQUENCE_INVALID')
        _need((previous['event_seq'] == 1) == (previous['kind'] == 'MANIFEST_VERIFIED'), 'SEQUENCE_INVALID')
    if previous is not None and previous['event_seq'] == 1:
        _need(previous['previous_event_hash'] == session, 'HASH_MISMATCH')
    _need(e['payload_hash'] == H(p) and e['previous_event_hash'] == (session if previous is None else H(previous)), 'HASH_MISMATCH')
    return H(e)


def verify_event(request):
    result = dict(schema_version='p6.event_validation.v1', structural_status='INVALID',
                  reason_code=None, canonical_hash=None,
                  external_reference_verification='UPSTREAM_REQUIRED', runtime_accepted=False)
    try:
        digest = _check(_snapshot(request))
    except _Refused as exc:
        result['reason_code'] = exc.reason
        return result
    except (RuntimeError, IndexError):
        result['reason_code'] = 'SCHEMA_INVALID'
        return result
    result.update(structural_status='VALID', canonical_hash=digest)
    return result
