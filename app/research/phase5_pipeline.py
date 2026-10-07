"""Governed historical Phase5 sequence, strict continuous-bar profile v1.

Real roots/data require caller authorization. Tests use synthetic fixtures only.
This is not prospective execution or automatic promotion.
"""
import os
import pandas as pd

from .registry_inspection import _hash,_hex
from .validation_plan import validate_plan,freeze_validation_plan,read_validation_plan,read_artifact,require,snapshot,finite
from .walkforward import _Store,_policy
from .walkforward_trial import run_walkforward_trial,_preflight
from .cpcv import evaluate_cpcv,applicability_hash
from .rag_provenance import retrieve_advisory
from .monte_carlo import run_monte_carlo,base_hash,_metrics,_quantile
from .bound_statistics import evaluate_trial_statistics
from .statistics_report import evaluate_statistics_report
from .holdout import run_holdout
from app.strategy_research.evaluation.backtest import run_backtest


def _manifest(value,contract):
    m=snapshot(value);d=contract['dataset_identity']
    require(type(m) is dict and set(m)=={'schema_version','timeframe_ms','rows','holdout_dataset_hash'} and
        m['schema_version']=='wp5.dataset_manifest.v1' and _hash(m)==d['dataset_hash'] and
        m['timeframe_ms']==d['timeframe_ms'] and _hex(m['holdout_dataset_hash']),'DATASET_MANIFEST_INVALID')
    require(d['coverage_mode']=='CONTINUOUS_24_7','COVERAGE_PROFILE_UNSUPPORTED')
    require(type(m['rows']) is list and len(m['rows'])==d['rows'] and m['rows'],'DATASET_MANIFEST_INVALID')
    ids=set();expected=d['first_open_ms'];index={}
    for row in m['rows']:
        require(type(row) is dict and set(row)=={'sample_id','open_time','close_time','feature_time_ms','label_start_ms','label_end_ms','row_hash'} and
            type(row['sample_id']) is str and row['sample_id'] and row['sample_id'] not in ids and
            type(row['open_time']) is int and type(row['close_time']) is int and row['open_time']==expected and
            row['close_time']==expected+d['timeframe_ms'] and row['feature_time_ms']==row['close_time'] and
            type(row['label_start_ms']) is int and type(row['label_end_ms']) is int and
            row['close_time']<=row['label_start_ms']<row['label_end_ms'] and _hex(row['row_hash']),'DATASET_MANIFEST_INVALID')
        ids.add(row['sample_id']);index[row['open_time']]=row;expected=row['close_time']
    require(expected==d['dataset_end_exclusive_ms'] and m['rows'][-1]['open_time']==d['last_open_ms'],
            'DATASET_MANIFEST_INVALID')
    return m,index


def _verify_rows(rows,start,end,index,duration):
    value=snapshot(rows)
    expected=list(range(start,end,duration))
    require(type(value) is list and len(value)==len(expected),'DATASET_CONTENT_MISMATCH')
    for row,t in zip(value,expected):
        require(type(row) is dict and row.get('open_time')==t and t in index and
            row.get('sample_id')==index[t]['sample_id'] and row.get('close_time')==index[t]['close_time'] and
            row.get('label_start_ms')==index[t]['label_start_ms'] and row.get('label_end_ms')==index[t]['label_end_ms'] and
            _hash(row)==index[t]['row_hash'],'DATASET_CONTENT_MISMATCH')
    return value


def _feature(rows):return [{k:r[k] for k in ('open_time','close_time','close')} for r in rows]


def _aggregate_mc(reports,policy):
    require(reports and all(r['mc_status']=='VALID' and r['mc_policy_hash']==_hash(policy) and
        r['mc_report_hash']==_hash({k:v for k,v in r.items() if k!='mc_report_hash'}) for r in reports),'MC_INVALID')
    scenarios=[];rule=policy['downgrade_rule']
    for i in range(policy['n_scenarios']):
        returns=[]
        for report in reports:
            require(len(report['scenarios'])==policy['n_scenarios'],'SCENARIO_OMITTED')
            scenario=report['scenarios'][i]
            require(scenario['scenario_id']==i and _hash(scenario['net_returns'])==scenario['returns_hash'],
                    'SCENARIO_OMITTED')
            returns.extend(scenario['net_returns'])
        metric=_metrics(returns)
        survives=not metric['ruined'] and metric['total_return']>=rule['min_total_return'] and metric['max_drawdown']<=rule['max_drawdown']
        scenarios.append(dict(scenario_id=i,metrics=metric,survives=survives,returns_hash=_hash(returns)))
    fraction=sum(s['survives'] for s in scenarios)/len(scenarios);values=[s['metrics']['total_return'] for s in scenarios]
    report=dict(schema_version='wp5_5.aggregate.v1',mc_status='VALID',mc_policy_hash=_hash(policy),
        window_report_hashes=[r['mc_report_hash'] for r in reports],scenarios=scenarios,reported_scenario_count=len(scenarios),
        readiness_effect='PRESERVED' if fraction>=rule['min_fraction_surviving'] else 'DOWNGRADED',
        scenarios_summary=dict(fraction_surviving=fraction,worst_case=min(values),
            metric_quantiles={str(q):_quantile(values,q) for q in (.05,.5,.95)}),rule_hash=_hash(rule))
    report['mc_report_hash']=_hash(report)
    return report


