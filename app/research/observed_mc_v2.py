"""Complete preregistered stress grid using separately versioned native execution."""
import copy
import os
from pathlib import Path
import pandas as pd
from .registry_inspection import _hash
from .validation_plan import require,snapshot,read_artifact,finite
from .walkforward import _Store
from .observed_walkforward import verify_observed_walkforward_trial,_descriptor
from .observed_session import run_observed_session,verify_observed_session,_read_journal
from .stress_session import run_observed_session as run_stress,verify_observed_session as verify_stress,stress_manifest
from .observed_mc import _stress_profile
from .session_coverage import validate_session_coverage,verify_session_rows
from .monte_carlo import _Random,_metrics,_quantile
from app.paper.session.profiles import execution_profile_hash


def validate_policy(m):
    fields={'master_seed','profile_grid','parameter_grid','repetitions','execution_delay_bars','missed_fill_probability','order_perturbation','n_scenarios','downgrade_rule'}
    require(type(m) is dict and set(m)==fields and type(m['master_seed']) is int and 0<=m['master_seed']<2**64 and
        type(m['profile_grid']) is list and m['profile_grid'] and type(m['parameter_grid']) is list and m['parameter_grid'] and
        type(m['repetitions']) is int and m['repetitions']>0 and type(m['n_scenarios']) is int and
        0<m['n_scenarios']<=10000 and m['n_scenarios']==len(m['profile_grid'])*len(m['parameter_grid'])*m['repetitions'] and
        type(m['execution_delay_bars']) is int and 0<=m['execution_delay_bars']<=10000 and finite(m['missed_fill_probability']) and
        0<=m['missed_fill_probability']<=1 and m['order_perturbation'] in ('NONE','SHUFFLE_EXPOSURE_BLOCKS'),'MC_POLICY_INVALID')
    for grid in ('profile_grid','parameter_grid'):require(len({_hash(v) for v in m[grid]})==len(m[grid]),'MC_POLICY_INVALID')
    require(all(type(v) is dict and all(type(k) is str and k for k in v) and all(finite(x) for x in v.values()) for v in m['parameter_grid']),'MC_POLICY_INVALID')
    for v in m['profile_grid']:
        require(type(v) is dict and set(v)=={'taker_fee_bps','slippage_bps','half_spread_bps'} and all(finite(x) and x>=0 for x in v.values()),'MC_POLICY_INVALID')
    r=m['downgrade_rule']
    require(type(r) is dict and set(r)=={'min_total_return','max_drawdown','min_fraction_surviving'} and all(finite(v) for v in r.values()) and
        0<=r['max_drawdown']<=1 and 0<=r['min_fraction_surviving']<=1,'MC_POLICY_INVALID')


def check_patches(m,candidates):
    for patch in m['parameter_grid']:
        require(all(set(patch)<=set(base) and all(type(patch[k]) is type(base[k]) and finite(base[k]) for k in patch) for base in candidates),'MC_PARAMETER_PATCH_INVALID')


def schedule(plan,frozen,receipt,artifact,wf):
    m=plan['mc_policy'];result=[]
    check_patches(m,frozen['policy']['candidates'])
    for g,grid in enumerate(m['profile_grid']):
        profile=_stress_profile(frozen['profile'],grid)
        for j,patch in enumerate(m['parameter_grid']):
            for r in range(m['repetitions']):
                items=[];s=len(result)
                for i,base in enumerate(receipt['windows']):
                    params=read_artifact(Path(wf)/f'window-{i:04d}','PARAMETERS.json')['parameters']
                    seed=_hash(dict(master_seed=m['master_seed'],plan_hash=_hash(plan),trial_result_hash=_hash(artifact),grid_index=g,patch_index=j,repetition=r,window_id=i))
                    stress=dict(schema_version='paper.stress_policy.v1',seed=seed,execution_delay_bars=m['execution_delay_bars'],
                        missed_fill_probability=m['missed_fill_probability'],decision_policy='SINGLE_PENDING',safe_exit_policy='SAME_DELAY_AND_MISS_ONCE',broker_error_policy='FAIL_CLOSED')
                    items.append(dict(window_id=i,parameters=dict(params,**patch),profile=profile,stress=stress,directory=f'scenario-{s:04d}-window-{i:04d}'))
                result.append(dict(scenario_id=s,grid_index=g,patch_index=j,repetition=r,windows=items))
    require(len(result)==m['n_scenarios'],'SCENARIO_OMITTED');return result


