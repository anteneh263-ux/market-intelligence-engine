import copy
import json
import importlib.util
from pathlib import Path
import pytest
from app.research.phase5_pipeline import run_phase5_pipeline
from app.research.phase5_identity_contract import validate_experiment_contract
from app.research.trial_registry_v2 import initialize_trial_registry,execute_trial
from app.research.holdout import initialize_holdout_store,verify_holdout_report
from app.research.walkforward_trial import walkforward_method_hash,walkforward_selector_hash
from app.research.registry_inspection import _hash,_canonical
from test_phase5_contract_binding import request as identity_request
from test_phase5_validation_plan import plan as validation_plan
from test_phase5_walkforward import policy as wf_policy
from test_phase5_rag_provenance import corpus
from test_phase5_bound_statistics import completed


def setup(root,*,prior=True,stress_cost=0,cpcv_required=False):
    root=root.resolve();out=root/'run';out.mkdir();holdout=root/'holdout';holdout.mkdir();initialize_holdout_store(holdout)
    registry=root/'registry';anchor=root/'anchor';initialize_trial_registry(str(registry),str(anchor))
    raw=identity_request('CONTINUOUS_24_7');start=raw['dataset_identity']['first_open_ms'];day=86400000
    all_rows=[dict(sample_id=str(i),open_time=start+i*day,close_time=start+(i+1)*day,
        close=100*(1.001**i)*(1.00005**(i%3)),label_start_ms=start+(i+1)*day,label_end_ms=start+(i+2)*day) for i in range(366)]
    def reader(a,b):return copy.deepcopy([r for r in all_rows if a<=r['open_time']<b])
    holdout_rows=reader(raw['boundaries']['holdout']['start_ms'],raw['boundaries']['holdout']['end_ms'])
    manifest=dict(schema_version='wp5.dataset_manifest.v1',timeframe_ms=day,holdout_dataset_hash=_hash(holdout_rows),
        rows=[dict(sample_id=r['sample_id'],open_time=r['open_time'],close_time=r['close_time'],feature_time_ms=r['close_time'],
                   label_start_ms=r['label_start_ms'],label_end_ms=r['label_end_ms'],row_hash=_hash(r)) for r in all_rows])
    raw['dataset_identity']['dataset_hash']=_hash(manifest)
    segments=dict(schema_version='wp5_2.segments.v1',segments=[dict(start_ms=start+i*40*day,end_ms=start+(i+1)*40*day,
        rows_hash=_hash(reader(start+i*40*day,start+(i+1)*40*day))) for i in range(5)])
    plan=validation_plan();plan['strategy_id']=raw['experiment_configuration']['strategy_id'];plan['dataset_manifest_hash']=_hash(segments)
    sources=corpus();plan['rag_policy']['corpus_hash']=_hash(sources)
    plan['holdout_policy'].update(dataset_hash=_hash(holdout_rows),parameter_source='LAST_WF_TRAIN',warmup_policy='NONE')
    plan['mc_policy']['cost_slippage_grid']=[dict(cost_bps=stress_cost,slippage_bps=0)]
    if cpcv_required:
        plan['cpcv'].update(cpcv_applicability='REQUIRED',
            folds_policy=dict(n_folds=3,n_test_folds=2,purged_size=1,embargo_size=0,embargo_ms=0),
            pinned_lib=json.loads((Path(__file__).resolve().parents[1]/'docs/specs/phase5-cpcv-runtime-pin-macos-py314.json').read_text()))
    p=wf_policy(start_ms=start,end_ms=start+200*day,train_span_ms=40*day,test_span_ms=40*day,step_ms=40*day,
        dataset_id=_hash(manifest),strategy_id=plan['strategy_id'],cost_bps=0,slippage_bps=0,timeframe='1d',validation_plan_hash=_hash(plan))
    raw['experiment_configuration'].update(method_hash=walkforward_method_hash(p),param_hash=walkforward_selector_hash(p))
    c=validate_experiment_contract(raw);assert c['contract_status']=='VALID'
    p['experiment_id']=c['experiment_id']
    def reservation(contract,method):
        return dict(mode='RESERVATION',research_program_id=contract['research_program_id'],experiment_id=contract['experiment_id'],
            contract_hash=contract['contract_hash'],contract_record=contract,attempt_fingerprint_record=dict(
                research_program_id=contract['research_program_id'],experiment_id=contract['experiment_id'],
                method_hash=method,ordered_input_hashes=[_hash(segments)],intended_dataset_zone='TRAIN'))
    q=reservation(c,walkforward_method_hash(p));artifacts={}
    if prior:
        older=copy.deepcopy(raw);older['experiment_configuration'].update(method_hash=_hash('prior-method'),param_hash=_hash('prior-parameters'))
        old=validate_experiment_contract(older)
        assert old['contract_status']=='VALID',old
        completed((registry,anchor),reservation(old,_hash('prior-method')),[-.01,.01]*80,artifacts)
    args=dict(registry_path=str(registry),anchor_path=str(anchor),evidence_directory=out,holdout_directory=holdout,
        read_bars=reader,select_parameters=lambda rows:{'direction':1},signal=lambda history,params:params['direction'],
        regime=lambda history:'synthetic',read_holdout=lambda:copy.deepcopy(holdout_rows),prior_result_artifacts=artifacts,
        corpus=sources,advisory_query='transaction costs',cpcv_sample_index=[],actor='synthetic-pipeline')
    if cpcv_required:
        args['cpcv_sample_index']=[{k:r[k] for k in ('sample_id','feature_time_ms','label_start_ms','label_end_ms')}
            for r in manifest['rows'] if start<=r['open_time']<p['end_ms'] and r['label_end_ms']<=p['end_ms']]
    return q,p,segments,plan,manifest,args


