import copy
import json
from pathlib import Path
import pytest
from app.research.registry_inspection import _hash,_canonical
from test_phase5_observed_pipeline import setup as base_setup,bind,run,holdout_states
from app.research.observed_pipeline import verify_observed_pipeline


def setup(root,delay=1,miss=.25,patches=None):
    c=base_setup(root);p=c[1];d=c[2]['timeframe_ms'];start=p['windows'][0]['train'][0]
    p.update(schema_version='wp5.observed_wf_policy.v3',window_policy=dict(mode='rolling',start_ms=start,train_span_ms=60*d,test_span_ms=60*d,step_ms=60*d,n_windows=2),
        regime_policy=dict(rule='TRAIN_CLOSE_RETURN_V1',down_threshold=-.01,up_threshold=.01))
    plan=c[3];plan.update(schema_version='wp5.observed_plan.v3',holdout_access_policy='AFTER_VALID_MC_RETAIN_DOWNGRADE')
    m=plan['mc_policy'];m.pop('block_length');m.update(parameter_grid=patches or [{},{'target':.25}],execution_delay_bars=delay,missed_fill_probability=miss,
        order_perturbation='SHUFFLE_EXPOSURE_BLOCKS',n_scenarios=2*len(patches or [{},{'target':.25}]))
    return bind(c)


@pytest.mark.parametrize('required_cpcv',[False,True])
def test_full_native_stress_grid_and_readonly_pipeline(tmp_path,required_cpcv):
    import importlib.util
    c=setup(tmp_path)
    if required_cpcv:
        pin=json.loads((Path(__file__).resolve().parents[1]/'docs/specs/phase5-cpcv-runtime-pin-macos-py314.json').read_text())
        c[3]['cpcv']=dict(cpcv_applicability='REQUIRED',applicability_justification='Synthetic full v3 pipeline validates actual split dependency',
            folds_policy=dict(n_folds=3,n_test_folds=2,purged_size=1,embargo_size=0,embargo_ms=0),pinned_lib=pin)
        bind(c)
    r=run(c)
    if required_cpcv and importlib.util.find_spec('skfolio') is None:
        assert r['pipeline_status']=='STOPPED' and r['stopped_at']=='CPCV' and r['reason_code']=='DEP_PIN_MISMATCH'
        assert holdout_states(c[4]['holdout_directory'])==[]
        return
    assert r['pipeline_status']=='COMPLETED',r
    assert r['schema_version']=='wp5.observed_pipeline_report.v3' and r['mc']['schema_version']=='wp5.observed_mc.v2'
    mc=r['mc'];assert mc['reported_scenario_count']==mc['generated_scenario_count']==4
    assert not mc['dropped_scenarios'] and mc['scenarios_summary']['metric_quantiles']
    assert r['statistics']['N_total']==2 and not r['promotion_authorized']
    assert verify_observed_pipeline(c[4]['evidence_directory'],expected_report_hash=r['report_hash'])['verification_status']=='VERIFIED'
    root=c[4]['evidence_directory'];native=json.loads((root/'holdout-execution'/'session'/'MANIFEST.json').read_text())['native_manifest']
    assert native['params']=={'target':.75}
    schedule=json.loads((root/'mc'/'SCHEDULE.json').read_text());assert [s['patch_index'] for s in schedule]==[0,0,1,1]
    for s in mc['scenarios']:
        for w in s['windows']:
            a=w['analytical'];assert sorted(a['return_indexes'])==list(range(59)) and a['timestamps'] is None
    assert 'MC_COST_STRESS_AND_BOOTSTRAP_ONLY' not in r['acceptance_limits']


def test_all_missed_downgrade_not_erased_by_holdout(tmp_path):
    c=setup(tmp_path,miss=1.);c[3]['mc_policy']['downgrade_rule']['min_total_return']=.001;bind(c);r=run(c)
    assert r['pipeline_status']=='COMPLETED',r
    assert r['readiness_effect']==r['mc']['readiness_effect']=='DOWNGRADED'
    assert all(s['metrics']['total_return']==0 for s in r['mc']['scenarios'])
    assert r['holdout']['holdout_status']=='HOLDOUT_PASSED' and not r['promotion_authorized']


@pytest.mark.parametrize('patch',[{'unknown':.2},{'target':1}])
def test_incompatible_parameter_patch_refuses_before_data(tmp_path,patch):
    c=setup(tmp_path,patches=[patch]);c[4]['read_bars']=lambda a,b:pytest.fail('data opened')
    with pytest.raises(ValueError,match='MC_PARAMETER_PATCH_INVALID'):run(c)
    assert not list(c[4]['evidence_directory'].iterdir())


@pytest.mark.parametrize('mutation',['omit','relabel','schedule'])
def test_stress_evidence_mutation_stops_before_holdout(tmp_path,monkeypatch,mutation):
    import app.research.observed_pipeline as module
    c=setup(tmp_path);original=module.run_observed_mc
    def bad(*a,**kw):
        r=original(*a,**kw);root=Path(kw['evidence_directory'])
        if mutation=='omit':(root/'SCENARIO-0000.json').unlink()
        else:
            p=root/('SCHEDULE.json' if mutation=='schedule' else 'SCENARIO-0000.json');v=json.loads(p.read_text())
            if mutation=='schedule':v[0]['windows'][0]['stress']['execution_delay_bars']+=1
            else:v['survives']=not v['survives']
            p.write_bytes(_canonical(v))
        return r
    monkeypatch.setattr(module,'run_observed_mc',bad);c[4]['read_holdout']=lambda:pytest.fail('holdout opened')
    r=run(c);assert r['pipeline_status']=='STOPPED' and r['stopped_at']=='HOLDOUT'
    assert holdout_states(c[4]['holdout_directory'])==[]


def test_no_perturbation_matches_baseline_returns(tmp_path):
    c=setup(tmp_path,delay=0,miss=0.,patches=[{}]);c[3]['mc_policy']['order_perturbation']='NONE';bind(c);r=run(c)
    assert r['pipeline_status']=='COMPLETED',r
    root=c[4]['evidence_directory']/'mc'
    for s in r['mc']['scenarios']:
        for i,w in enumerate(s['windows']):
            a=json.loads((root/f'baseline-{i:04d}'/'RESULT.json').read_text());b=json.loads((root/w['directory']/'RESULT.json').read_text())
            assert a['net_returns']==b['net_returns']


def test_incomplete_stress_scenarios_remain_in_denominator(tmp_path):
    from app.paper.session.risk import freeze_risk_limits
    c=setup(tmp_path,delay=0,miss=0.,patches=[{}]);c[3]['mc_policy']['profile_grid'][0]['taker_fee_bps']=1000.
    c[4]['risk_limits']=freeze_risk_limits(max_drawdown=.05,max_session_loss=.05,max_daily_loss=.05,max_loss_per_trade=.05)
    bind(c);r=run(c)
    assert r['pipeline_status']=='COMPLETED',r
    mc=r['mc'];assert mc['readiness_effect']=='DOWNGRADED' and mc['reported_scenario_count']==2
    assert all(not s['complete'] and not s['survives'] for s in mc['scenarios'])
    assert mc['scenarios_summary']['metric_quantiles'] is None and mc['scenarios_summary']['fraction_surviving']==0.
    assert mc['scenarios_summary']['incomplete_scenario_ids']==[0,1] and not mc['dropped_scenarios']