def _native(original,item,zero=False):
    native=snapshot(original)
    if not zero:
        native['params']=item['parameters'];native['param_hash']=_hash(item['parameters'])
        native['execution_profile']=item['profile'];native['execution_profile_hash']=execution_profile_hash(item['profile'])
        for k in ('taker_fee_bps','slippage_bps','half_spread_bps'):native['cost_assumptions'][k]=item['profile'][k]
    stress=snapshot(item['stress'])
    if zero:stress.update(execution_delay_bars=0,missed_fill_probability=0.)
    return stress_manifest(native,stress)


def _complete(observed,window,duration):
    return observed['native_result']['lifecycle_status']=='COMPLETED' and [p['timestamp_ms'] for p in observed['equity_points']]==list(range(window['test'][0]+duration,window['test'][1]+duration,duration))


def _financial(directory,observed):
    native=read_artifact(directory,'MANIFEST.json')['native_manifest'];journal=_read_journal(directory,native['manifest_hash'])
    return dict(orders=[e['payload'] for e in journal if e['event_type']=='order'],broker=observed['native_result']['broker'],
        equity_points=observed['equity_points'],net_returns=observed['net_returns'])


def _analytical(directory,observed,item,order_policy):
    native=read_artifact(directory,'MANIFEST.json')['native_manifest'];journal=_read_journal(directory,native['manifest_hash'])
    # Interval classification uses BOTH endpoint holdings, retaining entire mixed
    # old/new-position return (including its overnight gap and costs) as one sample.
    states=[(e['timestamp'],e['payload']['position_units']) for e in journal if e['event_type']=='portfolio_snapshot']
    end=(journal[-1]['timestamp'],journal[-1]['payload']['broker']['position_units'])
    if end[0]==states[-1][0]:states[-1]=end
    else:states.append(end)
    returns=observed['net_returns'];require(len(states)==len(returns)+1,'MC_BLOCKS_INVALID')
    blocks=[];last=None
    for i,value in enumerate(returns):
        state=(states[i][1],states[i+1][1])
        if i==0 or state!=last:blocks.append(dict(block_id=len(blocks),endpoint_positions=list(state),return_indexes=[]))
        blocks[-1]['return_indexes'].append(i);last=state
    ids=list(range(len(blocks)))
    if order_policy!='NONE':
        rng=_Random(_hash(dict(seed=item['stress']['seed'],purpose='EXPOSURE_BLOCK_REORDER')))
        for i in range(len(ids)-1,0,-1):j=rng.below(i+1);ids[i],ids[j]=ids[j],ids[i]
    indexes=[i for b in ids for i in blocks[b]['return_indexes']]
    require(sorted(indexes)==list(range(len(returns))),'MC_BLOCKS_INVALID')
    values=[returns[i] for i in indexes]
    return dict(evidence_kind='ANALYTICAL_EXPOSURE_BLOCK_REORDER',blocks=blocks,block_order=ids,return_indexes=indexes,
        returns_hash=_hash(values),metrics=_metrics(values),timestamps=None,completed_trade_interpretation=False)


def _scenario(entry,items,m):
    complete=all(v['complete'] for v in items);metrics=None
    if complete:
        vals=[v['analytical']['metrics'] for v in items]
        metrics=dict(total_return=sum(v['total_return'] for v in vals)/len(vals),max_drawdown=max(v['max_drawdown'] for v in vals),ruined=any(v['ruined'] for v in vals))
    rule=m['downgrade_rule'];survives=complete and not metrics['ruined'] and metrics['total_return']>=rule['min_total_return'] and metrics['max_drawdown']<=rule['max_drawdown']
    return dict(scenario_id=entry['scenario_id'],grid_index=entry['grid_index'],patch_index=entry['patch_index'],repetition=entry['repetition'],
        windows=items,complete=complete,metrics=metrics,survives=survives,continuous_portfolio=False)