def run(case):return run_phase5_pipeline(*case[:5],**case[5])


def test_complete_actual_chain_and_ordering(tmp_path):
    case=setup(tmp_path);args=case[5];reader=args['read_bars'];holdout_reader=args['read_holdout'];reads=[]
    def guarded(a,b):
        assert (args['evidence_directory']/'plan'/'VALIDATION_PLAN.json').exists()
        state=execute_trial(dict(mode='REGISTRY_STATE',research_program_id=case[0]['research_program_id']),args['registry_path'],args['anchor_path'])
        assert state['N_total']==2
        return reader(a,b)
    def held():
        reads.append(1)
        assert (args['evidence_directory']/'MONTE_CARLO.json').exists()
        assert (args['evidence_directory']/'PREHOLDOUT_VERIFICATION.json').exists()
        return holdout_reader()
    args.update(read_bars=guarded,read_holdout=held)
    result=run(case)
    assert result['pipeline_status']=='COMPLETED',result
    assert result['holdout']['holdout_status']=='HOLDOUT_PASSED'
    assert result['statistics']['stats_status']=='VALID'
    assert result['statistics']['stats_hash']==_hash({k:v for k,v in result['statistics'].items() if k!='stats_hash'})
    assert result['phase5_acceptance_status']=='INCOMPLETE'
    assert result['trial']['statistics']['statistics']['decision_input']['dsr_pass'] is True
    assert result['mc']['readiness_effect']=='PRESERVED'
    assert result['advisory']['advisory'] is True and result['promotion_requires_human'] is True
    assert reads==[1]
    assert verify_holdout_report(args['holdout_directory'],result['holdout']['reservation_key'])==result['holdout']
    assert reads==[1]


def test_single_trial_cannot_reach_holdout(tmp_path):
    case=setup(tmp_path,prior=False);case[5]['read_holdout']=lambda:pytest.fail('holdout read')
    result=run(case)
    assert result['pipeline_status']=='STOPPED' and result['stopped_at']=='STATISTICS'
    assert result['holdout'] is None and not list(case[5]['holdout_directory'].glob('row-*'))


def test_mc_downgrade_is_recorded_not_upgraded_or_silently_skipped(tmp_path):
    case=setup(tmp_path,stress_cost=20000)
    result=run(case)
    assert result['pipeline_status']=='COMPLETED',result
    assert result['mc']['readiness_effect']=='DOWNGRADED'
    assert result['mc']['scenarios_summary']['worst_case']==-1
    assert result['holdout']['evaluation_ordinal']==1


