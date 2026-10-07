"""Synthetic binding tests; no research data or frozen test imports."""
import copy
import hashlib
import json
import builtins
import io
import os
import socket
import sqlite3
import subprocess
import time

import pytest
from app.research.phase5_identity_contract import validate_experiment_contract
from app.research.contract_binding import check_contract_binding


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def request(coverage):
    day, start = 86400000, 1577836800000
    h = lambda tag: hashlib.sha256(('binding-synthetic:' + tag).encode()).hexdigest()
    dataset = dict(schema_version='wp5_0a1.dataset_identity.v3', dataset_hash=h('data'),
                   timeframe_ms=day, first_open_ms=start, last_open_ms=start+365*day,
                   dataset_end_exclusive_ms=start+366*day, rows=366, coverage_mode=coverage)
    if coverage == 'SESSION_CALENDAR':
        dataset.update(calendar_id='SYNTHETIC', calendar_hash=h('calendar'), rows=250)
    if coverage == 'SPARSE_DECLARED':
        dataset.update(coverage_manifest_hash=h('coverage'), rows=17)
    return dict(
        schema_version='wp5_0a1.request.v4',
        research_scope=dict(schema_version='wp5_0a1.research_scope.v1', **{
            key: h(key) for key in ['hypothesis_family_hash', 'strategy_family_hash',
                                   'asset_universe_hash', 'target_definition_hash', 'evaluation_objective_hash']}),
        research_program_registration=dict(schema_version='wp5_0a1.research_program_registration.v1',
            program_label='Synthetic binding test', owner='test', created_utc_ms=1700000000000),
        dataset_identity=dataset,
        static_timing_policy=dict(schema_version='wp5_0a1.static_timing_policy.v3',
            bar_duration_ms=day, settlement_lag_ms=0, feature_availability_rule='AT_BAR_CLOSE',
            signal_delay_ms=0, order_submission_latency_ms=0, order_submission_rule='AT_ORDER_ELIGIBILITY',
            fill_rule='FIRST_OBSERVED_VALID_BAR_OPEN_AT_OR_AFTER_ORDER_ELIGIBILITY'),
        boundaries=dict(schema_version='wp5_0a1.boundaries.v1',
            train=dict(start_ms=start, end_ms=start+200*day),
            val=dict(start_ms=start+201*day, end_ms=start+281*day),
            holdout=dict(start_ms=start+282*day, end_ms=start+366*day)),
        experiment_configuration=dict(schema_version='wp5_0a1.experiment_configuration.v1',
            strategy_id='SYNTHETIC-BINDING', param_hash=h('params'), method_hash=h('method')),
        preregistration=dict(schema_version='wp5_0a1.preregistration.v1', registered_utc_ms=1700000001000,
            registered_by='test', registration_reference='SYNTHETIC/1'),
        expected_identities={}, timing_ledger_schema_version='wp5_0.timing_ledger_row.v1')


@pytest.fixture
def record():
    result = validate_experiment_contract(request('CONTINUOUS_24_7'))
    assert result['contract_status'] == 'VALID', result
    return result


def args(record):
    return {k: record[k] for k in ['contract_hash', 'research_program_id', 'experiment_id']}


def seal(record):
    record['contract_hash'] = digest({k: v for k, v in record.items() if k != 'contract_hash'})


def expect(record, kwargs, reason=None):
    assert check_contract_binding(record, **kwargs) == dict(check_id='CONTRACT_BINDING',
        status='FAIL' if reason else 'PASS', reason_code=reason)


@pytest.mark.parametrize('coverage', ['CONTINUOUS_24_7', 'SESSION_CALENDAR', 'SPARSE_DECLARED'])
def test_real_public_contract_and_no_mutation(coverage):
    record = validate_experiment_contract(request(coverage))
    assert record['contract_status'] == 'VALID'
    before = copy.deepcopy(record)
    expect(record, args(record))
    expect(dict(reversed(list(record.items()))), args(record))
    expect(record, args(record))
    assert record == before
    if coverage != 'CONTINUOUS_24_7':
        assert any(c['status'] == 'NOT_APPLICABLE' for c in record['checks'])