def run_phase5_pipeline(reservation_request,policy,segment_manifest,validation_plan,dataset_manifest,*,
                        registry_path,anchor_path,evidence_directory,holdout_directory,
                        read_bars,select_parameters,signal,regime,read_holdout,
                        prior_result_artifacts,corpus,advisory_query,cpcv_sample_index,actor):
    """Run registered historical validation through single-use final holdout.

    Unsupported coverage profiles refuse before reservation. A stopped stage never
    authorizes holdout. HOLDOUT_PASSED still sets promotion_requires_human=True.
    """
    p=_policy(policy);plan=validate_plan(validation_plan)
    require(p.get('validation_plan_hash')==_hash(plan),'VALIDATION_PLAN_BINDING_INVALID')
    q,p,segments=_preflight(reservation_request,p,segment_manifest)
    require(plan['strategy_id']==p['strategy_id'] and plan['dataset_manifest_hash']==_hash(segments),
            'VALIDATION_PLAN_BINDING_INVALID')
    require(plan['holdout_policy'].get('parameter_source')=='LAST_WF_TRAIN' and
        plan['holdout_policy'].get('warmup_policy')=='NONE','HOLDOUT_POLICY_INVALID')
    manifest,index=_manifest(dataset_manifest,q['contract_record'])
    require(manifest['holdout_dataset_hash']==plan['holdout_policy']['dataset_hash'],'HOLDOUT_DATASET_INVALID')
    require(type(actor) is str and actor,'STRATEGY_IDENTITY_INVALID')
    corpus_copy=snapshot(corpus);artifacts=snapshot(prior_result_artifacts)
    root=_Store(evidence_directory)
    from pathlib import Path
    folder=Path(evidence_directory)
    report=dict(schema_version='wp5.pipeline.v1',pipeline_status='STOPPED',stopped_at=None,reason_code=None,
        experiment_id=q['experiment_id'],validation_plan_hash=_hash(plan),dataset_manifest_hash=_hash(manifest),
        dataset_binding='MANIFEST_VERIFIED_ACCESSED_ROWS_VERIFIED',trial=None,cpcv=None,mc=None,holdout=None,advisory=None,
        promotion_requires_human=True,statistics=None,
        validation_profile='HISTORICAL_CONTINUOUS_CLOSE_TO_CLOSE',
        phase5_acceptance_status='INCOMPLETE',
        acceptance_limits=['FULL_EXECUTION_TIMING_NOT_ATTESTED','REAL_SCIENTIFIC_VALIDATION_NOT_PERFORMED'])
    stage='PREREGISTRATION'
    try:
        root.put('RUN.json',dict(reservation_request=q,policy=p,validation_plan_hash=_hash(plan)))
        root.put('DATASET_MANIFEST.json',manifest)
        for child in ('plan','trial','walkforward'):(folder/child).mkdir()
        os.fsync(root.fd)
        freeze_validation_plan(plan,evidence_directory=folder/'plan')
        report['advisory']=retrieve_advisory(dict(query=advisory_query,phase='POST_FREEZE',max_items=plan['rag_policy']['max_items'],
            action='RETRIEVE',requested_mutations=[]),corpus_copy,expected_corpus_hash=plan['rag_policy']['corpus_hash'])
        root.put('ADVISORY.json',report['advisory'])
        try:
            stage='CPCV'
            expected_samples=[] if plan['cpcv']['cpcv_applicability']=='NOT_APPLICABLE' else [
                {k:r[k] for k in ('sample_id','feature_time_ms','label_start_ms','label_end_ms')} for r in manifest['rows']
                if p['start_ms']<=r['open_time']<p['end_ms'] and r['label_end_ms']<=p['end_ms']]
            require(snapshot(cpcv_sample_index)==expected_samples,'CPCV_SAMPLE_BINDING_INVALID')
            cpcv_request=dict(experiment_id=q['experiment_id'],sample_index=snapshot(cpcv_sample_index),**plan['cpcv'])
            cpcv=evaluate_cpcv(cpcv_request,frozen_applicability_hash=applicability_hash(cpcv_request))
            root.put('CPCV.json',cpcv);report['cpcv']=cpcv
            require(cpcv['adapter_status']=='VALID',cpcv['reason_code'] or 'CPCV_INVALID')
            stage='WALKFORWARD';cache={};duration=manifest['timeframe_ms']
            def reader(start,end):
                rows=_verify_rows(read_bars(start,end),start,end,index,duration)
                cache[start,end]=snapshot(rows)
                return rows
            trial=run_walkforward_trial(q,p,segments,registry_path=registry_path,anchor_path=anchor_path,
                evidence_directory=folder/'trial',walkforward_directory=folder/'walkforward',read_bars=reader,
                select_parameters=select_parameters,signal=signal,regime=regime,prior_result_artifacts=artifacts)
            report['trial']=trial
            require(trial['lifecycle_status']=='COMPLETED_VALID',trial['completion_reason'] or 'WALKFORWARD_INVALID')
            stage='STATISTICS';stats=trial['statistics']
            require(stats is not None and stats['binding_status']=='VERIFIED','STATISTICS_BINDING_INVALID')
            require(stats['statistics']['stats_status']=='VALID' and stats['statistics']['decision_input']['dsr_pass'] and
                stats['statistics']['dsr']>=plan['holdout_policy']['dsr_min'],stats['statistics']['reason_code'] or 'DSR_THRESHOLD_FAILED')
            artifact=read_artifact(folder/'trial','TRIAL_RESULT.json');artifacts[trial['result_hash']]=artifact
            sealed_stats=evaluate_statistics_report(registry_path,anchor_path,
                research_program_id=q['research_program_id'],trial_id=trial['trial_id'],
                result_artifacts=artifacts,threshold_policy={'dsr_min':.95})
            require(sealed_stats['stats_status']=='VALID' and sealed_stats['decision_input']['dsr_pass'],
                sealed_stats['reason_code'] or 'DSR_THRESHOLD_FAILED')
            root.put('STATISTICS.json',sealed_stats);report['statistics']=sealed_stats
            wf=read_artifact(folder/'walkforward','REPORT.json')
            require(_hash({k:v for k,v in wf.items() if k!='walkforward_report_hash'})==
                wf['walkforward_report_hash']==trial['walkforward_report_hash'],'WALKFORWARD_BINDING_INVALID')
            frozen_records=[]
            stage='MONTE_CARLO';mc_reports=[];last_params=None
            for window in wf['windows']:
                train=cache[tuple(window['train'])];test=cache[tuple(window['test'])]
                frozen=read_artifact(folder/'walkforward',f"params-{window['window_id']}.json")
                opened=read_artifact(folder/'walkforward',f"open-{window['window_id']}.json")
                require(_hash(frozen)==window['frozen_artifact_hash']==opened['frozen_artifact_hash'] and
                    frozen['window_id']==window['window_id'] and frozen['policy_hash']==wf['policy_hash'] and
                    frozen['train']==window['train'] and frozen['test']==window['test']==opened['test'] and
                    opened['event']=='OPEN_OOS','PARAM_NOT_FROZEN')
                frozen_records.append((frozen,opened))
                params=frozen['parameters'];require(_hash(params)==window['param_hash'],'PARAM_NOT_FROZEN')
                base=dict(experiment_id=q['experiment_id'],param_hash=_hash(params),parameters=params,
                    warmup_rows=_feature(train[:-1]),rows=_feature([train[-1]]+test),positions=window['decision_positions'],
                    cost_bps=p['cost_bps'],slippage_bps=p['slippage_bps'],timeframe=p['timeframe'])
                base['base_run_ref']=base_hash(base)
                # Bind scenario baseline to exactly the committed OOS computation.
                baseline=run_backtest(pd.DataFrame(base['rows']),pd.Series(base['positions']),p['timeframe'],p['cost_bps'],p['slippage_bps'])
                require(baseline.net_returns[1:].tolist()==window['net_metrics']['returns'],'BASE_RESULT_MISMATCH')
                mc_dir=folder/f"mc-{window['window_id']}";mc_dir.mkdir();os.fsync(root.fd)
                mc=run_monte_carlo(base,plan_directory=folder/'plan',expected_plan_hash=_hash(plan),evidence_directory=mc_dir,signal=signal)
                require(mc['mc_status']=='VALID',mc['reason_code'] or 'MC_INVALID')
                mc_reports.append(mc);last_params=params
            aggregate=_aggregate_mc(mc_reports,plan['mc_policy']);root.put('MONTE_CARLO.json',aggregate);report['mc']=aggregate
            stage='HOLDOUT'
            artifact=read_artifact(folder/'trial','TRIAL_RESULT.json');artifacts[trial['result_hash']]=artifact
            def prereqs(holdout_request):
                require(read_artifact(folder,'STATISTICS.json')==sealed_stats,'PREREQ_NOT_MET')
                current_wf=read_artifact(folder/'walkforward','REPORT.json')
                require(current_wf==wf and _hash({k:v for k,v in current_wf.items() if k!='walkforward_report_hash'})==
                    trial['walkforward_report_hash'],'WALKFORWARD_BINDING_INVALID')
                for window,(frozen,opened) in zip(wf['windows'],frozen_records):
                    require(read_artifact(folder/'walkforward',f"params-{window['window_id']}.json")==frozen and
                        read_artifact(folder/'walkforward',f"open-{window['window_id']}.json")==opened,'PARAM_NOT_FROZEN')
                frozen_plan=read_validation_plan(folder/'plan',expected_plan_hash=_hash(plan))
                require(frozen_plan==plan and read_artifact(folder,'CPCV.json')==cpcv,'APPLICABILITY_CHANGED_POST_RESULTS')
                require(read_artifact(folder,'MONTE_CARLO.json')==aggregate and
                        aggregate['readiness_effect'] in ('PRESERVED','DOWNGRADED'),'PREREQ_NOT_MET')
                for window,expected in zip(wf['windows'],mc_reports):
                    require(read_artifact(folder/f"mc-{window['window_id']}",'REPORT.json')==expected,'PREREQ_NOT_MET')
                current=evaluate_trial_statistics(registry_path,anchor_path,research_program_id=q['research_program_id'],
                    trial_id=trial['trial_id'],result_artifacts=artifacts)
                require(current['binding_status']=='VERIFIED' and current['statistics']['stats_status']=='VALID' and
                    current['statistics']['decision_input']['dsr_pass'] and current['statistics']['dsr']>=plan['holdout_policy']['dsr_min'],
                    'PREREQ_NOT_MET')
                root.put('PREHOLDOUT_VERIFICATION.json',dict(statistics=current,cpcv_hash=cpcv['adapter_report_hash'],
                    mc_hash=aggregate['mc_report_hash'],plan_hash=_hash(plan),trial_id=trial['trial_id']))
            boundary=q['contract_record']['boundaries']['holdout']
            def holdout_reader():
                return _verify_rows(read_holdout(),boundary['start_ms'],boundary['end_ms'],index,duration)
            def holdout_evaluator(rows,params):
                require(len(rows)>=2,'HOLDOUT_DATASET_INVALID')
                features=_feature(rows);positions=[]
                for i in range(len(rows)-1):
                    supplied=snapshot(params);v=signal(snapshot(features[:i+1]),supplied)
                    require(_hash(supplied)==_hash(params),'POST_ACCESS_MUTATION')
                    require(type(v) in (int,float) and v in (-1,0,1),'HOLDOUT_DATASET_INVALID');positions.append(v)
                positions.append(0.)
                return run_backtest(pd.DataFrame(features),pd.Series(positions),p['timeframe'],p['cost_bps'],p['slippage_bps']).net_returns[1:].tolist()
            holdout_request=dict(strategy_identity=q['contract_record']['research_scope']['strategy_family_hash'],
                research_program_id=q['research_program_id'],experiment_id=q['experiment_id'],trial_id=trial['trial_id'],
                holdout_dataset_hash=plan['holdout_policy']['dataset_hash'],parameters=last_params,param_hash=_hash(last_params),
                decision_rule={k:plan['holdout_policy'][k] for k in ('min_total_return','max_drawdown')},actor=actor)
            holdout=run_holdout(holdout_request,directory=holdout_directory,verify_prerequisites=prereqs,
                                read_data=holdout_reader,evaluate=holdout_evaluator)
            report['holdout']=holdout
            require(holdout['holdout_status'] in ('HOLDOUT_PASSED','HOLDOUT_FAILED'),holdout['reason_code'] or 'HOLDOUT_INVALID')
            report.update(pipeline_status='COMPLETED',stopped_at=None)
        except (ValueError,TypeError,KeyError,OSError,OverflowError,RecursionError) as exc:
            report.update(stopped_at=stage,reason_code=str(exc))
        report['pipeline_report_hash']=_hash(report)
        root.put('RESULT.json',report)
        return report
    finally:
        root.close()