def test_changed_source_stops_before_holdout(tmp_path):
    case=setup(tmp_path);reader=case[5]['read_bars']
    def bad(a,b):rows=reader(a,b);rows[0]['close']+=1;return rows
    case[5].update(read_bars=bad,read_holdout=lambda:pytest.fail('holdout read'))
    result=run(case)
    assert result['pipeline_status']=='STOPPED' and result['stopped_at']=='WALKFORWARD'
    assert result['trial']['lifecycle_status']=='COMPLETED_INVALID'


def test_plan_mutation_during_reader_stops_before_holdout(tmp_path):
    case=setup(tmp_path);args=case[5];reader=args['read_bars'];changed=False
    def bad(a,b):
        nonlocal changed
        if not changed:
            path=args['evidence_directory']/'plan'/'VALIDATION_PLAN.json';value=json.loads(path.read_text())
            value['plan']['mc_policy']['master_seed']=99;path.write_bytes(_canonical(value));changed=True
        return reader(a,b)
    args.update(read_bars=bad,read_holdout=lambda:pytest.fail('holdout read'))
    result=run(case)
    assert result['pipeline_status']=='STOPPED' and result['stopped_at']=='MONTE_CARLO'
    assert result['reason_code']=='VALIDATION_PLAN_MUTATED'


def test_bad_manifest_refuses_before_any_reservation(tmp_path):
    case=setup(tmp_path,prior=False);case[4]['rows'][0]['row_hash']='f'*64
    with pytest.raises(ValueError):run(case)
    state=execute_trial(dict(mode='REGISTRY_STATE',research_program_id=case[0]['research_program_id']),case[5]['registry_path'],case[5]['anchor_path'])
    assert state['N_total']==0 and not list(case[5]['evidence_directory'].iterdir())


def test_changed_walkforward_content_with_old_seal_refuses(tmp_path,monkeypatch):
    import app.research.phase5_pipeline as module
    case=setup(tmp_path);original=module.run_walkforward_trial
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        path=case[5]['evidence_directory']/'walkforward'/'REPORT.json'
        report=json.loads(path.read_text());report['windows'][0]['regime_tag']='tampered'
        path.write_bytes(_canonical(report));return result
    monkeypatch.setattr(module,'run_walkforward_trial',changed)
    case[5]['read_holdout']=lambda:pytest.fail('holdout read')
    result=run(case)
    assert result['pipeline_status']=='STOPPED' and result['reason_code']=='WALKFORWARD_BINDING_INVALID'
    assert not list(case[5]['holdout_directory'].glob('row-*'))


@pytest.mark.parametrize('artifact',['parameters','statistics'])
def test_parameter_artifact_mutation_during_mc_refuses_before_holdout(tmp_path,monkeypatch,artifact):
    import app.research.phase5_pipeline as module
    case=setup(tmp_path);original=module.run_monte_carlo;changed=False
    def mutate(*args,**kwargs):
        nonlocal changed
        result=original(*args,**kwargs)
        if not changed:
            folder=case[5]['evidence_directory']/'walkforward'
            first=json.loads((folder/'REPORT.json').read_text())['windows'][0]['window_id']
            path=folder/f'params-{first}.json'
            if artifact=='statistics':path=case[5]['evidence_directory']/'STATISTICS.json'
            record=json.loads(path.read_text());record['training_hash']=_hash('tampered')
            path.write_bytes(_canonical(record));changed=True
        return result
    monkeypatch.setattr(module,'run_monte_carlo',mutate)
    case[5]['read_holdout']=lambda:pytest.fail('holdout read')
    result=run(case)
    assert result['pipeline_status']=='STOPPED' and result['stopped_at']=='HOLDOUT'
    assert result['reason_code']=='PREREQ_NOT_MET'
    assert not list(case[5]['holdout_directory'].glob('row-*'))


def test_required_cpcv_full_pipeline(tmp_path):
    case=setup(tmp_path,cpcv_required=True);result=run(case)
    if importlib.util.find_spec('skfolio') is None:
        assert result['pipeline_status']=='STOPPED' and result['stopped_at']=='CPCV'
        assert result['reason_code']=='DEP_PIN_MISMATCH'
        assert result['trial'] is None and not list(case[5]['holdout_directory'].glob('row-*'))
    else:
        assert result['pipeline_status']=='COMPLETED',result
        assert result['cpcv']['adapter_status']=='VALID'
        assert len(result['cpcv']['folds'])==3
        assert result['holdout']['holdout_status']=='HOLDOUT_PASSED'
