"""Durable reducer integration, not prospective admission or anchor verification."""
import copy
import hashlib
import json
import os
import re
import threading
from app.paper.session import shadow_reducer_v1 as reducer
from app.research.phase6_object_store import ObjectWriter


def _need(ok, reason):
    if not ok:
        raise ValueError(reason)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


class TransitionSession:
    """Publish manifest before one reducer call; never resume after uncertainty."""

    def __init__(self, writer, config, *, context_hash):
        _need(type(writer) is ObjectWriter and type(context_hash) is str
              and re.fullmatch('[0-9a-f]{64}', context_hash) is not None, 'INPUT_INVALID')
        state = reducer.initial(copy.deepcopy(config))
        self._pid = os.getpid()
        self._thread = threading.get_ident()
        self._writer = writer
        self._claim = writer.claim()
        self._lifecycle = 'INCOMPLETE'
        self._busy = False
        self._objects = {}
        self._count = 0
        self._context = context_hash
        self._run = writer.header['run_id']
        try:
            state_hash = self._publish(state)
            genesis_hash = self._publish(dict(schema_version='p6.transition_genesis.v1',
                run_id=self._run, context_hash=self._context, method=reducer.METHOD,
                state_hash=state_hash))
            self._state, self._state_hash = state, state_hash
            self._genesis = self._head = genesis_hash
            self._lifecycle = 'OPEN'
        except BaseException:
            self._fail()
            raise

    def _owner(self):
        _need(self._pid == os.getpid() and self._thread == threading.get_ident(), 'SESSION_STOPPED')

    def _publish(self, value):
        raw = _canonical(value)
        digest = self._writer.put(raw, claim=self._claim)
        _need(digest == hashlib.sha256(raw).hexdigest(), 'INTEGRITY_FAILURE')
        self._objects[digest] = len(raw)
        return digest

    def _fail(self):
        self._lifecycle = 'INCOMPLETE'
        try:
            self._writer.close(claim=self._claim)
        except BaseException:
            pass  # Preserve the initiating failure; the session remains terminal.

    def apply(self, kind, payload):
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('SESSION_STOPPED')
        _need(self._lifecycle == 'OPEN', 'SESSION_STOPPED')
        self._busy = True
        try:
            _need(type(kind) is str and kind in ('BAR', 'QUOTE', 'STOP')
                  and type(payload) is dict, 'INPUT_INVALID')
            expected = {'BAR': {'observation', 'target'}, 'QUOTE': {'observation'}, 'STOP': {'reason'}}
            _need(set(payload) == expected[kind], 'INPUT_INVALID')
            payload = copy.deepcopy(payload)
            if kind == 'STOP':
                _need(payload['reason'] == 'OPERATOR_STOP', 'INPUT_INVALID')
            input_hash = self._publish(dict(schema_version='p6.transition_input.v1', kind=kind, payload=payload))
            manifest_hash = self._publish(dict(schema_version='p6.transition_manifest.v1',
                run_id=self._run, context_hash=self._context, method=reducer.METHOD,
                transition_seq=self._count + 1, kind=kind, input_hash=input_hash,
                prior_state_hash=self._state_hash, prior_commit_hash=self._head))
            _need(self._lifecycle == 'OPEN', 'SESSION_STOPPED')
            if kind == 'BAR':
                state = reducer.bar(copy.deepcopy(self._state), payload['observation'], payload['target'])
            elif kind == 'QUOTE':
                state = reducer.quote(copy.deepcopy(self._state), payload['observation'])
            else:
                state = reducer.stop(copy.deepcopy(self._state), payload['reason'])
            _need(self._lifecycle == 'OPEN', 'SESSION_STOPPED')
            state_hash = self._publish(state)
            commit_hash = self._publish(dict(schema_version='p6.transition_commit.v1',
                run_id=self._run, transition_seq=self._count + 1,
                manifest_hash=manifest_hash, state_hash=state_hash))
            _need(self._lifecycle == 'OPEN', 'SESSION_STOPPED')
            self._state, self._state_hash = copy.deepcopy(state), state_hash
            self._head = commit_hash
            self._count += 1
            if state['stopped']:
                self._lifecycle = 'STOPPED'
                self._writer.close(claim=self._claim)
            return commit_hash
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def snapshot(self):
        self._owner()
        return copy.deepcopy(dict(schema_version='p6.transition_status.v1', run_id=self._run,
            context_hash=self._context, lifecycle=self._lifecycle, transition_count=self._count,
            genesis_hash=self._genesis, commit_hash=self._head, state_hash=self._state_hash,
            state=self._state, objects=self._objects, technical_verification='LOCAL_ONLY',
            runtime_accepted=False))

    def close(self):
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('SESSION_STOPPED')
        try:
            self._writer.close(claim=self._claim)
        except BaseException:
            self._lifecycle = 'INCOMPLETE'
            raise
        if self._lifecycle == 'OPEN':
            self._lifecycle = 'CLOSED'

    def __enter__(self):
        self._owner()
        _need(self._lifecycle == 'OPEN' and not self._busy, 'SESSION_STOPPED')
        return self

    def __exit__(self, *_):
        self.close()
