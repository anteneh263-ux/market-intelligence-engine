"""Observed native cost stress plus explicitly analytical within-window bootstrap."""
import copy
import os
from pathlib import Path
import pandas as pd
from app.paper.session.profiles import freeze_execution_profile
from app.strategy_research.evaluation.observed_walkforward import build_native
from .registry_inspection import _hash
from .validation_plan import require,snapshot,read_artifact
from .walkforward import _Store
from .session_coverage import validate_session_coverage,verify_session_rows
from .observed_session import run_observed_session,verify_observed_session
from .observed_walkforward import verify_observed_walkforward_trial
from .monte_carlo import _Random,_metrics


def _derive_draw(plan,trial_hash,g,r,w,returns):
    policy=plan['mc_policy'];n=len(returns)
    require(n>0 and policy['block_length']<=n,'BOOTSTRAP_LENGTH_INVALID')
    seed=_hash(dict(master_seed=policy['master_seed'],plan_hash=_hash(plan),trial_result_hash=trial_hash,grid_index=g,repetition=r,window_id=w))
    rng=_Random(seed);indexes=[]
    while len(indexes)<n:
        start=rng.below(n)
        indexes.extend((start+j)%n for j in range(min(policy['block_length'],n-len(indexes))))
    sample=[returns[i] for i in indexes]
    return dict(seed=seed,indexes=indexes,returns_hash=_hash(sample),metrics=_metrics(sample),evidence_kind='ANALYTICAL_BOOTSTRAP')


def _stress_profile(base,overrides):
    require(base['fee_model']=='bps_on_notional' and all(overrides[k]>=base[k] for k in overrides),'MC_PROFILE_UNSUPPORTED')
    profile=dict(base,**overrides)
    return freeze_execution_profile(base['instrument'],**{k:v for k,v in profile.items() if k not in ('instrument','profile_version')})


def _scenario_reports(plan,trial_hash,executions):
    rule=plan['mc_policy']['downgrade_rule'];scenarios=[]
    for g,windows in enumerate(executions):
        for r in range(plan['mc_policy']['repetitions']):
            draws=[];survives=True
            for w,execution in enumerate(windows):
                if execution['complete']:
                    draw=_derive_draw(plan,trial_hash,g,r,w,execution['net_returns'])
                    metric=draw['metrics'];good=not metric['ruined'] and metric['total_return']>=rule['min_total_return'] and metric['max_drawdown']<=rule['max_drawdown']
                else:draw=dict(evidence_kind='FAILED_EXECUTION',execution_evidence_hash=execution['session_evidence_hash']);good=False
                draws.append(draw);survives=survives and good
            scenarios.append(dict(scenario_id=len(scenarios),grid_index=g,repetition=r,windows=draws,survives=survives))
    return scenarios


def _report(plan,trial_hash,baselines,executions,scenarios):
    n=len(plan['mc_policy']['profile_grid'])*plan['mc_policy']['repetitions']
    require(len(scenarios)==n,'SCENARIO_OMITTED')
    fraction=sum(s['survives'] for s in scenarios)/n
    report=dict(schema_version='wp5.observed_mc.v1',plan_hash=_hash(plan),trial_result_hash=trial_hash,
        baselines=baselines,executions=executions,scenarios=scenarios,required_scenario_count=n,reported_scenario_count=len(scenarios),
        fraction_surviving=fraction,readiness_effect='PRESERVED' if fraction>=plan['mc_policy']['downgrade_rule']['min_fraction_surviving'] else 'DOWNGRADED',
        mc_status='VALID',continuous_portfolio=False,unevaluated=['EXECUTION_DELAY','MISSED_FILLS','TRADE_REORDER','PARAMETER_SENSITIVITY','MODEL_UNCERTAINTY'])
    report['report_hash']=_hash(report);return report


def run_observed_mc(plan,*,wf_directory,registry_path,anchor_path,evidence_directory,strategy,read_bars):
    if plan['schema_version']=='wp5.observed_plan.v3':
        from .observed_mc_v2 import run_observed_mc_v2
        return run_observed_mc_v2(plan,wf_directory=wf_directory,registry_path=registry_path,anchor_path=anchor_path,evidence_directory=evidence_directory,strategy=strategy,read_bars=read_bars)
    verify_observed_walkforward_trial(wf_directory,registry_path=registry_path,anchor_path=anchor_path)
    frozen=read_artifact(wf_directory,'RUN.json');receipt=read_artifact(wf_directory,'EXECUTION_RECEIPT.json');artifact=read_artifact(wf_directory,'TRIAL_RESULT.json')
    require(receipt['lifecycle_status']=='COMPLETED_VALID' and frozen['policy']['validation_plan_hash']==_hash(plan),'BASE_BINDING_INVALID')
    data,index=validate_session_coverage(frozen['dataset_manifest'],frozen['request']['contract_record'],frozen['profile'])
    root=_Store(evidence_directory);folder=Path(evidence_directory);baselines=[];executions=[]
    try:
        plan_raw=root.put('PLAN.json',plan)
        for g,override in enumerate([None]+plan['mc_policy']['profile_grid']):
            windows=[]
            for i,base in enumerate(receipt['windows']):
                root.check('PLAN.json',plan_raw)
                window=base['window'];original=Path(wf_directory)/f'window-{i:04d}'
                parameters=read_artifact(original,'PARAMETERS.json')['parameters']
                rows=verify_session_rows(read_bars(*window['test']),index,*window['test'])
                profile=frozen['profile'] if override is None else _stress_profile(frozen['profile'],override)
                local=copy.deepcopy(strategy)
                from .observed_walkforward import _descriptor
                require(_descriptor(local)==frozen['strategy'],'STRATEGY_BINDING_INVALID')
                native=build_native(frozen['policy'],profile,frozen['risk_limits'],local,parameters,window,rows)
                name=f'baseline-{i:04d}' if override is None else f'grid-{g-1:04d}-window-{i:04d}'
                child=folder/name;child.mkdir();os.fsync(root.fd)
                observed=run_observed_session(manifest=native,profile=profile,risk_limits=frozen['risk_limits'],strategy=local,params=parameters,
                    frame=pd.DataFrame(rows),evidence_directory=child,trial_binding=receipt['trial_binding'])
                observed=verify_observed_session(child,expected_report_hash=observed['report_hash'])
                if override is None:
                    require(observed['report_hash']==base['session_evidence_hash'] and observed['net_returns']==base['returns'],'BASELINE_REPLAY_MISMATCH')
                duration=data['timeframe_ms'];endpoints=list(range(window['test'][0]+duration,window['test'][1]+duration,duration))
                item=dict(directory=name,session_evidence_hash=observed['report_hash'],net_returns=observed['net_returns'],
                    complete=observed['native_result']['lifecycle_status']=='COMPLETED' and [p['timestamp_ms'] for p in observed['equity_points']]==endpoints)
                windows.append(item)
            if override is None:baselines=windows
            else:executions.append(windows)
        scenarios=_scenario_reports(plan,_hash(artifact),executions)
        for i,scenario in enumerate(scenarios):root.put(f'SCENARIO-{i:04d}.json',scenario)
        report=_report(plan,_hash(artifact),baselines,executions,scenarios)
        root.check('PLAN.json',plan_raw);root.put('REPORT.json',report)
        verify_observed_mc(plan,wf_directory=wf_directory,evidence_directory=folder,expected_report_hash=report['report_hash'])
        return report
    finally:root.close()