def _report(plan,artifact,baselines,scenarios):
    m=plan['mc_policy'];n=m['n_scenarios'];require(len(scenarios)==n,'SCENARIO_OMITTED')
    fraction=sum(v['survives'] for v in scenarios)/n;all_complete=all(v['complete'] for v in scenarios)
    summary=dict(fraction_surviving=fraction,metric_quantiles=None,worst_case=None,incomplete_scenario_ids=[s['scenario_id'] for s in scenarios if not s['complete']],
        aggregation='EQUAL_WINDOW_MEAN_RETURN_AND_WORST_WINDOW_DRAWDOWN')
    if all_complete:
        summary['metric_quantiles']={k:{str(q):_quantile([s['metrics'][k] for s in scenarios],q) for q in (.05,.5,.95)} for k in ('total_return','max_drawdown')}
        summary['worst_case']=dict(total_return=min(s['metrics']['total_return'] for s in scenarios),max_drawdown=max(s['metrics']['max_drawdown'] for s in scenarios))
    report=dict(schema_version='wp5.observed_mc.v2',plan_hash=_hash(plan),mc_policy_hash=_hash(m),trial_result_hash=_hash(artifact),
        baselines=baselines,scenarios=scenarios,generated_scenario_count=n,valid_generated_scenario_count=n,reported_scenario_count=n,dropped_scenarios=[],
        scenarios_summary=summary,readiness_effect='PRESERVED' if fraction>=m['downgrade_rule']['min_fraction_surviving'] else 'DOWNGRADED',
        readiness_effect_derivation=dict(rule_hash=_hash(m['downgrade_rule']),recomputed=True),mc_status='VALID',reason_code=None,
        checks=[dict(check_id=k,status='PASS') for k in ('POLICY_PREREGISTRATION','SEED_CONTRACT','PERTURBATION_POLICY','SCENARIO_GENERATION','SCENARIO_COMPLETENESS','BASE_IMMUTABILITY','METRIC_AGGREGATION','READINESS_MONOTONICITY','READINESS_DERIVATION','MC_HASH')],
        fragility_flags=['INCOMPLETE_EXECUTION'] if not all_complete else [],continuous_portfolio=False,prospective_verified=False,
        limitations=['SINGLE_PENDING_SUPPRESSES_NEW_DECISIONS','REORDER_IS_ANALYTICAL_NOT_COMPLETED_TRADES'])
    report['report_hash']=_hash(report);return report


def run_observed_mc_v2(plan,*,wf_directory,registry_path,anchor_path,evidence_directory,strategy,read_bars):
    from .observed_plan import validate_observed_plan
    plan=validate_observed_plan(plan)
    verify_observed_walkforward_trial(wf_directory,registry_path=registry_path,anchor_path=anchor_path)
    frozen=read_artifact(wf_directory,'RUN.json');receipt=read_artifact(wf_directory,'EXECUTION_RECEIPT.json');artifact=read_artifact(wf_directory,'TRIAL_RESULT.json')
    require(receipt['lifecycle_status']=='COMPLETED_VALID' and frozen['policy']['validation_plan_hash']==_hash(plan),'BASE_BINDING_INVALID')
    data,index=validate_session_coverage(frozen['dataset_manifest'],frozen['request']['contract_record'],frozen['profile'])
    tasks=schedule(plan,frozen,receipt,artifact,wf_directory);root=Path(evidence_directory);store=_Store(root);baselines=[];scenarios=[]
    def execute(name,native,rows,stress=False):
        child=root/name;child.mkdir();os.fsync(store.fd);local=copy.deepcopy(strategy)
        require(_descriptor(local)==frozen['strategy'],'STRATEGY_BINDING_INVALID')
        fn=run_stress if stress else run_observed_session
        result=fn(manifest=native,profile=native['execution_profile'],risk_limits=frozen['risk_limits'],strategy=local,params=native['params'],frame=pd.DataFrame(rows),evidence_directory=child,trial_binding=receipt['trial_binding'])
        return (verify_stress if stress else verify_observed_session)(child,expected_report_hash=result['report_hash'])
    try:
        raw=store.put('PLAN.json',plan);schedule_raw=store.put('SCHEDULE.json',tasks)
        for i,base in enumerate(receipt['windows']):
            store.check('PLAN.json',raw);store.check('SCHEDULE.json',schedule_raw)
            native=read_artifact(Path(wf_directory)/f'window-{i:04d}','NATIVE_MANIFEST.json');rows=verify_session_rows(read_bars(*base['window']['test']),index,*base['window']['test'])
            name=f'baseline-{i:04d}';a=execute(name,native,rows)
            require(a['report_hash']==base['session_evidence_hash'],'BASELINE_REPLAY_MISMATCH')
            zero=f'zero-stress-{i:04d}';b=execute(zero,_native(native,tasks[0]['windows'][i],zero=True),rows,True)
            require(_financial(root/name,a)==_financial(root/zero,b),'ZERO_STRESS_PARITY_MISMATCH')
            baselines.append(dict(directory=name,session_evidence_hash=a['report_hash'],zero_directory=zero,zero_evidence_hash=b['report_hash']))
        for task in tasks:
            items=[]
            for item in task['windows']:
                store.check('PLAN.json',raw);store.check('SCHEDULE.json',schedule_raw)
                i=item['window_id'];w=receipt['windows'][i]['window'];rows=verify_session_rows(read_bars(*w['test']),index,*w['test'])
                native=_native(read_artifact(Path(wf_directory)/f'window-{i:04d}','NATIVE_MANIFEST.json'),item)
                result=execute(item['directory'],native,rows,True);complete=_complete(result,w,data['timeframe_ms'])
                items.append(dict(directory=item['directory'],session_evidence_hash=result['report_hash'],complete=complete,native_lifecycle=result['native_result']['lifecycle_status'],
                    analytical=_analytical(root/item['directory'],result,item,plan['mc_policy']['order_perturbation']) if complete else None))
            scenario=_scenario(task,items,plan['mc_policy']);store.put(f"SCENARIO-{task['scenario_id']:04d}.json",scenario);scenarios.append(scenario)
        validate_observed_plan(plan);store.check('PLAN.json',raw);store.check('SCHEDULE.json',schedule_raw)
        report=_report(plan,artifact,baselines,scenarios);store.put('REPORT.json',report)
        return verify_observed_mc_v2(plan,wf_directory=wf_directory,evidence_directory=root,expected_report_hash=report['report_hash'])
    finally:store.close()


