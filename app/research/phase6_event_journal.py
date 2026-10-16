"""Immutable typed event links, not execution or full journal lifecycle authority."""
import copy
import hashlib
import json
import os
import threading
import re
from fractions import Fraction
from app.paper.session import diagnostic_strategy_v1 as strategy, shadow_reducer_v1 as reducer
from app.research import phase6_bar_binding as bar_binding, phase6_quote_binding as quote_binding
from app.research.phase6_checkpoint_anchor_validation import verify_checkpoint_anchors
from app.research.phase6_genesis_admission import verify_genesis_admission
from app.research.phase6_checkpoint_continuation import verify_checkpoint_continuation
from app.research.phase6_prior_receipt_validation import verify_prior_receipts
from app.research.phase6_prior_state_validation import verify_prior_state
from app.research.phase6_intake_validation import verify_intake_prefix
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

    def __init__(self, writer, manifest, *, utc_ms, monotonic_ns, checkpoint_admission=None, genesis_admission=None):
        _need(type(writer) is ObjectWriter, 'INPUT_INVALID')
        manifest = _detach(manifest)
        _need(type(manifest) is dict, 'INPUT_INVALID')
        manifest_raw = _bytes(manifest)
        first = dict(schema_version='p6.payload.manifest_verified.v1', manifest_hash=_hash(manifest_raw))
        payload_raw, event_raw = _prepare(manifest, None, 'MANIFEST_VERIFIED', first, utc_ms, monotonic_ns)
        _need(writer.header['run_id'] == manifest['run_id'], 'WRITER_MISMATCH')
        _need(checkpoint_admission is None or genesis_admission is None, 'ADMISSION_CONFLICT')
        genesis = None
        if genesis_admission is not None:
            package = _detach(dict(manifest=manifest,proof=genesis_admission,utc_ms=utc_ms))
            verified = verify_genesis_admission(package)
            _need(verified['binding_status'] == 'VALID', verified['reason_code'] or 'ADMISSION_REQUIRED')
            genesis = dict(package=package,validation=verified)
        admission = None
        if checkpoint_admission is not None:
            package = _detach(dict(manifest=manifest, proof=checkpoint_admission, utc_ms=utc_ms))
            verified = verify_checkpoint_continuation(package)
            _need(verified['binding_status'] == 'VALID', verified['reason_code'] or 'ADMISSION_REQUIRED')
            prior_event = package['proof']['checkpoints'][-1]['event']
            _need(monotonic_ns > prior_event['monotonic_ns'], 'CLOCK_INVALID')
            admission = dict(package=package, validation=verified)
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._writer, self._manifest = writer, manifest
        self._claim = writer.claim()
        self._lifecycle, self._busy = 'PREPARING', True
        self._objects, self._count, self._last, self._head = {}, 0, None, None
        self._diagnostic_context, self._bar_done = None, False
        self._bar_context, self._quote_done = None, False
        self._accepted_context, self._execution_context = None, None
        self._intake_context = None
        self._quote_input_context = None
        self._prior_state_context = None
        self._prior_receipt_context = None
        self._checkpoint_admission = None
        self._genesis_admission = None
        self._checkpoint_context = None
        try:
            if genesis is not None:
                package_hash = self._publish(_bytes(genesis['package']))
                validation_hash = self._publish(_bytes(genesis['validation']))
                commit_hash = self._publish(_bytes(dict(schema_version='p6.genesis_admission.v1',
                    run_id=manifest['run_id'],session_id=_hash(manifest_raw),package_hash=package_hash,
                    validation_hash=validation_hash,genesis_hash=manifest['prior_checkpoint_hash'],
                    prior_anchor_hash=manifest['prior_anchor_hash'])))
                self._check_bound('PREPARING')
                self._genesis_admission = dict(**genesis,commit_hash=commit_hash)
            if admission is not None:
                package_hash = self._publish(_bytes(admission['package']))
                validation_hash = self._publish(_bytes(admission['validation']))
                commit_hash = self._publish(_bytes(dict(schema_version='p6.checkpoint_admission.v1',
                    run_id=manifest['run_id'],session_id=_hash(manifest_raw),package_hash=package_hash,
                    validation_hash=validation_hash,prior_checkpoint_hash=manifest['prior_checkpoint_hash'],
                    prior_anchor_hash=manifest['prior_anchor_hash'])))
                self._check_bound('PREPARING')
                self._checkpoint_admission = dict(**admission,commit_hash=commit_hash)
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
        _need(self._lifecycle in ('OPEN', 'PREPARING', 'CHECKPOINTED_LOCAL'), 'JOURNAL_STOPPED')
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
            if self._count == 2 and self._last['kind'] == 'BAR_ACCEPTED':
                self._accepted_context = json.loads(payload_raw)
            return head
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def bind_intake(self, bundle):
        """Persist a verified supplied bar prefix before a diagnostic call."""
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('JOURNAL_STOPPED')
        self._active('OPEN')
        self._busy = True
        try:
            _need(self._intake_context is None and self._count == 2
                  and self._last['kind'] == 'BAR_ACCEPTED' and self._accepted_context is not None
                  and self._diagnostic_context is None, 'SEQUENCE_INVALID')
            q = _detach(bundle)
            result = verify_intake_prefix(q)
            _need(result['binding_status'] == 'VALID', result['reason_code'] or 'INPUT_INVALID')
            segment, receipt = q['segments'][-1], q['receipts'][-1]
            bar = q['payloads'][segment['bar_hash']]
            _need(_hash(_bytes(q['plan'])) == self._manifest['plan_hash']
                  and _hash(_bytes(q['reservation'])) == self._manifest['run_id']
                  and _hash(_bytes(segment)) == self._manifest['segment_id']
                  and _hash(_bytes(receipt)) == segment['accepted_receipt_hash'], 'IDENTITY_MISMATCH')
            expected = dict(schema_version='p6.payload.bar_accepted.v1',
                segment_hash=_hash(_bytes(segment)), receipt_hash=_hash(_bytes(receipt)),
                bar_hash=segment['bar_hash'], accepted_prefix_hash=result['accepted_prefix_hash'])
            _need(_bytes(expected) == _bytes(self._accepted_context), 'IDENTITY_MISMATCH')
            _need(receipt['received_utc_ms'] <= self._manifest['persisted_utc_ms'] <= self._last['utc_ms']
                  and receipt['received_monotonic_ns'] <= self._last['monotonic_ns'], 'CLOCK_INVALID')
            bundle_hash = self._publish(_bytes(q))
            result_hash = self._publish(_bytes(result))
            commit_hash = self._publish(_bytes(dict(schema_version='p6.intake_binding.v1',
                run_id=self._manifest['run_id'], session_id=_hash(_bytes(self._manifest)),
                bundle_hash=bundle_hash, validation_hash=result_hash, bar_event_hash=self._head)))
            self._check_bound('OPEN')
            self._intake_context = copy.deepcopy(dict(plan=q['plan'], segment=segment, receipt=receipt,
                bar=bar, prefix_hash=result['accepted_prefix_hash'], commit_hash=commit_hash, bundle=q))
            return dict(schema_version='p6.intake_binding_result.v1', bundle_hash=bundle_hash,
                commit_hash=commit_hash, technical_verification='LOCAL_ONLY', runtime_accepted=False,
                external_reference_verification='UPSTREAM_REQUIRED')
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def bind_prior_state(self, prior_artifacts, *, params, state, strategy_state):
        """Persist local prior-state continuity; never grants session admission."""
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('JOURNAL_STOPPED')
        self._active('OPEN')
        self._busy = True
        try:
            _need(self._intake_context is not None and self._prior_state_context is None
                  and self._diagnostic_context is None and self._count == 2
                  and self._last['kind'] == 'BAR_ACCEPTED', 'SEQUENCE_INVALID')
            q = _detach(dict(manifest=self._manifest, intake=self._intake_context['bundle'],
                prior=prior_artifacts, params=params, state=state, strategy_state=strategy_state))
            if self._checkpoint_admission is not None:
                expected = self._checkpoint_admission['package']['proof']['checkpoints'][-1]
                _need(_bytes(q['prior']) == _bytes(expected), 'IDENTITY_MISMATCH')
            if self._genesis_admission is not None:
                expected = self._genesis_admission['package']['proof']['start']['genesis']
                _need(_bytes(q['prior']) == _bytes(expected), 'IDENTITY_MISMATCH')
            result = verify_prior_state(q)
            _need(result['binding_status'] == 'VALID', result['reason_code'] or 'INPUT_INVALID')
            package_hash = self._publish(_bytes(q))
            validation_hash = self._publish(_bytes(result))
            commit_hash = self._publish(_bytes(dict(schema_version='p6.prior_state_binding.v1',
                run_id=self._manifest['run_id'], session_id=_hash(_bytes(self._manifest)),
                intake_binding_hash=self._intake_context['commit_hash'], package_hash=package_hash,
                validation_hash=validation_hash, prior_hash=result['prior_hash'], bar_event_hash=self._head)))
            self._check_bound('OPEN')
            self._prior_state_context = copy.deepcopy(dict(params=q['params'], state=q['state'],
                strategy_state=q['strategy_state'], commit_hash=commit_hash, package=q))
            return dict(schema_version='p6.prior_state_binding_result.v1',package_hash=package_hash,
                validation_hash=validation_hash,commit_hash=commit_hash,technical_verification='LOCAL_ONLY',
                runtime_accepted=False,external_reference_verification='UPSTREAM_REQUIRED')
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def bind_prior_receipts(self, proof):
        """Persist supplied receipt ancestry before the next local diagnostic."""
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('JOURNAL_STOPPED')
        self._active('OPEN')
        self._busy = True
        try:
            prior = self._prior_state_context
            _need(prior is not None and self._prior_receipt_context is None
                  and self._diagnostic_context is None and self._count == 2
                  and self._last['kind'] == 'BAR_ACCEPTED', 'SEQUENCE_INVALID')
            q = _detach(dict(prior_state=prior['package'], proof=proof))
            result = verify_prior_receipts(q)
            _need(result['binding_status'] == 'VALID', result['reason_code'] or 'INPUT_INVALID')
            package_hash = self._publish(_bytes(q))
            validation_hash = self._publish(_bytes(result))
            commit_hash = self._publish(_bytes(dict(schema_version='p6.prior_receipt_binding.v1',
                run_id=self._manifest['run_id'],session_id=_hash(_bytes(self._manifest)),
                prior_state_binding_hash=prior['commit_hash'],package_hash=package_hash,
                validation_hash=validation_hash,bar_event_hash=self._head)))
            self._check_bound('OPEN')
            self._prior_receipt_context = dict(commit_hash=commit_hash)
            return dict(schema_version='p6.prior_receipt_binding_result.v1',package_hash=package_hash,
                validation_hash=validation_hash,commit_hash=commit_hash,technical_verification='LOCAL_ONLY',
                runtime_accepted=False,external_reference_verification='UPSTREAM_REQUIRED')
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
            prior = self._prior_state_context
            if self._genesis_admission is not None:
                _need(self._intake_context is not None and prior is not None, 'ADMISSION_REQUIRED')
                _need(type(seq) is int and seq == 1, 'SEQUENCE_INVALID')
            if self._checkpoint_admission is not None:
                _need(self._intake_context is not None and prior is not None
                      and self._prior_receipt_context is not None, 'ADMISSION_REQUIRED')
                _need(type(seq) is int and seq == self._checkpoint_admission['validation']['next_segment_seq'], 'SEQUENCE_INVALID')
            if prior is not None:
                _need(_bytes(pa) == _bytes(prior['params']) and _bytes(s) == _bytes(prior['strategy_state']), 'STATE_INVALID')
            bound = self._intake_context
            if bound is not None:
                _need(_bytes(p) == _bytes(bound['plan']) and type(seq) is int
                      and seq == bound['segment']['segment_seq'], 'IDENTITY_MISMATCH')
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
            invocation = dict(schema_version='p6.diagnostic_invocation.v1',
                run_id=self._manifest['run_id'], session_id=_hash(_bytes(self._manifest)),
                plan_hash=plan_hash, params_hash=params_hash, input_state_hash=input_hash,
                segment_seq=seq, segment_hash=self._manifest['segment_id'], prior_event_hash=self._head)
            if bound is not None:
                invocation.update(schema_version='p6.diagnostic_invocation.v2', intake_binding_hash=bound['commit_hash'])
            if prior is not None:
                invocation.update(schema_version='p6.diagnostic_invocation.v3', prior_state_binding_hash=prior['commit_hash'])
            if self._prior_receipt_context is not None:
                invocation.update(schema_version='p6.diagnostic_invocation.v4', prior_receipt_binding_hash=self._prior_receipt_context['commit_hash'])
            if self._checkpoint_admission is not None:
                invocation.update(schema_version='p6.diagnostic_invocation.v5', checkpoint_admission_hash=self._checkpoint_admission['commit_hash'])
            if self._genesis_admission is not None:
                invocation.update(schema_version='p6.diagnostic_invocation.v6', genesis_admission_hash=self._genesis_admission['commit_hash'])
            invocation_hash = self._publish(_bytes(invocation))
            self._check_bound('OPEN')
            started = _sample_clock(clock)
            self._check_bound('OPEN')
            _need(started['utc_ms'] >= self._last['utc_ms']
                  and started['monotonic_ns'] > self._last['monotonic_ns'], 'CLOCK_INVALID')
            if self._checkpoint_admission is not None:
                admission = self._checkpoint_admission['package']
                current = dict(admission, utc_ms=started['utc_ms'])
                _need(verify_checkpoint_continuation(current)['binding_status'] == 'VALID', 'ADMISSION_EXPIRED')
                _need(started['clock_session_id'] == prior['state']['clock_session_id'], 'CLOCK_INVALID')
            if self._genesis_admission is not None:
                current = dict(self._genesis_admission['package'],utc_ms=started['utc_ms'])
                _need(verify_genesis_admission(current)['binding_status'] == 'VALID', 'ADMISSION_EXPIRED')
                _need(started['clock_session_id'] == self._intake_context['receipt']['clock_session_id'], 'CLOCK_INVALID')
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
            if self._prior_state_context is not None:
                _need(_bytes(s) == _bytes(self._prior_state_context['state']), 'STATE_INVALID')
            bound = self._intake_context
            if bound is not None:
                expected = dict(close_ms=bound['bar']['close_ms'], close=bound['bar']['close'],
                    **{k: bound['receipt'][k] for k in ('received_utc_ms', 'received_monotonic_ns', 'clock_session_id')})
                _need(type(o) is dict and all(k in o for k in expected)
                      and _bytes({k: o[k] for k in expected}) == _bytes(expected), 'IDENTITY_MISMATCH')
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
                    barrier_record = dict(schema_version='p6.intent_barrier.v1',
                        run_id=self._manifest['run_id'], session_id=_hash(_bytes(self._manifest)),
                        intent_event_hash=event_hash, **barrier)
                    barrier_hash = self._publish(_bytes(barrier_record))
            commit_hash = self._publish(_bytes(dict(schema_version='p6.bar_call_commit.v1',
                invocation_hash=invocation_hash, output_state_hash=output_hash,
                event_hash=event_hash, barrier_hash=barrier_hash)))
            self._check_bound('OPEN')
            if event_hash is not None:
                self._last, self._head = json.loads(event_raw), event_hash
                self._count += 1
            self._bar_done = True
            if output['pending'] is not None:
                self._bar_context = copy.deepcopy(dict(state=output, barrier=barrier_record, barrier_hash=barrier_hash, event_hash=event_hash, commit_hash=commit_hash))
            self._execution_context = copy.deepcopy(dict(state=output, commit_hash=commit_hash, head=self._head, count=self._count))
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

    def bind_quote_receipts(self, bundle):
        """Bind the first eligible quote in a supplied extension, not a live stream."""
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('JOURNAL_STOPPED')
        self._active('OPEN')
        self._busy = True
        try:
            bound, c = self._intake_context, self._bar_context
            _need(bound is not None and c is not None and not self._quote_done
                  and self._quote_input_context is None and self._count == 4
                  and self._head == c['event_hash'], 'SEQUENCE_INVALID')
            q = _detach(bundle)
            result = verify_intake_prefix(q)
            _need(result['binding_status'] == 'VALID', result['reason_code'] or 'INPUT_INVALID')
            old = bound['bundle']; n = len(old['receipts'])
            _need(all(_bytes(q[k]) == _bytes(old[k]) for k in ('plan','reservation','segments','prefix'))
                  and len(q['receipts']) > n and _bytes(q['receipts'][:n]) == _bytes(old['receipts']), 'IDENTITY_MISMATCH')
            for name in ('payloads', 'clock_probes'):
                _need(all(k in q[name] and _bytes(q[name][k]) == _bytes(v) for k,v in old[name].items()), 'IDENTITY_MISMATCH')
            barrier = c['barrier']; eligible = []
            for index, receipt in enumerate(q['receipts'][n:], n):
                _need(receipt['kind'] == 'QUOTE', 'POLICY_UNSUPPORTED')
                _need(receipt['clock_session_id'] == barrier['clock_session_id'], 'CLOCK_INVALID')
                later = receipt['received_monotonic_ns'] > barrier['monotonic_ns']
                _need(receipt['received_utc_ms'] >= barrier['utc_ms'] if later
                      else receipt['received_utc_ms'] <= barrier['utc_ms'], 'CLOCK_INVALID')
                if later:eligible.append(index)
            _need(eligible == [len(q['receipts'])-1], 'SEQUENCE_INVALID')
            receipt = q['receipts'][-1]; price = q['payloads'][receipt['normalized_payload_hash']]
            observation = dict(bid=price['bid'], ask=price['ask'], **{k: receipt[k] for k in
                ('received_utc_ms','received_monotonic_ns','clock_session_id')})
            receipt_hash = _hash(_bytes(receipt))
            bundle_hash = self._publish(_bytes(q))
            validation_hash = self._publish(_bytes(result))
            commit_hash = self._publish(_bytes(dict(schema_version='p6.quote_receipt_binding.v1',
                run_id=self._manifest['run_id'], session_id=_hash(_bytes(self._manifest)),
                intake_binding_hash=bound['commit_hash'], barrier_hash=c['barrier_hash'],
                intent_event_hash=c['event_hash'], bundle_hash=bundle_hash,
                validation_hash=validation_hash, quote_receipt_hash=receipt_hash)))
            self._check_bound('OPEN')
            self._quote_input_context = copy.deepcopy(dict(observation=observation,
                receipt_hash=receipt_hash, receipt_seq=receipt['receipt_seq'], commit_hash=commit_hash))
            return dict(schema_version='p6.quote_receipt_binding_result.v1', bundle_hash=bundle_hash,
                commit_hash=commit_hash, quote_receipt_hash=receipt_hash,
                technical_verification='LOCAL_ONLY', runtime_accepted=False,
                external_reference_verification='UPSTREAM_REQUIRED')
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def run_quote(self, observation, *, quote_receipt_hash, clock):
        """One local simulated quote using a separately persisted barrier adapter."""
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('JOURNAL_STOPPED')
        self._active('OPEN')
        self._busy = True
        try:
            c = self._bar_context
            _need(not self._quote_done and c is not None and self._count == 4
                  and self._head == c['event_hash'], 'SEQUENCE_INVALID')
            _need(callable(clock), 'INPUT_INVALID')
            q = _detach(dict(observation=observation, receipt_hash=quote_receipt_hash))
            o, receipt_hash = q['observation'], q['receipt_hash']
            quote_input = self._quote_input_context
            if self._intake_context is not None:
                _need(quote_input is not None and _bytes(o) == _bytes(quote_input['observation'])
                      and receipt_hash == quote_input['receipt_hash'], 'IDENTITY_MISMATCH')
            session_id = _hash(_bytes(self._manifest))
            adapted = quote_binding.prepare(c, o, receipt_hash, self._manifest['run_id'], session_id)
            adapted_hash = self._publish(_bytes(adapted))
            observation_hash = self._publish(_bytes(o))
            invocation = dict(schema_version='p6.quote_invocation.v1',
                policy=quote_binding.POLICY, run_id=self._manifest['run_id'], session_id=session_id,
                bar_commit_hash=c['commit_hash'], raw_state_hash=_hash(_bytes(c['state'])),
                adapted_state_hash=adapted_hash, barrier_hash=c['barrier_hash'],
                intent_event_hash=c['event_hash'], observation_hash=observation_hash, quote_receipt_hash=receipt_hash)
            if quote_input is not None:
                invocation.update(schema_version='p6.quote_invocation.v2', quote_binding_hash=quote_input['commit_hash'])
            invocation_hash = self._publish(_bytes(invocation))
            self._check_bound('OPEN')
            argument_state, argument_observation = copy.deepcopy(adapted), copy.deepcopy(o)
            output = reducer.quote(argument_state, argument_observation)
            output = _detach(output)
            returned_state = _bytes(_detach(argument_state))
            returned_observation = _bytes(_detach(argument_observation))
            self._check_bound('OPEN')
            _need(returned_state == _bytes(adapted) and returned_observation == _bytes(o), 'EXECUTION_INVALID')
            outcome = quote_binding.check_output(adapted, o, output)
            output_hash = self._publish(_bytes(output))
            kind = outcome['kind']
            first = dict(schema_version='p6.payload.'+kind.lower()+'.v1', **outcome['fields'])
            if kind == 'STOP':
                first['pending_intent_hash'] = c['event_hash']
            else:
                first.update(intent_event_hash=c['event_hash'], quote_receipt_hash=receipt_hash)
            events = [(kind, first)]
            if output['reason_code'] == 'RISK_STOP':
                events.append(('STOP', dict(schema_version='p6.payload.stop.v1',
                                           reason_code='RISK_STOP', pending_intent_hash=None)))
            previous = self._last
            event_hashes = []
            for event_kind, payload in events:
                observed = _sample_clock(clock)
                self._check_bound('OPEN')
                _need(observed['clock_session_id'] == o['clock_session_id']
                      and observed['utc_ms'] >= o['received_utc_ms']
                      and observed['monotonic_ns'] >= o['received_monotonic_ns'], 'CLOCK_INVALID')
                payload_raw, event_raw = _prepare(self._manifest, previous, event_kind, payload,
                                                   observed['utc_ms'], observed['monotonic_ns'])
                self._active('OPEN')
                self._publish(payload_raw)
                event_hashes.append(self._publish(event_raw))
                previous = json.loads(event_raw)
            commit_hash = self._publish(_bytes(dict(schema_version='p6.quote_call_commit.v1',
                invocation_hash=invocation_hash, output_state_hash=output_hash, event_hashes=event_hashes)))
            self._check_bound('OPEN')
            self._last, self._head = previous, event_hashes[-1]
            self._count += len(event_hashes)
            self._quote_done = True
            self._execution_context = copy.deepcopy(dict(state=output, commit_hash=commit_hash, head=self._head, count=self._count))
            return copy.deepcopy(dict(schema_version='p6.quote_call_result.v1', state=output,
                adapted_state_hash=adapted_hash, event_hashes=event_hashes, commit_hash=commit_hash,
                invocation_hash=invocation_hash, technical_verification='LOCAL_ONLY', runtime_accepted=False,
                prospective_quote_ready=False, external_reference_verification='UPSTREAM_REQUIRED'))
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def prepare_checkpoint(self, *, last_receipt_seq, clock):
        """Seal local execution/strategy/event linkage; references remain upstream."""
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('JOURNAL_STOPPED')
        self._active('OPEN')
        self._busy = True
        try:
            c, d, accepted = self._execution_context, self._diagnostic_context, self._accepted_context
            _need(self._bar_done and c is not None and d is not None and accepted is not None
                  and self._head == c['head'] and self._count == c['count'], 'SEQUENCE_INVALID')
            _need(type(last_receipt_seq) is int and d['output']['state']['bar_count'] <= last_receipt_seq < 2**63,
                  'INPUT_INVALID')
            bound = self._intake_context
            if bound is not None:
                n = bound['receipt']['receipt_seq']
                expected_seq = self._quote_input_context['receipt_seq'] if self._quote_done and self._quote_input_context is not None else n
                _need(accepted['accepted_prefix_hash'] == bound['prefix_hash']
                      and (not self._quote_done or self._quote_input_context is not None)
                      and last_receipt_seq == expected_seq, 'SEQUENCE_INVALID')
            _need(callable(clock), 'INPUT_INVALID')
            s = c['state']
            reducer.state_check(s)
            ss = strategy.decode(strategy.encode(d['output']['state']))
            _need(s['bar_count'] == ss['bar_count'], 'STATE_INVALID')
            pending_hash = None
            if s['pending'] is not None:
                _need(self._quote_done and s['stopped'] and s['reason_code'] == 'QUOTE_TIMEOUT'
                      and self._bar_context is not None, 'PENDING_UNRESOLVED')
                pending_hash = self._bar_context['event_hash']
            state_hash = self._publish(_bytes(s))
            strategy_hash = self._publish(strategy.encode(ss))
            observed = _sample_clock(clock)
            self._check_bound('OPEN')
            _need(observed['clock_session_id'] == s['clock_session_id']
                  and observed['utc_ms'] >= s['last_action_utc_ms']
                  and observed['monotonic_ns'] >= s['last_action_monotonic_ns'], 'CLOCK_INVALID')
            payload = dict(schema_version='p6.payload.checkpoint_prepared.v1',
                state_hash=state_hash, strategy_state_hash=strategy_hash,
                accepted_prefix_hash=accepted['accepted_prefix_hash'], last_receipt_seq=last_receipt_seq,
                pending_intent_hash=pending_hash)
            payload_raw, event_raw = _prepare(self._manifest, self._last, 'CHECKPOINT_PREPARED', payload,
                                                observed['utc_ms'], observed['monotonic_ns'])
            self._publish(payload_raw)
            event_hash = self._publish(event_raw)
            checkpoint = dict(schema_version='p6.checkpoint.v1', run_id=self._manifest['run_id'],
                session_id=_hash(_bytes(self._manifest)), segment_seq=ss['bar_count'],
                **{k: s[k] for k in ('cash', 'units', 'cumulative_fees', 'cumulative_realized_pnl',
                    'cost_basis', 'mark_price', 'equity', 'peak_equity', 'stopped')},
                strategy_state_hash=strategy_hash, accepted_prefix_hash=accepted['accepted_prefix_hash'],
                last_receipt_seq=last_receipt_seq, pending_intent_hash=pending_hash,
                journal_head_hash=event_hash, journal_count=self._count + 1,
                previous_checkpoint_hash=self._manifest['prior_checkpoint_hash'])
            checkpoint_hash = self._publish(_bytes(checkpoint))
            commit_hash = self._publish(_bytes(dict(schema_version='p6.checkpoint_commit.v1',
                checkpoint_hash=checkpoint_hash, event_hash=event_hash, state_hash=state_hash,
                strategy_state_hash=strategy_hash, execution_commit_hash=c['commit_hash'],
                diagnostic_commit_hash=d['commit_hash'])))
            self._check_bound('OPEN')
            self._last, self._head = json.loads(event_raw), event_hash
            self._count += 1
            self._checkpoint_context = dict(checkpoint_hash=checkpoint_hash,commit_hash=commit_hash)
            self._lifecycle = 'CHECKPOINTED_LOCAL'
            return dict(schema_version='p6.checkpoint_result.v1', checkpoint_hash=checkpoint_hash,
                event_hash=event_hash, commit_hash=commit_hash, technical_verification='LOCAL_ONLY',
                runtime_accepted=False, external_reference_verification='UPSTREAM_REQUIRED')
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def bind_checkpoint_anchor(self, proof, *, clock):
        """Persist supplied signed acknowledgement for this writer's own checkpoint."""
        self._owner()
        if self._busy:
            self._fail()
            raise ValueError('JOURNAL_STOPPED')
        self._active('CHECKPOINTED_LOCAL')
        self._busy = True
        try:
            admission = self._genesis_admission or self._checkpoint_admission
            _need(admission is not None and self._checkpoint_context is not None, 'ADMISSION_REQUIRED')
            p = _detach(proof)
            _need(callable(clock), 'INPUT_INVALID')
            observed = _sample_clock(clock)
            self._check_bound('CHECKPOINTED_LOCAL')
            _need(observed['clock_session_id'] == self._execution_context['state']['clock_session_id']
                  and observed['utc_ms'] >= self._last['utc_ms']
                  and observed['monotonic_ns'] >= self._last['monotonic_ns'], 'CLOCK_INVALID')
            initial = verify_checkpoint_anchors(p)
            _need(initial['binding_status'] == 'VALID', initial['reason_code'] or 'ANCHOR_INVALID')
            previous = admission['package']['proof']
            expected_checkpoints = previous.get('checkpoints', [])
            _need(_bytes(p['start']) == _bytes(previous['start'])
                  and _bytes(p['checkpoints'][:-1]) == _bytes(expected_checkpoints)
                  and _bytes(p['anchor']['receipts'][:-1]) == _bytes(previous['anchor']['receipts']), 'ANCESTRY_MISMATCH')
            own = p['checkpoints'][-1]
            _need(_bytes(own['manifest']) == _bytes(self._manifest)
                  and _hash(_bytes(own['checkpoint'])) == self._checkpoint_context['checkpoint_hash']
                  and _hash(_bytes(own['checkpoint_commit'])) == self._checkpoint_context['commit_hash']
                  and _hash(_bytes(own['event'])) == self._head, 'CHECKPOINT_MISMATCH')
            _need(p['anchor']['now_utc_ms'] <= observed['utc_ms'], 'CLOCK_INVALID')
            fresh = copy.deepcopy(p)
            fresh['anchor']['now_utc_ms'] = observed['utc_ms']
            result = verify_checkpoint_anchors(fresh)
            _need(result['binding_status'] == 'VALID', result['reason_code'] or 'ANCHOR_INVALID')
            self._check_bound('CHECKPOINTED_LOCAL')
            package_hash = self._publish(_bytes(dict(proof=p,observed=observed)))
            validation_hash = self._publish(_bytes(result))
            commit_hash = self._publish(_bytes(dict(schema_version='p6.checkpoint_anchor_binding.v1',
                run_id=self._manifest['run_id'],session_id=_hash(_bytes(self._manifest)),
                checkpoint_hash=self._checkpoint_context['checkpoint_hash'],
                checkpoint_commit_hash=self._checkpoint_context['commit_hash'],
                admission_hash=admission['commit_hash'],anchor_hash=result['last_anchor_hash'],
                package_hash=package_hash,validation_hash=validation_hash)))
            self._check_bound('CHECKPOINTED_LOCAL')
            self._lifecycle = 'CHECKPOINT_ANCHOR_BOUND_LOCAL'
            return dict(schema_version='p6.checkpoint_anchor_binding_result.v1',
                package_hash=package_hash,validation_hash=validation_hash,commit_hash=commit_hash,
                checkpoint_hash=self._checkpoint_context['checkpoint_hash'],anchor_hash=result['last_anchor_hash'],
                technical_verification='LOCAL_ONLY',runtime_accepted=False,
                independent_retention_verified=False,external_authority_verification='UPSTREAM_REQUIRED')
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
        if self._lifecycle not in ('OPEN', 'CHECKPOINTED_LOCAL', 'CHECKPOINT_ANCHOR_BOUND_LOCAL'):
            return
        expected = self._lifecycle
        self._busy = True
        try:
            self._writer.close(claim=self._claim)
            self._active(expected)
            inspect_objects(self._writer.root, header=self._writer.header, objects=dict(self._objects))
            self._active(expected)
            self._lifecycle = 'CLOSED'
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False
