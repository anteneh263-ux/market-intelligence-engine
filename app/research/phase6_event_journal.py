"""Immutable typed event links, not execution or full journal lifecycle authority."""
import copy
import hashlib
import json
import os
import threading
from app.research.phase6_object_store import ObjectWriter, inspect_objects, _bound
from app.research.phase6_start_validation import _snapshot, _Refused
from app.research.phase6_event_validation import verify_event


def _need(ok, reason):
    if not ok:
        raise ValueError(reason)


def _detach(value):
    try:
        return _snapshot(value)
    except (_Refused, RuntimeError, IndexError):
        raise ValueError('INPUT_INVALID') from None


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _prepare(manifest, previous, kind, payload, utc_ms, monotonic_ns):
    supplied = _detach(dict(kind=kind, payload=payload, utc_ms=utc_ms, monotonic_ns=monotonic_ns))
    payload_raw = _bytes(supplied['payload'])
    event = dict(schema_version='p6.event.v1', run_id=manifest.get('run_id'),
        session_id=_hash(_bytes(manifest)), event_seq=1 if previous is None else previous['event_seq'] + 1,
        kind=supplied['kind'], utc_ms=supplied['utc_ms'], monotonic_ns=supplied['monotonic_ns'],
        payload_hash=_hash(payload_raw), previous_event_hash=_hash(_bytes(manifest if previous is None else previous)))
    event_raw = _bytes(event)
    validation = verify_event(dict(event=event, payload=supplied['payload'],
                                   manifest=manifest, previous_event=previous))
    _need(validation['structural_status'] == 'VALID', validation['reason_code'])
    _need(validation['canonical_hash'] == _hash(event_raw), 'INTEGRITY_FAILURE')
    return payload_raw, event_raw


class EventJournal:
    """Own one writer and derive each event from the last acknowledged head."""

    def __init__(self, writer, manifest, *, utc_ms, monotonic_ns):
        _need(type(writer) is ObjectWriter, 'INPUT_INVALID')
        manifest = _detach(manifest)
        _need(type(manifest) is dict, 'INPUT_INVALID')
        manifest_raw = _bytes(manifest)
        first = dict(schema_version='p6.payload.manifest_verified.v1', manifest_hash=_hash(manifest_raw))
        payload_raw, event_raw = _prepare(manifest, None, 'MANIFEST_VERIFIED', first, utc_ms, monotonic_ns)
        _need(writer.header['run_id'] == manifest['run_id'], 'WRITER_MISMATCH')
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._writer, self._manifest = writer, manifest
        self._claim = writer.claim()
        self._lifecycle, self._busy = 'PREPARING', True
        self._objects, self._count, self._last, self._head = {}, 0, None, None
        try:
            self._publish(manifest_raw)
            self._publish(payload_raw)
            head = self._publish(event_raw)
            self._check_bound('PREPARING')
            self._last, self._head, self._count = json.loads(event_raw), head, 1
            self._lifecycle = 'OPEN'
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def _owner(self):
        _need(self._pid == os.getpid() and self._thread == threading.get_ident(), 'JOURNAL_STOPPED')

    def _active(self, expected):
        _need(self._lifecycle == expected, 'JOURNAL_STOPPED')

    def _check_bound(self, expected):
        self._active(expected)
        # Same storage implementation: recheck the descriptor immediately before ack.
        _bound(self._writer.root, self._writer._fd)
        self._active(expected)

    def _publish(self, raw):
        digest = self._writer.put(raw, claim=self._claim)
        _need(digest == _hash(raw), 'INTEGRITY_FAILURE')
        _need(self._lifecycle in ('OPEN', 'PREPARING'), 'JOURNAL_STOPPED')
        self._objects[digest] = len(raw)
        return digest

    def _fail(self):
        self._lifecycle = 'INCOMPLETE'
        try:
            self._writer.close(claim=self._claim)
        except BaseException:
            pass  # Preserve the original failure and uncertain publications.

    def append(self, kind, payload, *, utc_ms, monotonic_ns):
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('JOURNAL_STOPPED')
        self._active('OPEN')
        self._busy = True
        try:
            payload_raw, event_raw = _prepare(self._manifest, self._last, kind, payload, utc_ms, monotonic_ns)
            self._active('OPEN')
            self._publish(payload_raw)
            head = self._publish(event_raw)
            self._check_bound('OPEN')
            self._last, self._head = json.loads(event_raw), head
            self._count += 1
            return head
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def snapshot(self):
        self._owner()
        return copy.deepcopy(dict(schema_version='p6.event_journal_status.v1',
            run_id=self._manifest['run_id'], session_id=_hash(_bytes(self._manifest)),
            lifecycle=self._lifecycle, event_count=self._count, event_head_hash=self._head,
            last_event=self._last, objects=self._objects,
            technical_verification='LOCAL_ONLY', runtime_accepted=False))

    def close(self):
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('JOURNAL_STOPPED')
        if self._lifecycle != 'OPEN':
            return
        self._busy = True
        try:
            self._writer.close(claim=self._claim)
            self._active('OPEN')
            inspect_objects(self._writer.root, header=self._writer.header, objects=dict(self._objects))
            self._active('OPEN')
            self._lifecycle = 'CLOSED'
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False
