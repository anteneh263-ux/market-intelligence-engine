import copy
import pytest
from app.research.bound_statistics import evaluate_trial_statistics
from app.research.trial_registry_v2 import execute_trial
from test_phase5_trial_registry_v2 import store,reservation_request,digest


def completed(store,q,returns,artifacts):
    r=execute_trial(q,*map(str,store));trial=r['result']['trial_id']
    artifact=dict(schema_version='wp5_3.trial_result.v1',research_program_id=q['research_program_id'],
                  experiment_id=q['experiment_id'],trial_id=trial,net_returns=returns)
    h=digest(artifact);artifacts[h]=artifact
    out=execute_trial(dict(mode='OUTCOME_APPEND',research_program_id=q['research_program_id'],trial_id=trial,
                          trial_outcome='COMPLETED_VALID',result_hash=h),*map(str,store))
    assert out['counter_status']=='VERIFIED'
    return trial


def evaluate(store,q,trial,artifacts):
    return evaluate_trial_statistics(*map(str,store),research_program_id=q['research_program_id'],
                                     trial_id=trial,result_artifacts=artifacts)


def test_derived_vector_is_bound_to_every_completed_trial(store,reservation_request):
    q=reservation_request;artifacts={}
    t=completed(store,q,[-.2,.3,.1,-.1,.2]*10,artifacts)
    second=completed(store,q,[-1.,1.]*25,artifacts)
    before=copy.deepcopy(artifacts);r=evaluate(store,q,t,artifacts)
    assert r['binding_status']=='VERIFIED' and r['statistics']['N_total']==2
    assert r['trial_ids']==[t,second] and r['statistics']['K']==2
    assert r['statistics']['ledger_binding']=='VERIFIED'
    assert r['report_hash']==digest({k:v for k,v in r.items() if k!='report_hash'})
    assert artifacts==before and evaluate(store,q,t,artifacts)==r


def test_missing_artifact_cannot_shrink_trials(store,reservation_request):
    q=reservation_request;artifacts={}
    t=completed(store,q,[-.2,.3,.1,-.1,.2]*10,artifacts)
    completed(store,q,[-1.,1.]*25,artifacts);artifacts.pop(next(reversed(artifacts)))
    r=evaluate(store,q,t,artifacts)
    assert r['binding_status']=='REFUSED' and r['statistics'] is None


def test_tampered_returns_fail_result_hash(store,reservation_request):
    q=reservation_request;artifacts={};t=completed(store,q,[-1.,1.]*25,artifacts)
    next(iter(artifacts.values()))['net_returns'][0]=123.
    assert evaluate(store,q,t,artifacts)['binding_status']=='REFUSED'


def test_reserved_and_crashed_trials_remain_in_n(store,reservation_request):
    q=reservation_request;artifacts={}
    t=completed(store,q,[-.2,.3,.1,-.1,.2]*10,artifacts)
    completed(store,q,[-1.,1.]*25,artifacts)
    third=execute_trial(q,*map(str,store))['result']['trial_id']
    execute_trial(dict(mode='OUTCOME_APPEND',research_program_id=q['research_program_id'],trial_id=third,
        trial_outcome='CRASHED',result_hash='f'*64),*map(str,store))
    execute_trial(q,*map(str,store))
    r=evaluate(store,q,t,artifacts)
    assert r['statistics']['N_total']==4 and r['statistics']['K']==2


def test_single_trial_remains_statistical_failure(store,reservation_request):
    artifacts={};t=completed(store,reservation_request,[-1.,1.]*25,artifacts)
    r=evaluate(store,reservation_request,t,artifacts)
    assert r['binding_status']=='VERIFIED' and r['statistics']['dsr'] is None
    assert not r['statistics']['decision_input']['dsr_pass']


def test_unknown_trial_refused(store,reservation_request):
    assert evaluate(store,reservation_request,'f'*64,{})['binding_status']=='REFUSED'


def test_context_exit_failure_clears_verified_payload(store,reservation_request,monkeypatch):
    from contextlib import contextmanager
    import app.research.bound_statistics as module
    artifacts={};t=completed(store,reservation_request,[-1.,1.]*25,artifacts)
    real=module._open
    @contextmanager
    def failed_exit(*args):
        with real(*args) as opened:yield opened
        raise OSError('context close failure')
    monkeypatch.setattr(module,'_open',failed_exit)
    r=evaluate(store,reservation_request,t,artifacts)
    assert r['binding_status']=='REFUSED'
    assert all(r[k] is None for k in ('statistics','trial_ids','result_hashes','ledger_snapshot_hash'))


def test_identity_checked_on_the_same_snapshot_as_result_hash(store,reservation_request,monkeypatch):
    import app.research.bound_statistics as module
    q=reservation_request;t=execute_trial(q,*map(str,store))['result']['trial_id']
    invalid=dict(schema_version='wp5_3.trial_result.v1',research_program_id=q['research_program_id'],
        experiment_id=q['experiment_id'],trial_id='f'*64,net_returns=[-1.,1.]*25)
    h=digest(invalid)
    execute_trial(dict(mode='OUTCOME_APPEND',research_program_id=q['research_program_id'],trial_id=t,
        trial_outcome='COMPLETED_VALID',result_hash=h),*map(str,store))
    supplied=dict(invalid,trial_id=t);real=module.base._canonical
    def mutate_before_snapshot(value):
        if value is supplied:value['trial_id']='f'*64
        return real(value)
    monkeypatch.setattr(module.base,'_canonical',mutate_before_snapshot)
    assert evaluate(store,q,t,{h:supplied})['binding_status']=='REFUSED'
