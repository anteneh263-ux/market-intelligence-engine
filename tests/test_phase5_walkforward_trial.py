import copy
import hashlib
import json

import pytest

from app.research.phase5_identity_contract import validate_experiment_contract
from app.research.trial_registry_v2 import execute_trial, initialize_trial_registry
from app.research.walkforward_trial import (run_walkforward_trial,
    walkforward_method_hash, walkforward_selector_hash)
from test_phase5_contract_binding import request as identity_request
from test_phase5_walkforward import policy


def digest(x):
    return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


@pytest.fixture
def setup(tmp_path):
    root=tmp_path.resolve(); evidence=root/'evidence';wf=root/'wf';evidence.mkdir();wf.mkdir()
    registry=root/'registry';anchor=root/'anchor'
    initialize_trial_registry(str(registry),str(anchor))
    req=identity_request('CONTINUOUS_24_7');start=req['boundaries']['train']['start_ms'];day=86400000
    p=policy(start_ms=start,end_ms=start+200*day,train_span_ms=40*day,test_span_ms=40*day,step_ms=40*day,
        dataset_id=req['dataset_identity']['dataset_hash'],strategy_id=req['experiment_configuration']['strategy_id'],timeframe='1d')
    req['experiment_configuration']['method_hash']=walkforward_method_hash(p)
    req['experiment_configuration']['param_hash']=walkforward_selector_hash(p)
    c=validate_experiment_contract(req);assert c['contract_status']=='VALID'
    p['experiment_id']=c['experiment_id']
    def reader(a,b):
        return [dict(sample_id=str(t),open_time=t,close_time=t+day,close=100+((t-start)//day)%7,
            label_start_ms=t+day,label_end_ms=t+2*day) for t in range(a,b,day)]
    ranges={(start+i*40*day,start+(i+1)*40*day) for i in range(5)}
    manifest=dict(schema_version='wp5_2.segments.v1',segments=[dict(start_ms=a,end_ms=b,rows_hash=digest(reader(a,b))) for a,b in sorted(ranges)])
    q=dict(mode='RESERVATION',research_program_id=c['research_program_id'],experiment_id=c['experiment_id'],
        contract_hash=c['contract_hash'],contract_record=c,attempt_fingerprint_record=dict(
            research_program_id=c['research_program_id'],experiment_id=c['experiment_id'],
            ordered_input_hashes=[digest(manifest)],method_hash=walkforward_method_hash(p),intended_dataset_zone='TRAIN'))
    args=dict(registry_path=str(registry),anchor_path=str(anchor),evidence_directory=evidence,walkforward_directory=wf,
        read_bars=reader,select_parameters=lambda rows:{'position':1},signal=lambda history,params:params['position'],
        regime=lambda history:'synthetic',prior_result_artifacts={})
    return q,p,manifest,args


def run(setup):
    q,p,m,a=setup
    return run_walkforward_trial(q,p,m,**a)


def state(setup):
    q,p,m,a=setup
    return execute_trial(dict(mode='REGISTRY_STATE',research_program_id=q['research_program_id']),a['registry_path'],a['anchor_path'])


def test_reservation_precedes_reader_and_complete_result_is_bound(setup):
    q,p,m,a=setup;reader=a['read_bars']
    def checked(start,end):
        assert state(setup)['N_total']==1
        receipt=json.loads((a['evidence_directory']/'RESERVATION.json').read_text())
        assert receipt['counter_status']=='COMMITTED'
        return reader(start,end)
    a['read_bars']=checked
    report=run(setup)
    assert report['lifecycle_status']=='COMPLETED_VALID'
    assert report['statistics']['binding_status']=='VERIFIED'
    assert report['statistics']['statistics']['N_total']==1
    assert not report['statistics']['statistics']['decision_input']['dsr_pass']
    artifact=json.loads((a['evidence_directory']/'TRIAL_RESULT.json').read_text())
    assert report['result_hash']==digest(artifact)
    assert artifact['trial_id']==report['trial_id']
    assert state(setup)['result']['outcome_count']==1


@pytest.mark.parametrize('what',['method','policy','manifest','zone','experiment','parameters'])
def test_binding_refusal_precedes_reservation_and_data(setup,what):
    q,p,m,a=setup
    if what=='method':q['attempt_fingerprint_record']['method_hash']='f'*64
    if what=='policy':p['cost_bps']=99
    if what=='manifest':m['segments'][0]['rows_hash']='f'*64
    if what=='zone':q['attempt_fingerprint_record']['intended_dataset_zone']='HOLDOUT'
    if what=='experiment':p['experiment_id']='f'*64
    if what=='parameters':q['contract_record']['experiment_configuration']['param_hash']='f'*64
    a['read_bars']=lambda *args:pytest.fail('unexpected access')
    with pytest.raises(ValueError):run(setup)
    assert state(setup)['N_total']==0
    assert not list(a['evidence_directory'].iterdir())


def test_segment_tampering_records_invalid_and_no_statistics(setup):
    q,p,m,a=setup;reader=a['read_bars']
    def changed(start,end):
        rows=reader(start,end);rows[0]['close']+=1;return rows
    a['read_bars']=changed
    report=run(setup)
    assert report['lifecycle_status']=='COMPLETED_INVALID'
    assert report['statistics'] is None
    assert state(setup)['N_total']==1 and state(setup)['result']['outcome_count']==1
    assert not (a['walkforward_directory']/'open-0.json').exists()


def test_existing_evidence_never_reserves_again(setup):
    run(setup)
    with pytest.raises(FileExistsError):run(setup)
    assert state(setup)['N_total']==1


def test_receipt_failure_preserves_reservation_without_data(setup,monkeypatch):
    import app.research.walkforward_trial as module
    real=module._Store.put
    def fail(self,name,value):
        if name=='RESERVATION.json':raise OSError('injected receipt failure')
        return real(self,name,value)
    monkeypatch.setattr(module._Store,'put',fail)
    setup[3]['read_bars']=lambda *args:pytest.fail('unexpected access')
    with pytest.raises(OSError):run(setup)
    assert state(setup)['N_total']==1
    assert state(setup)['result']['outcome_count']==0


def test_failed_outcome_append_preserves_result_for_reconciliation(setup,monkeypatch):
    import app.research.walkforward_trial as module
    real=module.execute_trial
    def fail(request,*args):
        if request['mode']=='OUTCOME_APPEND':raise OSError('injected outcome failure')
        return real(request,*args)
    monkeypatch.setattr(module,'execute_trial',fail)
    with pytest.raises(OSError):run(setup)
    assert (setup[3]['evidence_directory']/'TRIAL_RESULT.json').is_file()
    assert state(setup)['N_total']==1
    assert state(setup)['result']['outcome_count']==0


def test_flat_result_does_not_poison_completed_valid_vector(setup):
    q,p,m,a=setup;reader=a['read_bars']
    original_q,original_m=copy.deepcopy(q),copy.deepcopy(m)
    def flat(start,end):
        rows=reader(start,end)
        for row in rows:row['close']=100
        return rows
    # Explicitly commit the flat fixture's segment bytes before the run.
    for segment in m['segments']:
        segment['rows_hash']=digest(flat(segment['start_ms'],segment['end_ms']))
    q['attempt_fingerprint_record']['ordered_input_hashes']=[digest(m)]
    a['read_bars']=flat;a['signal']=lambda *args:0
    report=run(setup)
    assert report['lifecycle_status']=='COMPLETED_INVALID'
    assert report['completion_reason']=='MOMENTS_INVALID'
    assert state(setup)['N_total']==1
    q.clear();q.update(original_q);m.clear();m.update(original_m)
    root=a['evidence_directory'].parent
    a['evidence_directory']=root/'after-flat';a['walkforward_directory']=root/'after-flat-wf'
    a['evidence_directory'].mkdir();a['walkforward_directory'].mkdir()
    a['read_bars']=reader;a['signal']=lambda *args:1
    later=run(setup)
    assert later['statistics']['binding_status']=='VERIFIED'
    assert later['statistics']['statistics']['N_total']==2
    assert later['statistics']['statistics']['K']==1


@pytest.mark.parametrize('supply_prior',[True,False])
def test_two_real_runs_use_complete_trial_vector(setup,supply_prior):
    first=run(setup)
    q,p,m,a=setup
    artifact=json.loads((a['evidence_directory']/'TRIAL_RESULT.json').read_text())
    root=a['evidence_directory'].parent
    a['evidence_directory']=root/'second';a['walkforward_directory']=root/'second-wf'
    a['evidence_directory'].mkdir();a['walkforward_directory'].mkdir()
    if supply_prior:a['prior_result_artifacts']={first['result_hash']:artifact}
    report=run(setup)
    assert report['lifecycle_status']=='COMPLETED_VALID'
    assert state(setup)['N_total']==2
    if supply_prior:
        assert report['statistics']['binding_status']=='VERIFIED'
        assert report['statistics']['statistics']['N_total']==2
        assert report['statistics']['statistics']['K']==2
    else:
        assert report['statistics']['binding_status']=='REFUSED'
        assert report['statistics']['statistics'] is None


def test_process_crash_after_reservation_preserves_count_and_prevents_resume(setup):
    import os
    import subprocess
    import sys
    q,p,m,a=setup
    payload=a['evidence_directory'].parent/'input.json'
    payload.write_text(json.dumps(dict(q=q,p=p,m=m,paths={k:str(a[k]) for k in
        ('registry_path','anchor_path','evidence_directory','walkforward_directory')})))
    script='''
import json,os,sys
from app.research.walkforward_trial import run_walkforward_trial
x=json.load(open(sys.argv[1]))
def stop(*args):os._exit(52)
run_walkforward_trial(x['q'],x['p'],x['m'],**x['paths'],read_bars=stop,
    select_parameters=lambda rows:{},signal=lambda *args:0,regime=lambda *args:'synthetic',prior_result_artifacts={})
'''
    process=subprocess.run([sys.executable,'-c',script,str(payload)],env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),timeout=20)
    assert process.returncode==52
    assert state(setup)['N_total']==1 and state(setup)['result']['outcome_count']==0
    assert (a['evidence_directory']/'RESERVATION.json').exists()
    assert not (a['evidence_directory']/'RESULT.json').exists()
    with pytest.raises(FileExistsError):run(setup)
    assert state(setup)['N_total']==1