@pytest.mark.parametrize('field', ['contract_hash', 'research_program_id', 'experiment_id'])
@pytest.mark.parametrize('bad', [None, True, 1, [], 'A'*64, 'a'*63, 'a'*65])
def test_invalid_arguments(record, field, bad):
    kw = args(record); kw[field] = bad
    expect(record, kw, 'CONTRACT_BINDING_INVALID')


@pytest.mark.parametrize('bad', [None, [], 'text', 3])
def test_not_dict(record, bad):
    expect(bad, args(record), 'CONTRACT_BINDING_INVALID')


@pytest.mark.parametrize('field,bad', [('schema_version', None), ('schema_version', 'v0'),
    ('contract_status', 'INVALID'), ('contract_hash', None), ('contract_hash', 'F'*64)])
def test_bad_contract_fields(record, field, bad):
    kw = args(record); record[field] = bad
    expect(record, kw, 'CONTRACT_BINDING_INVALID')


@pytest.mark.parametrize('field', ['schema_version', 'contract_status', 'contract_hash',
                                  'research_program_id', 'experiment_id'])
def test_missing_fields(record, field):
    kw = args(record); del record[field]
    if field != 'contract_hash':
        seal(record); kw['contract_hash'] = record['contract_hash']
    expect(record, kw, 'CONTRACT_BINDING_INVALID')


def test_tamper_and_request_hash(record):
    kw = args(record); kw['contract_hash'] = '0'*64
    expect(record, kw, 'CONTRACT_BINDING_INVALID')
    kw = args(record); record['limitations'][0] += 'changed'
    expect(record, kw, 'CONTRACT_BINDING_INVALID')


@pytest.mark.parametrize('field,reason', [('research_program_id', 'WRONG_RESEARCH_PROGRAM_ID'),
    ('experiment_id', 'CONTRACT_BINDING_INVALID'), ('timing_ledger_schema_version', 'TIMING_SCHEMA_UNSUPPORTED')])
def test_resealed_mismatch(record, field, reason):
    kw = args(record); record[field] = 'f'*64
    seal(record); kw['contract_hash'] = record['contract_hash']
    expect(record, kw, reason)


def test_precedence(record):
    kw = args(record); record['research_program_id'] = 'f'*64
    record['timing_ledger_schema_version'] = 'bad'
    expect(record, kw, 'CONTRACT_BINDING_INVALID')
    seal(record); kw['contract_hash'] = record['contract_hash']
    expect(record, kw, 'WRONG_RESEARCH_PROGRAM_ID')


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf'), object(), (1, 2), {1: 'x'}])
def test_non_json(record, bad):
    kw = args(record); record['extra'] = bad
    expect(record, kw, 'CONTRACT_BINDING_INVALID')


def test_cycle(record):
    kw = args(record); record['cycle'] = record
    expect(record, kw, 'CONTRACT_BINDING_INVALID')


def test_subclasses_rejected_without_callbacks(record):
    class Hostile(dict):
        def items(self):
            raise AssertionError('custom code executed')
    expect(Hostile(record), args(record), 'CONTRACT_BINDING_INVALID')
    kw = args(record); record['extra'] = Hostile()
    expect(record, kw, 'CONTRACT_BINDING_INVALID')


def test_no_io_or_clock(record, monkeypatch):
    kw = args(record)
    def forbidden(*a, **k):
        raise AssertionError('side effect attempted')
    with monkeypatch.context() as m:
        for obj, name in [(builtins, 'open'), (io, 'open'), (os, 'open'), (os, 'system'),
                          (sqlite3, 'connect'), (socket, 'socket'), (socket, 'create_connection'),
                          (subprocess, 'Popen'), (time, 'time'), (time, 'monotonic'), (os, 'urandom')]:
            m.setattr(obj, name, forbidden)
        expect(record, kw)
        bad = dict(record, contract_status='INVALID')
        expect(bad, kw, 'CONTRACT_BINDING_INVALID')
