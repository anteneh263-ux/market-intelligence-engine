"""Immutable typed event links, not execution or full journal lifecycle authority."""
import copy
import hashlib
import json
import os
import threading
import re
from fractions import Fraction
from app.paper.session import diagnostic_strategy_v1 as strategy, shadow_reducer_v1 as reducer
from app.research import phase6_bar_binding as bar_binding
from app.research.phase6_validation import validate
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


def _sample_clock(clock):
    value = _detach(clock())
    _need(type(value) is dict and set(value) == {'utc_ms', 'monotonic_ns', 'clock_session_id'}, 'CLOCK_INVALID')
    _need(all(type(value[k]) is int and 0 <= value[k] < 2**63 for k in ('utc_ms', 'monotonic_ns'))
          and type(value['clock_session_id']) is str
          and re.fullmatch('[A-Za-z0-9_.:-]{1,128}', value['clock_session_id']) is not None, 'CLOCK_INVALID')
    return value


def _ordered_clock(before, after):
    _need(before['clock_session_id'] == after['clock_session_id']
          and before['utc_ms'] <= after['utc_ms']
          and before['monotonic_ns'] <= after['monotonic_ns'], 'CLOCK_INVALID')


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
        self._diagnostic_context, self._bar_done = None, False
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

    def run_diagnostic(self, plan, params, state, *, segment_seq, clock):
        """One fixed strategy call with durable inputs; never runtime admission."""
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('JOURNAL_STOPPED')
        self._active('OPEN')
        self._busy = True
        try:
            _need(self._count == 2 and self._last['kind'] == 'BAR_ACCEPTED', 'SEQUENCE_INVALID')
            _need(callable(clock), 'INPUT_INVALID')
            q = _detach(dict(plan=plan, params=params, state=state, segment_seq=segment_seq))
            p, pa, s, seq = (q[k] for k in ('plan', 'params', 'state', 'segment_seq'))
            v = validate(dict(document=p, plan=None, reservation=None, prior_receipts=[],
                              prior_segment=None, payloads={}, clock_probes={}))
            _need(v['structural_status'] == 'VALID' and v['document_kind'] == 'PLAN', 'PLAN_INVALID')
            # Canonical schema checks do not execute the strategy.
            pa = strategy.decode(strategy.encode(pa))
            s = strategy.decode(strategy.encode(s))
            expected_initial = strategy.initial(pa)
            _need(s.get('schema_version') == strategy.STATE, 'STATE_INVALID')
            _need(_hash(_bytes(p)) == self._manifest['plan_hash']
                  and _hash(_bytes(pa)) == p['params_hash'] == self._manifest['params_hash']
                  and p['strategy_code_hash'] == self._manifest['strategy_code_hash']
                  and pa['warmup_bars'] == p['warmup_bars']
                  and s['params_hash'] == expected_initial['params_hash'], 'IDENTITY_MISMATCH')
            _need(Fraction(p['max_target']) >= Fraction(3, 4), 'POLICY_UNSUPPORTED')
            _need(type(seq) is int and 0 < seq < 2**63 and seq == s['bar_count'] + 1
                  and self._manifest['segment_id'] not in ('0'*64, s['last_segment_hash']), 'SEQUENCE_INVALID')
            expected_state = dict(s, bar_count=seq, last_segment_hash=self._manifest['segment_id'])
            warmup = seq <= pa['warmup_bars']
            expected = dict(state=expected_state, kind='WARMUP' if warmup else 'DECISION',
                target=None if warmup else pa['targets'][(seq-pa['warmup_bars']-1) % 2])
            plan_hash = self._publish(_bytes(p))
            params_hash = self._publish(strategy.encode(pa))
            input_hash = self._publish(strategy.encode(s))
            invocation_hash = self._publish(_bytes(dict(schema_version='p6.diagnostic_invocation.v1',
                run_id=self._manifest['run_id'], session_id=_hash(_bytes(self._manifest)),
                plan_hash=plan_hash, params_hash=params_hash, input_state_hash=input_hash,
                segment_seq=seq, segment_hash=self._manifest['segment_id'], prior_event_hash=self._head)))
            self._check_bound('OPEN')
            started = _sample_clock(clock)
            self._check_bound('OPEN')
            _need(started['utc_ms'] >= self._last['utc_ms']
                  and started['monotonic_ns'] > self._last['monotonic_ns'], 'CLOCK_INVALID')
            argument_state, argument_params = copy.deepcopy(s), copy.deepcopy(pa)
            output = strategy.advance(argument_state, argument_params,
                                      segment_seq=seq, segment_hash=self._manifest['segment_id'])
            # Freeze return-time evidence before any further external callback.
            output = _detach(output)
            returned_state = _bytes(_detach(argument_state))
            returned_params = _bytes(_detach(argument_params))
            self._check_bound('OPEN')
            finished = _sample_clock(clock)
            self._check_bound('OPEN')
            _ordered_clock(started, finished)
            _need(returned_state == strategy.encode(s) and returned_params == strategy.encode(pa), 'STRATEGY_INVALID')
            _need(type(output) is dict and set(output) == {'state', 'kind', 'target'}, 'STRATEGY_INVALID')
            # Check exact types as well as values (bool must never stand in for int).
            _need(strategy.encode(output['state']) == strategy.encode(expected_state)
                  and type(output['kind']) is str and output['kind'] == expected['kind']
                  and type(output['target']) is type(expected['target'])
                  and output['target'] == expected['target'], 'STRATEGY_INVALID')
            output_hash = self._publish(strategy.encode(output['state']))
            observed = _sample_clock(clock)
            self._check_bound('OPEN')
            _ordered_clock(finished, observed)
            payload = dict(schema_version='p6.payload.'+output['kind'].lower()+'.v1',
                strategy_input_hash=input_hash, strategy_output_hash=output_hash,
                bar_count=seq, target=output['target'],
                callback_started_utc_ms=started['utc_ms'], callback_finished_utc_ms=finished['utc_ms'],
                callback_started_monotonic_ns=started['monotonic_ns'],
                callback_finished_monotonic_ns=finished['monotonic_ns'],
                clock_session_id=started['clock_session_id'])
            payload_raw, event_raw = _prepare(self._manifest, self._last, output['kind'], payload,
                                               observed['utc_ms'], observed['monotonic_ns'])
            self._active('OPEN')
            self._publish(payload_raw)
            head = self._publish(event_raw)
            commit_hash = self._publish(_bytes(dict(schema_version='p6.diagnostic_call_commit.v1',
                invocation_hash=invocation_hash, event_hash=head, output_state_hash=output_hash)))
            self._check_bound('OPEN')
            self._last, self._head = json.loads(event_raw), head
            self._count += 1
            self._diagnostic_context = copy.deepcopy(dict(plan=p, output=output, payload=payload, event_hash=head, commit_hash=commit_hash))
            return copy.deepcopy(dict(schema_version='p6.diagnostic_call_result.v1',
                state=output['state'], target=output['target'], event_hash=head,
                invocation_hash=invocation_hash, commit_hash=commit_hash,
                technical_verification='LOCAL_ONLY', runtime_accepted=False,
                external_reference_verification='UPSTREAM_REQUIRED'))
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def run_bar(self, state, observation, *, clock):
        """Persist one local bar transition and optional post-intent time barrier."""
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('JOURNAL_STOPPED')
        self._active('OPEN')
        self._busy = True
        try:
            d = self._diagnostic_context
            _need(not self._bar_done and d is not None and self._count == 3
                  and self._head == d['event_hash'], 'SEQUENCE_INVALID')
            _need(callable(clock), 'INPUT_INVALID')
            q = _detach(dict(state=state, observation=observation))
            s, o = q['state'], q['observation']
            target = bar_binding.prepare(d['plan'], d, s, o)
            input_hash = self._publish(_bytes(s))
            observation_hash = self._publish(_bytes(o))
            invocation_hash = self._publish(_bytes(dict(schema_version='p6.bar_invocation.v1',
                run_id=self._manifest['run_id'], session_id=_hash(_bytes(self._manifest)),
                diagnostic_commit_hash=d['commit_hash'], decision_event_hash=d['event_hash'],
                input_state_hash=input_hash, observation_hash=observation_hash, target=target)))
            self._check_bound('OPEN')
            argument_state, argument_observation = copy.deepcopy(s), copy.deepcopy(o)
            output = reducer.bar(argument_state, argument_observation, target)
            output = _detach(output)
            returned_state = _bytes(_detach(argument_state))
            returned_observation = _bytes(_detach(argument_observation))
            self._check_bound('OPEN')
            _need(returned_state == _bytes(s) and returned_observation == _bytes(o), 'EXECUTION_INVALID')
            bar_binding.check_output(s, o, target, output)
            output_hash = self._publish(_bytes(output))
            kind = None
            if output['pending'] is not None:
                kind = 'ORDER_INTENT'
                payload = dict(schema_version='p6.payload.order_intent.v1',
                    decision_event_hash=d['event_hash'], side=output['pending']['side'],
                    quantity=output['pending']['quantity'], clock_session_id=o['clock_session_id'])
            elif output['stopped']:
                kind = 'STOP'
                payload = dict(schema_version='p6.payload.stop.v1', reason_code='RISK_STOP', pending_intent_hash=None)
            elif target is not None:
                kind = 'NO_TRADE'
                payload = dict(schema_version='p6.payload.no_trade.v1', decision_event_hash=d['event_hash'], reason_code='ZERO_QUANTITY')
            event_hash = barrier_hash = None
            if kind is not None:
                observed = _sample_clock(clock)
                self._check_bound('OPEN')
                _need(observed['clock_session_id'] == o['clock_session_id']
                      and observed['utc_ms'] >= self._last['utc_ms']
                      and observed['monotonic_ns'] > self._last['monotonic_ns'], 'CLOCK_INVALID')
                payload_raw, event_raw = _prepare(self._manifest, self._last, kind, payload,
                                                   observed['utc_ms'], observed['monotonic_ns'])
                self._active('OPEN')
                self._publish(payload_raw)
                event_hash = self._publish(event_raw)
                self._check_bound('OPEN')
                if kind == 'ORDER_INTENT':
                    barrier = _sample_clock(clock)
                    self._check_bound('OPEN')
                    _ordered_clock(observed, barrier)
                    _need(barrier['monotonic_ns'] > observed['monotonic_ns'], 'CLOCK_INVALID')
                    barrier_hash = self._publish(_bytes(dict(schema_version='p6.intent_barrier.v1',
                        run_id=self._manifest['run_id'], session_id=_hash(_bytes(self._manifest)),
                        intent_event_hash=event_hash, **barrier)))
            commit_hash = self._publish(_bytes(dict(schema_version='p6.bar_call_commit.v1',
                invocation_hash=invocation_hash, output_state_hash=output_hash,
                event_hash=event_hash, barrier_hash=barrier_hash)))
            self._check_bound('OPEN')
            if event_hash is not None:
                self._last, self._head = json.loads(event_raw), event_hash
                self._count += 1
            self._bar_done = True
            return copy.deepcopy(dict(schema_version='p6.bar_call_result.v1', state=output,
                event_hash=event_hash, barrier_hash=barrier_hash, commit_hash=commit_hash,
                invocation_hash=invocation_hash, technical_verification='LOCAL_ONLY',
                runtime_accepted=False, prospective_quote_ready=False,
                external_reference_verification='UPSTREAM_REQUIRED'))
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
