"""Request-prefix behavior using entirely synthetic identities."""
import copy
import hashlib
import json
import builtins
import io
import os
import sqlite3
import socket
import subprocess
import time
import pytest
from app.research.request_preflight import check_request_preflight

MODES = ['RESERVATION', 'TIMING_VALIDATION', 'OUTCOME_APPEND', 'VERIFICATION_REPLAY', 'REGISTRY_STATE']
ASSIGNED = ['trial_id','reservation_sequence','reservation_utc_ms','N','N_total','row_hash',
            'previous_row_hash','verification_replay_id','outcome_sequence','outcome_utc_ms']


def request(mode):
    # Minimal self-sealed synthetic body: not claimed as producer authentication.
    body = dict(schema_version='wp5_0a1.contract.v4', contract_status='VALID',
                research_program_id='a'*64, experiment_id='b'*64,
                timing_ledger_schema_version='wp5_0.timing_ledger_row.v1')
    body['contract_hash'] = hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    value = dict(mode=mode, research_program_id='a'*64)
    if mode in MODES[:2]:
        value.update(experiment_id='b'*64,contract_hash=body['contract_hash'],contract_record=body)
    if mode == 'RESERVATION':
        value['attempt_fingerprint_record'] = dict(research_program_id='a'*64,experiment_id='b'*64,
            ordered_input_hashes=['c'*64],method_hash='d'*64,intended_dataset_zone='TRAIN')
    elif mode == 'TIMING_VALIDATION':
        value.update(trial_id='e'*64,timing_ledger_rows=[])
    elif mode == 'OUTCOME_APPEND':
        value.update(trial_id='e'*64,trial_outcome='CRASHED',result_hash='f'*64)
    elif mode == 'VERIFICATION_REPLAY':
        value.update(verification_replay_record=dict(replay_of_trial_id='e'*64,
            original_input_hashes=['c'*64],original_method_hash='d'*64,original_result_hash='f'*64),
            performance_data_accessed=False,append_reservation_requested=False)
    return value


def assert_result(value, failed=None, reason=None):
    result = check_request_preflight(value)
    checks = ['INPUT_MODE'] if failed == 'INPUT_MODE' else [
        'INPUT_MODE','FORBIDDEN_ASSIGNED_FIELDS','REQUEST_SCHEMA']
    if failed != 'INPUT_MODE' and value['mode'] in MODES[:2]:
        checks.append('CONTRACT_BINDING')
    statuses = []; stopped = False
    for c in checks:
        statuses.append('NOT_EVALUATED' if stopped else 'FAIL' if c == failed else 'PASS')
        if c == failed: stopped = True
    assert result == dict(checks=[dict(check_id=c,status=s) for c,s in zip(checks,statuses)],reason_code=reason)


@pytest.mark.parametrize('mode', MODES)
def test_valid_prefix_is_pure(mode):
    value = request(mode); before = copy.deepcopy(value)
    assert_result(value); assert_result(dict(reversed(list(value.items()))))
    assert value == before


@pytest.mark.parametrize('value', [None,[],{},dict(mode=[]),dict(mode=True),dict(mode='bad')])
def test_input_mode(value):
    assert_result(value,'INPUT_MODE','INPUT_MODE_INVALID')


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('field', ASSIGNED)
def test_assigned_fields_top_and_nested(mode, field):
    value = request(mode)
    if not (field == 'trial_id' and mode in ['TIMING_VALIDATION','OUTCOME_APPEND']):
        value[field] = 1
        assert_result(value,'FORBIDDEN_ASSIGNED_FIELDS','CALLER_SUPPLIED_RESERVATION_FIELD')
    value = request(mode); value['unknown'] = [{'nested': {field: 1}}]
    assert_result(value,'FORBIDDEN_ASSIGNED_FIELDS','CALLER_SUPPLIED_RESERVATION_FIELD')


@pytest.mark.parametrize('mode', MODES)
def test_missing_fields_and_unknowns(mode):
    for field in request(mode):
        if field == 'mode': continue
        value = request(mode); del value[field]
        assert_result(value,'REQUEST_SCHEMA','INPUT_SCHEMA_INVALID')
    value = request(mode); value['unknown'] = 1
    assert_result(value,'REQUEST_SCHEMA','INPUT_SCHEMA_INVALID')


@pytest.mark.parametrize('mode,foreign', [('RESERVATION','timing_ledger_rows'),
    ('TIMING_VALIDATION','attempt_fingerprint_record'),('OUTCOME_APPEND','experiment_id'),
    ('REGISTRY_STATE','contract_record'),('VERIFICATION_REPLAY','result_hash')])
def test_foreign_fields(mode, foreign):
    value = request(mode); value[foreign] = None
    assert_result(value,'REQUEST_SCHEMA','OUT_OF_MODE_FIELD')


