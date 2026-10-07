"""Synthetic attempt identity and prefix composition; no persistent trial creation."""
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
from app.research.attempt_fingerprint import check_attempt_fingerprint, check_trial_preflight


def record():
    return dict(research_program_id='a'*64,experiment_id='b'*64,
                ordered_input_hashes=['c'*64],method_hash='d'*64,intended_dataset_zone='TRAIN')


def request():
    body=dict(schema_version='wp5_0a1.contract.v4',contract_status='VALID',research_program_id='a'*64,
              experiment_id='b'*64,timing_ledger_schema_version='wp5_0.timing_ledger_row.v1')
    body['contract_hash']=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(mode='RESERVATION',research_program_id='a'*64,experiment_id='b'*64,
                contract_record=body,contract_hash=body['contract_hash'],attempt_fingerprint_record=record())


def check(r):
    return check_attempt_fingerprint(r,research_program_id='a'*64,experiment_id='b'*64)


def assert_bad(r):
    assert check(r)==dict(check_id='ATTEMPT_FINGERPRINT',status='FAIL',
                         reason_code='ATTEMPT_FINGERPRINT_INVALID',attempt_fingerprint=None)


def test_known_answer_and_stability():
    r=record();before=copy.deepcopy(r)
    # Independent literal canonical byte-vector, not the implementation's serializer.
    canonical=('{'+'"experiment_id":"'+'b'*64+'","intended_dataset_zone":"TRAIN",'
               +'"method_hash":"'+'d'*64+'","ordered_input_hashes":["'+'c'*64
               +'"],"research_program_id":"'+'a'*64+'"}').encode()
    expected=hashlib.sha256(canonical).hexdigest()
    assert check(r)==dict(check_id='ATTEMPT_FINGERPRINT',status='PASS',reason_code=None,attempt_fingerprint=expected)
    assert check(dict(reversed(list(r.items()))))==check(r)==check(r)
    assert r==before


@pytest.mark.parametrize('field', ['research_program_id','experiment_id','ordered_input_hashes',
                                    'method_hash','intended_dataset_zone'])
def test_every_field_significant(field):
    r=record();before=check(r)['attempt_fingerprint']
    value={'research_program_id':'e'*64,'experiment_id':'f'*64,
           'ordered_input_hashes':['e'*64],'method_hash':'e'*64,'intended_dataset_zone':'VAL'}[field]
    r[field]=value
    result=check_attempt_fingerprint(r,research_program_id=r['research_program_id'],experiment_id=r['experiment_id'])
    assert result['status']=='PASS' and result['attempt_fingerprint']!=before


def test_list_order_and_duplicate_significant():
    r=record();r['ordered_input_hashes']=['c'*64,'e'*64]
    a=check(r);r['ordered_input_hashes'].reverse();b=check(r)
    r['ordered_input_hashes'].append('c'*64);c=check(r)
    assert len({x['attempt_fingerprint'] for x in [a,b,c]})==3


@pytest.mark.parametrize('bad', [None,[],True,'x',{},dict(extra=1)])
def test_malformed_record(bad):assert_bad(bad)


@pytest.mark.parametrize('field',list(record()))
def test_missing_extra_fields(field):
    r=record();del r[field];assert_bad(r)
    r=record();r['extra']=1;assert_bad(r)


@pytest.mark.parametrize('field',['research_program_id','experiment_id','method_hash'])
@pytest.mark.parametrize('bad',[None,True,1,'A'*64,'a'*63,[]])
def test_malformed_id(field,bad):
    r=record();r[field]=bad;assert_bad(r)


@pytest.mark.parametrize('bad',[None,[],(),['C'*64],[True],['c'*63],[object()]])
def test_invalid_list(bad):
    r=record();r['ordered_input_hashes']=bad;assert_bad(r)


@pytest.mark.parametrize('bad',[None,True,1,[], 'test'])
def test_invalid_zone(bad):
    r=record();r['intended_dataset_zone']=bad;assert_bad(r)


@pytest.mark.parametrize('field',['research_program_id','experiment_id'])
def test_nested_mismatch(field):
    r=record();r[field]='f'*64;assert_bad(r)