def verify_observed_mc(plan,*,wf_directory,evidence_directory,expected_report_hash):
    if plan['schema_version']=='wp5.observed_plan.v3':
        from .observed_mc_v2 import verify_observed_mc_v2
        return verify_observed_mc_v2(plan,wf_directory=wf_directory,evidence_directory=evidence_directory,expected_report_hash=expected_report_hash)
    folder=Path(evidence_directory);report=read_artifact(folder,'REPORT.json')
    require(report['report_hash']==expected_report_hash==_hash({k:v for k,v in report.items() if k!='report_hash'}) and read_artifact(folder,'PLAN.json')==plan,'MC_BINDING_INVALID')
    frozen=read_artifact(wf_directory,'RUN.json');receipt=read_artifact(wf_directory,'EXECUTION_RECEIPT.json');artifact=read_artifact(wf_directory,'TRIAL_RESULT.json')
    require(receipt['lifecycle_status']=='COMPLETED_VALID' and report['trial_result_hash']==_hash(artifact),'BASE_BINDING_INVALID')
    count=len(receipt['windows']);grid=plan['mc_policy']['profile_grid']
    require(len(report['baselines'])==count and len(report['executions'])==len(grid) and all(len(v)==count for v in report['executions']),'SCENARIO_OMITTED')
    expected_names={'PLAN.json','REPORT.json'}|{f'SCENARIO-{i:04d}.json' for i in range(len(grid)*plan['mc_policy']['repetitions'])}
    for g,group in enumerate([report['baselines']]+report['executions']):
        for i,item in enumerate(group):
            name=f'baseline-{i:04d}' if g==0 else f'grid-{g-1:04d}-window-{i:04d}';expected_names.add(name)
            require(item['directory']==name,'MC_BINDING_INVALID')
            observed=verify_observed_session(folder/name,expected_report_hash=item['session_evidence_hash'])
            native=read_artifact(folder/name,'MANIFEST.json')['native_manifest']
            original=read_artifact(Path(wf_directory)/f'window-{i:04d}','NATIVE_MANIFEST.json')
            expected=snapshot(original)
            if g:
                from app.paper.session.profiles import execution_profile_hash
                expected['execution_profile']=_stress_profile(frozen['profile'],grid[g-1]);expected['execution_profile_hash']=execution_profile_hash(expected['execution_profile'])
                expected['cost_assumptions'].update(grid[g-1])
            require(observed['trial_binding']==receipt['trial_binding'] and item['net_returns']==observed['net_returns'],'MC_BINDING_INVALID')
            if g==0:require(observed['report_hash']==receipt['windows'][i]['session_evidence_hash'],'BASELINE_REPLAY_MISMATCH')
            else:
                # Compare all semantic fields; derived session/manifest IDs validate in the observer.
                require({k:v for k,v in native.items() if k not in ('manifest_hash','session_id')}==
                    {k:v for k,v in expected.items() if k not in ('manifest_hash','session_id')},'MC_PROFILE_BINDING_INVALID')
            w=receipt['windows'][i]['window'];duration=frozen['dataset_manifest']['timeframe_ms']
            complete=observed['native_result']['lifecycle_status']=='COMPLETED' and [p['timestamp_ms'] for p in observed['equity_points']]==list(range(w['test'][0]+duration,w['test'][1]+duration,duration))
            require(item['complete']==complete,'MC_BINDING_INVALID')
    require({p.name for p in folder.iterdir()}==expected_names,'SCENARIO_OMITTED')
    scenarios=_scenario_reports(plan,_hash(artifact),report['executions'])
    require(all(read_artifact(folder,f'SCENARIO-{i:04d}.json')==s for i,s in enumerate(scenarios)) and
        report==_report(plan,_hash(artifact),report['baselines'],report['executions'],scenarios),'MC_DERIVATION_INVALID')
    return report
