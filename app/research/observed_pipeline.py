"""Governed observed WF, split geometry, native stress/bootstrap and single-use holdout."""
import copy
import os
from pathlib import Path
import pandas as pd
from .registry_inspection import _hash
from .validation_plan import require,snapshot,read_artifact
from .walkforward import _Store
from .observed_plan import validate_observed_plan
from .observed_walkforward import run_observed_walkforward_trial,verify_observed_walkforward_trial,_descriptor,_validate_policy
from .observed_mc import run_observed_mc,verify_observed_mc
from .observed_holdout import run_observed_holdout,verify_observed_holdout
from .session_coverage import validate_session_coverage,verify_session_rows
from .statistics_report import evaluate_statistics_report
from .cpcv import evaluate_cpcv,applicability_hash
from .rag_provenance import retrieve_advisory
from .observed_session import run_observed_session,verify_observed_session
from .timing_rows import check_timing_rows
from app.strategy_research.evaluation.observed_walkforward import build_native


def _cpcv(plan,q,policy,data):
    start=min(w['train'][0] for w in policy['windows']);end=max(w['test'][1] for w in policy['windows']);duration=data['timeframe_ms']
    samples=[] if plan['cpcv']['cpcv_applicability']=='NOT_APPLICABLE' else [dict(sample_id=str(r['open_time']),feature_time_ms=r['open_time']+duration,
        label_start_ms=r['open_time']+duration,label_end_ms=r['open_time']+2*duration) for r in data['rows'] if start<=r['open_time'] and r['open_time']+2*duration<=end]
    request=dict(experiment_id=q['experiment_id'],sample_index=samples,**plan['cpcv'])
    return evaluate_cpcv(request,frozen_applicability_hash=applicability_hash(request))


def _statistics(run,root):
    artifact=read_artifact(root/'wf','TRIAL_RESULT.json');artifacts=snapshot(run['prior_result_artifacts']);artifacts[_hash(artifact)]=artifact
    q=run['request']
    return evaluate_statistics_report(run['registry_path'],run['anchor_path'],research_program_id=q['research_program_id'],
        trial_id=artifact['trial_id'],result_artifacts=artifacts,threshold_policy={'dsr_min':.95})


def _check_mc_access(plan,mc):
    require(mc['mc_status']=='VALID' and mc['readiness_effect'] in ('PRESERVED','DOWNGRADED'),'MC_INVALID')
    if plan['schema_version']=='wp5.observed_plan.v1':
        require(mc['readiness_effect']=='PRESERVED','MC_DOWNGRADED')
    else:
        require(plan.get('holdout_access_policy')=='AFTER_VALID_MC_RETAIN_DOWNGRADE','OBSERVED_PLAN_INVALID')


def _verify_prerequisites(run,root,expected_mc_hash):
    plan=validate_observed_plan(run['plan']);q=run['request'];policy=run['policy'];data=run['dataset_manifest']
    require(read_artifact(root,'RUN.json')==run and read_artifact(root,'PLAN.json')==plan,'PLAN_BINDING_INVALID')
    verify_observed_walkforward_trial(root/'wf',registry_path=run['registry_path'],anchor_path=run['anchor_path'])
    wf=read_artifact(root/'wf','RUN.json')
    require(wf['request']==q and wf['policy']==policy and wf['dataset_manifest']==data and wf['policy']['validation_plan_hash']==_hash(plan), 'WF_BINDING_INVALID')
    trial=read_artifact(root/'wf','RESULT.json');artifact=read_artifact(root/'wf','TRIAL_RESULT.json');receipt=read_artifact(root/'wf','EXECUTION_RECEIPT.json')
    require(trial['report_hash']==_hash({k:v for k,v in trial.items() if k!='report_hash'}) and trial['result_hash']==_hash(artifact) and
        trial['execution_receipt_hash']==_hash(receipt) and trial['lifecycle_status']==receipt['lifecycle_status']=='COMPLETED_VALID','WF_BINDING_INVALID')
    cpcv=_cpcv(plan,q,policy,data)
    require(cpcv['adapter_status']=='VALID' and read_artifact(root,'CPCV.json')==cpcv,'CPCV_BINDING_INVALID')
    stats=_statistics(run,root)
    require(stats['stats_status']=='VALID' and stats['decision_input']['dsr_pass'] and stats==read_artifact(root,'STATISTICS.json'),'STATISTICS_BINDING_INVALID')
    mc=verify_observed_mc(plan,wf_directory=root/'wf',evidence_directory=root/'mc',expected_report_hash=expected_mc_hash)
    _check_mc_access(plan,mc)
    return dict(wf_result_hash=read_artifact(root/'wf','RESULT.json')['result_hash'],statistics_hash=stats['stats_hash'],
        cpcv_hash=cpcv['adapter_report_hash'],mc_hash=mc['report_hash'],plan_hash=_hash(plan))


