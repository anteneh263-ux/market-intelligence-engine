import pytest
from app.research.statistics_report import evaluate_statistics_report
from app.research.aggregate_statistics import derive_aggregate,derivation_method_hash
from app.research.registry_inspection import _hash
from test_phase5_trial_registry_v2 import store,reservation_request
from test_phase5_bound_statistics import completed


def inputs(store,q):
    artifacts={};trial=completed(store,q,[-.2,.3,.1,-.1,.2]*10,artifacts)
    completed(store,q,[-1.,1.]*25,artifacts)
    return dict(research_program_id=q['research_program_id'],trial_id=trial,result_artifacts=artifacts)


def test_actual_forms_envelope_and_hash(store,reservation_request):
    args=inputs(store,reservation_request)
    a=evaluate_statistics_report(*map(str,store),**args,threshold_policy={'dsr_min':.95})
    pin=derivation_method_hash();bundle=derive_aggregate(*map(str,store),**args,expected_method_hash=pin)
    b=evaluate_statistics_report(*map(str,store),**args,threshold_policy={'dsr_min':.95},
        aggregate_bundle=bundle,expected_method_hash=pin)
    for report in (a,b):
        assert report['stats_status']=='VALID'
        assert report['stats_hash']==_hash({k:v for k,v in report.items() if k!='stats_hash'})
        assert report['independence_assumption_disclosure']
        assert [c['check_id'] for c in report['checks']]==['RETURN_SERIES','MOMENTS','TRIAL_COUNTER','N_DOMAIN',
            'SHARPE_VECTOR_BINDING','CROSS_TRIAL_VARIANCE','PSR_COMPUTE','DEFLATION_BENCHMARK','DSR_COMPUTE',
            'INDEPENDENCE_DISCLOSURE','THRESHOLD_POLICY','STATS_HASH']
        assert all(c['status']=='PASS' for c in report['checks'])
    assert a['dsr']==b['dsr'] and b['variance_convention']['source']=='FORM_B'
    assert a==evaluate_statistics_report(*map(str,store),**args,threshold_policy={'dsr_min':.95})


@pytest.mark.parametrize('policy',[{'dsr_min':.9},{'dsr_min':True},{'dsr_min':.95,'change_record':'unverified'},None])
def test_unapproved_threshold_policy_refuses(store,reservation_request,policy):
    r=evaluate_statistics_report(*map(str,store),**inputs(store,reservation_request),threshold_policy=policy)
    assert r['reason_code']=='THRESHOLD_POLICY_VIOLATION' and r['dsr'] is None
    assert r['decision_input']['dsr_pass'] is False


def test_missing_binding_and_mixed_forms_refuse(store,reservation_request):
    args=inputs(store,reservation_request)
    r=evaluate_statistics_report(*map(str,store),**args,threshold_policy={'dsr_min':.95},expected_method_hash='f'*64)
    assert r['stats_status']=='INVALID' and r['dsr'] is None
    assert r['checks'][4]['status']=='FAIL'
    args['result_artifacts']={}
    r=evaluate_statistics_report(*map(str,store),**args,threshold_policy={'dsr_min':.95})
    assert r['stats_status']=='INVALID' and r['checks'][0]['status']=='FAIL'


def test_n_one_reports_psr_but_later_checks_not_evaluated(store,reservation_request):
    artifacts={};trial=completed(store,reservation_request,[-1.,1.]*25,artifacts)
    r=evaluate_statistics_report(*map(str,store),research_program_id=reservation_request['research_program_id'],
        trial_id=trial,result_artifacts=artifacts,threshold_policy={'dsr_min':.95})
    assert r['reason_code']=='INSUFFICIENT_TRIALS_FOR_DSR' and r['psr']==.5 and r['dsr'] is None
    assert [c['status'] for c in r['checks']]==['PASS']*3+['FAIL']+['NOT_EVALUATED']*8


def test_zero_variance_fails_benchmark_and_precedes_bad_threshold(store,reservation_request):
    artifacts={};trial=completed(store,reservation_request,[-1.,1.]*25,artifacts)
    completed(store,reservation_request,[-1.,1.]*25,artifacts)
    r=evaluate_statistics_report(*map(str,store),research_program_id=reservation_request['research_program_id'],
        trial_id=trial,result_artifacts=artifacts,threshold_policy={'dsr_min':.1})
    assert r['reason_code']=='DEFLATION_BENCHMARK_INVALID' and r['dsr'] is None
    assert [c['status'] for c in r['checks']]==['PASS']*7+['FAIL']+['NOT_EVALUATED']*4


def test_missing_valid_result_fails_binding_after_counter(store,reservation_request):
    args=inputs(store,reservation_request);args['result_artifacts'].pop(next(reversed(args['result_artifacts'])))
    r=evaluate_statistics_report(*map(str,store),**args,threshold_policy={'dsr_min':.95})
    assert [c['status'] for c in r['checks']]==['PASS']*4+['FAIL']+['NOT_EVALUATED']*7