def test_short_result_is_counted_but_not_completed_valid(setup):
    q,p,m,a=setup;day=86400000;start=p['start_ms']
    p.update(end_ms=start+60*day,test_span_ms=10*day,step_ms=10*day)
    req=identity_request('CONTINUOUS_24_7')
    req['experiment_configuration'].update(method_hash=walkforward_method_hash(p),param_hash=walkforward_selector_hash(p))
    c=validate_experiment_contract(req);assert c['contract_status']=='VALID'
    p['experiment_id']=c['experiment_id']
    q.update(experiment_id=c['experiment_id'],contract_hash=c['contract_hash'],contract_record=c)
    ranges=[(start,start+40*day),(start+10*day,start+50*day),
            (start+40*day,start+50*day),(start+50*day,start+60*day)]
    m['segments']=[dict(start_ms=x,end_ms=y,rows_hash=digest(a['read_bars'](x,y))) for x,y in ranges]
    q['attempt_fingerprint_record'].update(experiment_id=c['experiment_id'],method_hash=walkforward_method_hash(p),
        ordered_input_hashes=[digest(m)])
    report=run(setup)
    assert report['lifecycle_status']=='COMPLETED_INVALID'
    assert report['completion_reason']=='INSUFFICIENT_SAMPLE'
    assert state(setup)['N_total']==1
    assert len(json.loads((a['evidence_directory']/'TRIAL_RESULT.json').read_text())['net_returns'])==20