def _holdout_request(run,root):
    receipt=read_artifact(root/'wf','EXECUTION_RECEIPT.json');frozen=read_artifact(root/'wf','RUN.json')
    params=read_artifact(root/'wf'/f"window-{len(receipt['windows'])-1:04d}",'PARAMETERS.json')['parameters']
    h=run['plan']['holdout_policy'];q=run['request']
    return dict(strategy_identity=_hash(dict(strategy_id=run['policy']['strategy_id'],source_hash=frozen['strategy']['source_hash'])),
        research_program_id=q['research_program_id'],experiment_id=q['experiment_id'],trial_id=receipt['trial_binding']['trial_id'],
        holdout_dataset_hash=h['dataset_hash'],parameters=params,param_hash=_hash(params),
        decision_rule={k:h[k] for k in ('min_total_return','max_drawdown')},actor=run['actor'])


def _verify_holdout_execution(run,root,expected_hash):
    receipt=read_artifact(root/'holdout-execution','RECEIPT.json')
    require(_hash(receipt)==expected_hash,'HOLDOUT_EXECUTION_MISMATCH')
    hq=_holdout_request(run,root);q=run['request'];bounds=q['contract_record']['boundaries']['holdout']
    observed=verify_observed_session(root/'holdout-execution'/'session',expected_report_hash=receipt['session_evidence_hash'])
    native=read_artifact(root/'holdout-execution'/'session','MANIFEST.json')['native_manifest']
    wf=read_artifact(root/'wf','RUN.json');binding=read_artifact(root/'wf','EXECUTION_RECEIPT.json')['trial_binding']
    require(receipt['prerequisites_hash']==_hash(read_artifact(root,'PREHOLDOUT_VERIFICATION.json')) and
        receipt['holdout_request']==hq and receipt['plan_hash']==_hash(run['plan']) and observed['trial_binding']==binding and
        observed['native_result']['lifecycle_status']=='COMPLETED' and native['params']==hq['parameters'] and native['param_hash']==hq['param_hash'] and
        native['strategy']['strategy_hash']==wf['strategy']['source_hash'] and native['execution_profile']==run['profile'] and
        native['absolute_risk_limits']==run['risk_limits'] and native['initial_capital']==run['policy']['initial_capital'],'HOLDOUT_EXECUTION_MISMATCH')
    data,index=validate_session_coverage(run['dataset_manifest'],q['contract_record'],run['profile']);duration=data['timeframe_ms']
    require([p['timestamp_ms'] for p in observed['equity_points']]==list(range(bounds['start_ms']+duration,bounds['end_ms']+duration,duration)), 'HOLDOUT_INCOMPLETE')
    timing=check_timing_rows(observed['audit']['timing_rows'],q['contract_record'],intended_dataset_zone='HOLDOUT') if observed['audit']['timing_rows'] else None
    require(timing==receipt['timing'] and (timing is None or timing['status']=='PASS') and
        all(bounds['start_ms']<=row['bar_open_ms']<bounds['end_ms'] and bounds['start_ms']<=row['fill_ms']<bounds['end_ms'] and
            row['bar_open_ms'] in index and row['fill_ms'] in index for row in observed['audit']['timing_rows']),'HOLDOUT_TIMING_INVALID')
    require(receipt['returns_hash']==_hash(observed['net_returns']),'HOLDOUT_EXECUTION_MISMATCH')
    return observed['net_returns']


