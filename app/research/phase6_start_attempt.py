"""One durable local binding attempt; never runtime admission or recovery."""
import copy
import hashlib
import json
import os
import secrets
import threading

from app.research.phase6_object_store import ObjectWriter, inspect_objects, read_verified_object
from app.research.phase6_start_validation import _snapshot, _Refused, verify_start_bindings
from app.research.phase6_validation import canonical_hash


def _need(condition, reason):
    if not condition:
        raise ValueError(reason)


def _detach(value):
    try:
        return _snapshot(value)
    except (_Refused, RuntimeError, IndexError):
        raise ValueError('INPUT_INVALID') from None


def _time(clock):
    value = clock()
    _need(type(value) is int and 0 <= value < 2**63, 'CLOCK_INVALID')
    return value


def _request(context, receipts, head, challenge, now):
    request = {k: copy.deepcopy(context[k]) for k in (
        'plan', 'authorization', 'reservation', 'genesis',
        'registry_identity', 'strategy_state_hash')}
    request['anchor'] = dict(trust=copy.deepcopy(context['trust']), receipts=receipts,
                            head=head, challenge=challenge, now_utc_ms=now)
    return request


class StartBindingAttempt:
    """Persist candidate context, fresh challenge and a single checked response."""

    def __init__(self, writer, context, *, clock):
        _need(type(writer) is ObjectWriter and callable(clock), 'INPUT_INVALID')
        context = _detach(context)
        _need(type(context) is dict and set(context) == {
            'plan', 'authorization', 'reservation', 'genesis',
            'registry_identity', 'strategy_state_hash', 'trust'}, 'INPUT_INVALID')
        now = _time(clock)
        preflight = verify_start_bindings(_request(context, [], None, None, now))
        _need(preflight['reason_code'] == 'ANCHOR_INVALID'
              and preflight['anchor_reason_code'] is None, 'CONTEXT_INVALID')
        _need(canonical_hash(context['trust']) == context['plan']['anchor_trust_hash'],
              'CONTEXT_INVALID')
        authorization = context['authorization']
        _need(writer.header['owner'] == authorization['owner']
              and writer.header['run_id'] == canonical_hash(context['reservation'])
              and writer.root == authorization['evidence_root'], 'WRITER_MISMATCH')
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._writer, self._clock, self._context = writer, clock, context
        self._claim = writer.claim()
        self._lifecycle, self._busy = 'INCOMPLETE', True
        self._objects = {}
        self._challenge = self._challenge_hash = self._context_hash = None
        self._request_hash = self._result_hash = None
        try:
            self._lifecycle = 'PREPARING'
            for key in ('plan', 'authorization', 'reservation', 'genesis'):
                self._publish(context[key])
            self._context_hash = self._publish(context)
            nonce = secrets.token_hex(32)
            _need(type(nonce) is str and len(nonce) == 64
                  and all(c in '0123456789abcdef' for c in nonce), 'NONCE_INVALID')
            issued = _time(clock)
            self._active('PREPARING')
            _need(now <= issued < context['plan']['start_ms'], 'CLOCK_INVALID')
            challenge = dict(nonce=nonce, issued_utc_ms=issued)
            challenge_hash = self._publish(dict(schema_version='p6.start_challenge.v1',
                context_hash=self._context_hash, challenge=challenge))
            self._active('PREPARING')
            self._challenge, self._challenge_hash = challenge, challenge_hash
            self._lifecycle = 'AWAITING_BINDINGS'
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def _owner(self):
        _need(self._pid == os.getpid() and self._thread == threading.get_ident(),
              'ATTEMPT_STOPPED')

    def _active(self, expected):
        _need(self._lifecycle == expected, 'ATTEMPT_STOPPED')

    def _publish(self, value):
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
        digest = self._writer.put(raw, claim=self._claim)
        _need(digest == hashlib.sha256(raw).hexdigest(), 'INTEGRITY_FAILURE')
        _need(self._lifecycle in ('PREPARING', 'CHECKING'), 'ATTEMPT_STOPPED')
        self._objects[digest] = len(raw)
        return digest

    def _fail(self):
        self._lifecycle = 'INCOMPLETE'
        try:
            self._writer.close(claim=self._claim)
        except BaseException:
            pass  # Keep the original failure and any uncertain publications.

    def finish(self, receipts, head):
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('ATTEMPT_STOPPED')
        self._active('AWAITING_BINDINGS')
        self._busy, self._lifecycle = True, 'CHECKING'
        try:
            observations = _detach(dict(receipts=receipts, head=head))
            now = _time(self._clock)
            self._active('CHECKING')
            _need(self._challenge['issued_utc_ms'] <= now < self._context['plan']['start_ms'],
                  'CLOCK_INVALID')
            request = _request(self._context, observations['receipts'], observations['head'],
                               copy.deepcopy(self._challenge), now)
            request_hash = self._publish(request)
            validation = verify_start_bindings(request)
            self._active('CHECKING')
            result_hash = self._publish(dict(schema_version='p6.start_result.v1',
                challenge_hash=self._challenge_hash, request_hash=request_hash,
                validation=validation))
            self._writer.close(claim=self._claim)
            self._active('CHECKING')
            inspect_objects(self._writer.root, header=self._writer.header,
                            objects=dict(self._objects))
            self._active('CHECKING')
            self._request_hash, self._result_hash = request_hash, result_hash
            self._lifecycle = 'BOUND_LOCAL' if validation['binding_status'] == 'VALID' else 'REJECTED'
            return copy.deepcopy(validation)
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def export_request(self):
        """Read the acknowledged start request from its closed verified store."""
        self._owner()
        _need(not self._busy and self._lifecycle == 'BOUND_LOCAL', 'ATTEMPT_STOPPED')
        self._busy = True
        try:
            raw = read_verified_object(self._writer.root,header=self._writer.header,
                objects=dict(self._objects),digest=self._request_hash)
            self._active('BOUND_LOCAL')
            return _detach(json.loads(raw))
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def snapshot(self):
        self._owner()
        return copy.deepcopy(dict(schema_version='p6.start_attempt_status.v1',
            run_id=self._writer.header['run_id'], lifecycle=self._lifecycle,
            context_hash=self._context_hash, challenge=self._challenge,
            challenge_hash=self._challenge_hash, request_hash=self._request_hash,
            result_hash=self._result_hash, objects=self._objects,
            technical_verification='LOCAL_ONLY', runtime_accepted=False))

    def close(self):
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('ATTEMPT_STOPPED')
        try:
            self._writer.close(claim=self._claim)
        except BaseException:
            self._fail()
            raise
        if self._lifecycle == 'AWAITING_BINDINGS':
            self._lifecycle = 'CLOSED'
