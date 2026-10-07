import copy
import pytest
from app.research.aggregate_statistics import derive_aggregate,evaluate_aggregate_statistics,derivation_method_hash
from app.research.bound_statistics import evaluate_trial_statistics
from app.research.trial_registry_v2 import execute_trial
from test_phase5_trial_registry_v2 import store,reservation_request
from test_phase5_bound_statistics import completed


def case(store,q):
    artifacts={};trial=completed(store,q,[-.2,.3,.1,-.1,.2]*10,artifacts)
    completed(store,q,[-1.,1.]*25,artifacts)
    args=dict(research_program_id=q['research_program_id'],trial_id=trial,result_artifacts=artifacts,
        expected_method_hash=derivation_method_hash())
    bundle=derive_aggregate(*map(str,store),**args)
    return bundle,args


def test_actual_ledger_form_a_b_parity_and_replay(store,reservation_request):
    bundle,args=case(store,reservation_request);before=copy.deepcopy(bundle)
    a=evaluate_trial_statistics(*map(str,store),**{k:v for k,v in args.items() if k!='expected_method_hash'})
    b=evaluate_aggregate_statistics(bundle,*map(str,store),**args)
    assert b['binding_status']=='VERIFIED'
    for key in ('dsr','psr','sr_star0','var_sr_trials_recomputed','N_total','N_effective','K','decision_input'):
        assert b['statistics'][key]==a['statistics'][key]
    assert b['statistics']['variance_convention']['source']=='FORM_B'
    assert bundle==before and b==evaluate_aggregate_statistics(bundle,*map(str,store),**args)


@pytest.mark.parametrize('field,value,reason',[
    ('var_sr_trials',999.,'VAR_SR_TRIALS_MISMATCH'),('K',99,'SHARPE_VECTOR_LENGTH_MISMATCH'),
    ('K',True,'SHARPE_VECTOR_LENGTH_MISMATCH'),('trial_ids_hash','f'*64,'TRIAL_VECTOR_BINDING_INVALID'),
    ('ledger_snapshot_hash','f'*64,'TRIAL_VECTOR_BINDING_INVALID'),
    ('verifier_record_hash','f'*64,'TRIAL_VECTOR_BINDING_INVALID'),
    ('derivation_method_hash','f'*64,'TRIAL_VECTOR_BINDING_INVALID')])
def test_tampered_aggregate_refuses(store,reservation_request,field,value,reason):
    bundle,args=case(store,reservation_request);bundle['sharpe_aggregate'][field]=value
    report=evaluate_aggregate_statistics(bundle,*map(str,store),**args)
    assert report['binding_status']=='REFUSED' and report['reason_code']==reason and report['statistics'] is None


def test_wrong_runtime_pin_and_forged_record_refuse(store,reservation_request):
    bundle,args=case(store,reservation_request)
    assert evaluate_aggregate_statistics(bundle,*map(str,store),**dict(args,expected_method_hash='f'*64))['reason_code']=='DERIVATION_METHOD_MISMATCH'
    bundle['verifier_record']['verification']='TRUST_ME'
    assert evaluate_aggregate_statistics(bundle,*map(str,store),**args)['binding_status']=='REFUSED'


def test_new_reservation_invalidates_old_prefix_and_remains_counted(store,reservation_request):
    bundle,args=case(store,reservation_request)
    execute_trial(reservation_request,*map(str,store))
    assert evaluate_aggregate_statistics(bundle,*map(str,store),**args)['binding_status']=='REFUSED'
    updated=derive_aggregate(*map(str,store),**args)
    report=evaluate_aggregate_statistics(updated,*map(str,store),**args)
    assert report['statistics']['N_total']==3 and report['statistics']['K']==2


def test_missing_result_and_single_trial_refuse(store,reservation_request):
    artifacts={};trial=completed(store,reservation_request,[-.2,.3,.1,-.1,.2]*10,artifacts)
    args=dict(research_program_id=reservation_request['research_program_id'],trial_id=trial,
        result_artifacts=artifacts,expected_method_hash=derivation_method_hash())
    with pytest.raises(ValueError,match='INSUFFICIENT_TRIALS_FOR_DSR'):derive_aggregate(*map(str,store),**args)
    completed(store,reservation_request,[-1.,1.]*25,artifacts)
    bundle=derive_aggregate(*map(str,store),**args);artifacts.pop(next(reversed(artifacts)))
    assert evaluate_aggregate_statistics(bundle,*map(str,store),**args)['binding_status']=='REFUSED'


def test_variance_within_tolerance_is_never_used(store,reservation_request):
    bundle,args=case(store,reservation_request)
    original=evaluate_aggregate_statistics(bundle,*map(str,store),**args)
    bundle['sharpe_aggregate']['var_sr_trials']+=5e-13
    assert evaluate_aggregate_statistics(bundle,*map(str,store),**args)['statistics']==original['statistics']
    bundle['sharpe_aggregate']['var_sr_trials']=float('inf')
    assert evaluate_aggregate_statistics(bundle,*map(str,store),**args)['binding_status']=='REFUSED'