def verify_observed_mc_v2(plan,*,wf_directory,evidence_directory,expected_report_hash):
    from .observed_plan import validate_observed_plan
    plan=validate_observed_plan(plan);root=Path(evidence_directory);report=read_artifact(root,'REPORT.json')
    require(read_artifact(root,'PLAN.json')==plan and report['report_hash']==expected_report_hash==_hash({k:v for k,v in report.items() if k!='report_hash'}),'MC_BINDING_INVALID')
    frozen=read_artifact(wf_directory,'RUN.json');receipt=read_artifact(wf_directory,'EXECUTION_RECEIPT.json');artifact=read_artifact(wf_directory,'TRIAL_RESULT.json')
    require(frozen['policy']['validation_plan_hash']==_hash(plan) and receipt['lifecycle_status']=='COMPLETED_VALID','BASE_BINDING_INVALID')
    tasks=schedule(plan,frozen,receipt,artifact,wf_directory);require(read_artifact(root,'SCHEDULE.json')==tasks,'MC_SCHEDULE_INVALID')
    expected={'PLAN.json','SCHEDULE.json','REPORT.json'};baselines=[];scenarios=[]
    def verified(name,native,hash_value,stress):
        require(read_artifact(root/name,'MANIFEST.json')['native_manifest']==native,'MC_PROFILE_BINDING_INVALID')
        result=(verify_stress if stress else verify_observed_session)(root/name,expected_report_hash=hash_value)
        require(result['trial_binding']==receipt['trial_binding'],'MC_BINDING_INVALID');return result
    require(len(report['baselines'])==len(receipt['windows']) and len(report['scenarios'])==len(tasks),'SCENARIO_OMITTED')
    for i,base in enumerate(receipt['windows']):
        native=read_artifact(Path(wf_directory)/f'window-{i:04d}','NATIVE_MANIFEST.json');item=report['baselines'][i];name=f'baseline-{i:04d}';zero=f'zero-stress-{i:04d}';expected|={name,zero}
        a=verified(name,native,item['session_evidence_hash'],False);b=verified(zero,_native(native,tasks[0]['windows'][i],zero=True),item['zero_evidence_hash'],True)
        require(a['report_hash']==base['session_evidence_hash'] and _financial(root/name,a)==_financial(root/zero,b),'ZERO_STRESS_PARITY_MISMATCH')
        baselines.append(dict(directory=name,session_evidence_hash=a['report_hash'],zero_directory=zero,zero_evidence_hash=b['report_hash']))
    for task in tasks:
        items=[];s=task['scenario_id'];saved=read_artifact(root,f'SCENARIO-{s:04d}.json');expected.add(f'SCENARIO-{s:04d}.json')
        require(len(saved['windows'])==len(task['windows']),'SCENARIO_OMITTED')
        for item in task['windows']:
            i=item['window_id'];name=item['directory'];expected.add(name);original=read_artifact(Path(wf_directory)/f'window-{i:04d}','NATIVE_MANIFEST.json')
            result=verified(name,_native(original,item),saved['windows'][i]['session_evidence_hash'],True)
            complete=_complete(result,receipt['windows'][i]['window'],frozen['dataset_manifest']['timeframe_ms'])
            items.append(dict(directory=name,session_evidence_hash=result['report_hash'],complete=complete,native_lifecycle=result['native_result']['lifecycle_status'],
                analytical=_analytical(root/name,result,item,plan['mc_policy']['order_perturbation']) if complete else None))
        derived=_scenario(task,items,plan['mc_policy']);require(saved==derived,'MC_DERIVATION_INVALID');scenarios.append(derived)
    require({p.name for p in root.iterdir()}==expected,'SCENARIO_OMITTED')
    require(report==_report(plan,artifact,baselines,scenarios),'MC_DERIVATION_INVALID');return report