def run_observed_pipeline(request,policy,dataset_manifest,plan,*,profile,risk_limits,strategy,selector,registry_path,anchor_path,
        evidence_directory,holdout_directory,read_bars,read_holdout,prior_result_artifacts,corpus,advisory_query,actor):
    q=snapshot(request);p=_validate_policy(policy,q['contract_record']);plan=validate_observed_plan(plan)
    require(p['schema_version'] in ('wp5.observed_wf_policy.v2','wp5.observed_wf_policy.v3') and p['validation_plan_hash']==_hash(plan) and
        plan['strategy_id']==p['strategy_id'] and plan['dataset_manifest_hash']==_hash(dataset_manifest),'PLAN_BINDING_INVALID')
    data,index=validate_session_coverage(dataset_manifest,q['contract_record'],profile)
    require(type(actor) is str and actor and _hash(corpus)==plan['rag_policy']['corpus_hash'],'PIPELINE_INPUT_INVALID')
    if plan['schema_version']=='wp5.observed_plan.v3':
        from .observed_mc_v2 import check_patches
        require(p['schema_version']=='wp5.observed_wf_policy.v3','PLAN_BINDING_INVALID')
        check_patches(plan['mc_policy'],p['candidates'])
    else:
        require(all(plan['mc_policy']['block_length']<=(w['test'][1]-w['test'][0])//data['timeframe_ms']-1 for w in p['windows']),'BOOTSTRAP_LENGTH_INVALID')
    from .observed_mc import _stress_profile
    for override in plan['mc_policy']['profile_grid']:_stress_profile(profile,override)
    run=dict(schema_version='wp5.observed_pipeline_run.v1',request=q,policy=p,dataset_manifest=data,plan=plan,
        profile=snapshot(profile),risk_limits=snapshot(risk_limits),strategy=_descriptor(strategy),selector=_descriptor(selector),
        registry_path=str(registry_path),anchor_path=str(anchor_path),holdout_directory=str(holdout_directory),
        prior_result_artifacts=snapshot(prior_result_artifacts),corpus=snapshot(corpus),advisory_query=advisory_query,actor=actor)
    root=Path(evidence_directory);store=_Store(root);stage='PREREGISTRATION'
    report=dict(schema_version='wp5.observed_pipeline_report.v1',pipeline_status='STOPPED',stopped_at=None,reason_code=None,
        plan_hash=_hash(plan),trial=None,cpcv=None,statistics=None,mc=None,holdout=None,advisory=None,
        phase5_acceptance_status='LIMITED_PROFILE',prospective_verified=False,promotion_authorized=False,
        acceptance_limits=['CPCV_GEOMETRY_ONLY','MC_COST_STRESS_AND_BOOTSTRAP_ONLY','REAL_DATA_NOT_ATTESTED','PROSPECTIVE_NOT_ATTESTED'])
    if plan['schema_version'] in ('wp5.observed_plan.v2','wp5.observed_plan.v3'):
        version=2 if plan['schema_version']=='wp5.observed_plan.v2' else 3
        run['schema_version']=f'wp5.observed_pipeline_run.v{version}'
        report.update(schema_version=f'wp5.observed_pipeline_report.v{version}',readiness_effect=None,
            holdout_access_policy=plan['holdout_access_policy'])
    if plan['schema_version']=='wp5.observed_plan.v3':
        report['acceptance_limits']=['REAL_DATA_NOT_ATTESTED','PROSPECTIVE_NOT_ATTESTED','SINGLE_PENDING_STRESS_MODEL','ANALYTICAL_REORDER_ONLY']
    try:
        frozen=store.put('RUN.json',run);plan_raw=store.put('PLAN.json',plan)
        advisory=retrieve_advisory(dict(query=advisory_query,phase='POST_FREEZE',max_items=plan['rag_policy']['max_items'],action='RETRIEVE',requested_mutations=[]),
            run['corpus'],expected_corpus_hash=plan['rag_policy']['corpus_hash'])
        store.put('ADVISORY.json',advisory);report['advisory']=advisory
        try:
            stage='CPCV';cpcv=_cpcv(plan,q,p,data);store.put('CPCV.json',cpcv);report['cpcv']=cpcv
            require(cpcv['adapter_status']=='VALID',cpcv['reason_code'] or 'CPCV_INVALID')
            stage='WALKFORWARD';child=root/'wf';child.mkdir();os.fsync(store.fd)
            def reader(a,b):
                store.check('RUN.json',frozen);store.check('PLAN.json',plan_raw)
                return read_bars(a,b)
            trial=run_observed_walkforward_trial(q,p,data,profile=profile,risk_limits=risk_limits,strategy=strategy,selector=selector,
                registry_path=registry_path,anchor_path=anchor_path,evidence_directory=child,read_bars=reader,prior_result_artifacts=prior_result_artifacts)
            report['trial']=trial;require(trial['lifecycle_status']=='COMPLETED_VALID','WALKFORWARD_INVALID')
            stage='STATISTICS';stats=_statistics(run,root);store.put('STATISTICS.json',stats);report['statistics']=stats
            require(stats['stats_status']=='VALID' and stats['decision_input']['dsr_pass'],stats['reason_code'] or 'DSR_THRESHOLD_FAILED')
            stage='MONTE_CARLO';child=root/'mc';child.mkdir();os.fsync(store.fd)
            mc=run_observed_mc(plan,wf_directory=root/'wf',registry_path=registry_path,anchor_path=anchor_path,evidence_directory=child,strategy=strategy,read_bars=reader)
            report['mc']=mc
            if plan['schema_version'] in ('wp5.observed_plan.v2','wp5.observed_plan.v3'):report['readiness_effect']=mc['readiness_effect']
            _check_mc_access(plan,mc)
            stage='HOLDOUT'
            require(snapshot(profile)==run['profile'] and snapshot(risk_limits)==run['risk_limits'] and _descriptor(strategy)==run['strategy'],'RUNTIME_DRIFT')
            pre=_verify_prerequisites(run,root,mc['report_hash']);store.put('PREHOLDOUT_VERIFICATION.json',pre)
            hq=_holdout_request(run,root);child=root/'holdout-execution';child.mkdir();os.fsync(store.fd)
            def prereqs(actual):
                require(snapshot(profile)==run['profile'] and snapshot(risk_limits)==run['risk_limits'] and _descriptor(strategy)==run['strategy'],'RUNTIME_DRIFT')
                require(actual==hq and _verify_prerequisites(run,root,mc['report_hash'])==pre,'PREREQ_NOT_MET')
            def evaluate(rows,params):
                bounds=q['contract_record']['boundaries']['holdout']
                rows=verify_session_rows(rows,index,bounds['start_ms'],bounds['end_ms'])
                require(_hash(rows)==plan['holdout_policy']['dataset_hash'],'HOLDOUT_DATASET_INVALID')
                local=copy.deepcopy(strategy);require(_descriptor(local)==run['strategy'],'STRATEGY_BINDING_INVALID')
                window=dict(window_id=0,train=[bounds['start_ms'],bounds['start_ms']],test=[bounds['start_ms'],bounds['end_ms']])
                native=build_native(p,profile,risk_limits,local,params,window,rows)
                binding=read_artifact(root/'wf','EXECUTION_RECEIPT.json')['trial_binding']
                hs=_Store(child)
                try:
                    session=child/'session';session.mkdir();os.fsync(hs.fd)
                    observed=run_observed_session(manifest=native,profile=profile,risk_limits=risk_limits,strategy=local,params=params,
                        frame=pd.DataFrame(rows),evidence_directory=session,trial_binding=binding)
                    observed=verify_observed_session(session,expected_report_hash=observed['report_hash'])
                    timing=check_timing_rows(observed['audit']['timing_rows'],q['contract_record'],intended_dataset_zone='HOLDOUT') if observed['audit']['timing_rows'] else None
                    receipt=dict(schema_version='wp5.observed_holdout_execution.v1',holdout_request=hq,plan_hash=_hash(plan),
                        session_evidence_hash=observed['report_hash'],timing=timing,returns_hash=_hash(observed['net_returns']),prerequisites_hash=_hash(pre))
                    hs.put('RECEIPT.json',receipt)
                    returns=_verify_holdout_execution(run,root,_hash(receipt))
                    return dict(net_returns=returns,execution_evidence_hash=_hash(receipt))
                finally:hs.close()
            held=run_observed_holdout(hq,directory=holdout_directory,verify_prerequisites=prereqs,read_data=read_holdout,evaluate=evaluate)
            report['holdout']=held
            require(verify_observed_holdout(holdout_directory,held['reservation_key'],verify_execution=lambda h:_verify_holdout_execution(run,root,h))==held,'HOLDOUT_HASH_MISMATCH')
            store.check('RUN.json',frozen);store.check('PLAN.json',plan_raw)
            report.update(pipeline_status='COMPLETED',stopped_at=None,reason_code=None)
        except (ValueError,OSError,KeyError,TypeError,OverflowError) as exc:
            report.update(stopped_at=stage,reason_code=str(exc))
        report['report_hash']=_hash(report);store.put('REPORT.json',report);return report
    finally:store.close()


def verify_observed_pipeline(directory,*,expected_report_hash):
    root=Path(directory);report=read_artifact(root,'REPORT.json');run=read_artifact(root,'RUN.json')
    require(report['report_hash']==expected_report_hash==_hash({k:v for k,v in report.items() if k!='report_hash'}) and report['pipeline_status']=='COMPLETED','PIPELINE_REPORT_INVALID')
    version={'wp5.observed_plan.v1':1,'wp5.observed_plan.v2':2,'wp5.observed_plan.v3':3}.get(run['plan']['schema_version'])
    require(version is not None and run['schema_version']==f'wp5.observed_pipeline_run.v{version}' and
        report['schema_version']==f'wp5.observed_pipeline_report.v{version}','PIPELINE_VERSION_INVALID')
    if version in (2,3):
        require(report['schema_version']==f'wp5.observed_pipeline_report.v{version}' and
            report.get('holdout_access_policy')==run['plan']['holdout_access_policy'] and
            report.get('readiness_effect')==report['mc']['readiness_effect'] and
            report.get('promotion_authorized') is False and report.get('phase5_acceptance_status')=='LIMITED_PROFILE',
            'PIPELINE_READINESS_INVALID')
    pre=_verify_prerequisites(run,root,report['mc']['report_hash'])
    require(read_artifact(root,'PREHOLDOUT_VERIFICATION.json')==pre,'PREREQ_NOT_MET')
    held=verify_observed_holdout(run['holdout_directory'],report['holdout']['reservation_key'],verify_execution=lambda h:_verify_holdout_execution(run,root,h))
    require(held==report['holdout'] and report['trial']==read_artifact(root/'wf','RESULT.json') and report['statistics']==read_artifact(root,'STATISTICS.json') and
        report['cpcv']==read_artifact(root,'CPCV.json') and report['mc']==read_artifact(root/'mc','REPORT.json'),'PIPELINE_REPORT_INVALID')
    return dict(verification_status='VERIFIED',report_hash=expected_report_hash,source_data_read=False,counter_incremented=False,
        statistics_status='CURRENT_LEDGER_MATCHES_SAVED_SNAPSHOT',prospective_verified=False)