def test_nested_contract_record_is_not_exempt():
    value = request('RESERVATION')
    value['attempt_fingerprint_record']['contract_record'] = {'N': 1}
    assert_result(value,'FORBIDDEN_ASSIGNED_FIELDS','CALLER_SUPPLIED_RESERVATION_FIELD')


def test_contract_record_delegated_and_scanner_exempt():
    value = request('RESERVATION'); value['contract_record']['N'] = 1
    assert_result(value,'CONTRACT_BINDING','CONTRACT_BINDING_INVALID')
    value['contract_record'] = None
    assert_result(value,'CONTRACT_BINDING','CONTRACT_BINDING_INVALID')


@pytest.mark.parametrize('bad', [None,True,12,'A'*64,'a'*63])
@pytest.mark.parametrize('field', ['research_program_id','experiment_id','contract_hash'])
def test_invalid_hex(field,bad):
    value=request('RESERVATION');value[field]=bad
    assert_result(value,'REQUEST_SCHEMA','INPUT_SCHEMA_INVALID')


def test_nested_schema_and_empty_fingerprint_deferred():
    for field,bad in [('method_hash','BAD'),('ordered_input_hashes',[True]),
                      ('intended_dataset_zone','TEST'),('trial_outcome','CRASHED')]:
        value=request('RESERVATION');value['attempt_fingerprint_record'][field]=bad
        assert_result(value,'REQUEST_SCHEMA','INPUT_SCHEMA_INVALID')
    value=request('RESERVATION');value['attempt_fingerprint_record']['ordered_input_hashes']=[]
    value['attempt_fingerprint_record']['research_program_id']='f'*64
    assert_result(value)


def test_timing_list_shape_only():
    for rows in [None,{},()]:
        value=request('TIMING_VALIDATION');value['timing_ledger_rows']=rows
        assert_result(value,'REQUEST_SCHEMA','INPUT_SCHEMA_INVALID')
    for rows in [[],[{}],[{'fill_ms':True}]]:
        value=request('TIMING_VALIDATION');value['timing_ledger_rows']=rows
        assert_result(value)


def test_replay_bool_type_vs_later_eligibility():
    for key in ['performance_data_accessed','append_reservation_requested']:
        for bad in [0,1,None,'false']:
            value=request('VERIFICATION_REPLAY');value[key]=bad
            assert_result(value,'REQUEST_SCHEMA','INPUT_SCHEMA_INVALID')
        value=request('VERIFICATION_REPLAY');value[key]=True
        assert_result(value)


def test_outcome_and_replay_nested_types():
    value=request('OUTCOME_APPEND');value['trial_outcome']='RESERVED'
    assert_result(value,'REQUEST_SCHEMA','INPUT_SCHEMA_INVALID')
    value=request('VERIFICATION_REPLAY');value['verification_replay_record']['original_input_hashes']=[3]
    assert_result(value,'REQUEST_SCHEMA','INPUT_SCHEMA_INVALID')


def test_cycles_objects_and_nonstring_keys():
    value=request('REGISTRY_STATE');value['extra']=value
    assert_result(value,'REQUEST_SCHEMA','INPUT_SCHEMA_INVALID')
    for bad in [object(),{2:'x'},(1,2),float('nan')]:
        value=request('REGISTRY_STATE');value['extra']=bad
        assert_result(value,'REQUEST_SCHEMA','INPUT_SCHEMA_INVALID')


def test_priority_assigned_before_schema_before_binding():
    value=request('RESERVATION');value['N']=1;value['contract_record']=None
    value['timing_ledger_rows']=[]
    assert_result(value,'FORBIDDEN_ASSIGNED_FIELDS','CALLER_SUPPLIED_RESERVATION_FIELD')
    del value['N']
    assert_result(value,'REQUEST_SCHEMA','OUT_OF_MODE_FIELD')
    del value['timing_ledger_rows']
    assert_result(value,'CONTRACT_BINDING','CONTRACT_BINDING_INVALID')


def test_no_io_clock_or_mutation(monkeypatch):
    values=[request(m) for m in MODES]
    def fail(*a,**kw): raise AssertionError('side effect')
    with monkeypatch.context() as m:
        for obj,key in [(builtins,'open'),(io,'open'),(os,'open'),(sqlite3,'connect'),
            (socket,'socket'),(subprocess,'Popen'),(time,'time'),(os,'urandom')]:
            m.setattr(obj,key,fail)
        for v in values: assert_result(v)


def test_assigned_nested_under_nonstring_key_precedes_schema_error():
    value = request('REGISTRY_STATE')
    value[1] = {'N': 1}
    assert_result(value, 'FORBIDDEN_ASSIGNED_FIELDS', 'CALLER_SUPPLIED_RESERVATION_FIELD')