@pytest.mark.parametrize('field',['research_program_id','experiment_id'])
@pytest.mark.parametrize('bad',[None,True,1,'A'*64,[]])
def test_invalid_external_identity(field,bad):
    kw=dict(research_program_id='a'*64,experiment_id='b'*64);kw[field]=bad
    result=check_attempt_fingerprint(record(),**kw)
    assert result['reason_code']=='ATTEMPT_FINGERPRINT_INVALID' and result['attempt_fingerprint'] is None


def test_custom_objects_not_called():
    class Bad(dict):
        def items(self):raise AssertionError('callback')
    assert_bad(Bad(record()))
    r=record();r['ordered_input_hashes']=[Bad()];assert_bad(r)


def test_composition_success_and_no_mutation():
    value=request();before=copy.deepcopy(value)
    result=check_trial_preflight(value)
    assert [c['check_id'] for c in result['checks']]==['INPUT_MODE','FORBIDDEN_ASSIGNED_FIELDS',
        'REQUEST_SCHEMA','CONTRACT_BINDING','ATTEMPT_FINGERPRINT']
    assert all(c['status']=='PASS' for c in result['checks'])
    assert result['attempt_fingerprint']==check(record())['attempt_fingerprint']
    assert result['reason_code'] is None and value==before


@pytest.mark.parametrize('mutation,reason,failed',[
    ('program','ATTEMPT_FINGERPRINT_INVALID','ATTEMPT_FINGERPRINT'),
    ('experiment','ATTEMPT_FINGERPRINT_INVALID','ATTEMPT_FINGERPRINT'),
    ('empty','ATTEMPT_FINGERPRINT_INVALID','ATTEMPT_FINGERPRINT'),
    ('element','INPUT_SCHEMA_INVALID','REQUEST_SCHEMA'),
    ('caller','INPUT_SCHEMA_INVALID','REQUEST_SCHEMA'),
    ('binding','CONTRACT_BINDING_INVALID','CONTRACT_BINDING'),
    ('assigned','CALLER_SUPPLIED_RESERVATION_FIELD','FORBIDDEN_ASSIGNED_FIELDS')])
def test_composed_failure_order(mutation,reason,failed):
    value=request();r=value['attempt_fingerprint_record']
    if mutation=='program':r['research_program_id']='f'*64
    if mutation=='experiment':r['experiment_id']='f'*64
    if mutation=='empty':r['ordered_input_hashes']=[]
    if mutation=='element':r['ordered_input_hashes']=[True]
    if mutation=='caller':value['attempt_fingerprint']='f'*64
    if mutation=='binding':value['contract_hash']='f'*64
    if mutation=='assigned':r['N']=1
    result=check_trial_preflight(value)
    assert result['reason_code']==reason and result['attempt_fingerprint'] is None
    names=['INPUT_MODE','FORBIDDEN_ASSIGNED_FIELDS','REQUEST_SCHEMA','CONTRACT_BINDING','ATTEMPT_FINGERPRINT']
    i=names.index(failed)
    assert result['checks']==[dict(check_id=n,status='PASS' if j<i else 'FAIL' if j==i else 'NOT_EVALUATED')
                              for j,n in enumerate(names)]


def test_nonreservation_and_bad_mode_no_fingerprint():
    for value in [None,{},dict(mode='unknown'),dict(mode='REGISTRY_STATE',research_program_id='a'*64)]:
        result=check_trial_preflight(value)
        assert result['attempt_fingerprint'] is None
        assert all(c['check_id']!='ATTEMPT_FINGERPRINT' for c in result['checks'])


def test_no_side_effects(monkeypatch):
    value=request()
    def fail(*a,**kw):raise AssertionError('side effect')
    with monkeypatch.context() as m:
        for obj,name in [(builtins,'open'),(io,'open'),(os,'open'),(sqlite3,'connect'),
            (socket,'socket'),(subprocess,'Popen'),(time,'time'),(os,'urandom')]:m.setattr(obj,name,fail)
        assert check_trial_preflight(value)['reason_code'] is None
