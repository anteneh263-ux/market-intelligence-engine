"""Preregistered historical fragility scenarios, never selection or promotion."""
import hashlib
import math
import pandas as pd

from .registry_inspection import _hash, _hex
from .validation_plan import snapshot, finite, require, read_validation_plan, read_artifact
from .walkforward import _Store
from app.strategy_research.evaluation.backtest import run_backtest


def base_hash(base):
    return _hash({k:v for k,v in base.items() if k!='base_run_ref'})


class _Random:
    """Documented SHA256 counter stream, uint256 rejection for unbiased shuffling."""
    def __init__(self, seed):
        self.seed=seed;self.counter=0
    def integer(self):
        value=int.from_bytes(hashlib.sha256(f'{self.seed}:{self.counter}'.encode('ascii')).digest(),'big')
        self.counter+=1
        return value
    def below(self,n):
        limit=2**256-(2**256%n)
        while True:
            value=self.integer()
            if value<limit:return value%n
    def missed(self,probability):
        numerator,denominator=float(probability).as_integer_ratio()
        return self.integer()*denominator<numerator*2**256


def _metrics(returns):
    equity=peak=1.;drawdown=0.;ruined=False
    for value in returns:
        require(finite(value),'AGGREGATION_INVALID')
        if ruined:continue
        if value<=-1:
            equity=0.;ruined=True
        else:
            equity*=1+value
        require(math.isfinite(equity),'AGGREGATION_INVALID')
        peak=max(peak,equity);drawdown=max(drawdown,1-equity/peak)
    return dict(total_return=equity-1,max_drawdown=drawdown,ruined=ruined)


def _validate_base(value):
    b=snapshot(value)
    require(type(b) is dict and set(b) in ({'experiment_id','param_hash','base_run_ref','parameters',
        'rows','positions','cost_bps','slippage_bps','timeframe'}, {'experiment_id','param_hash','base_run_ref','parameters',
        'rows','positions','cost_bps','slippage_bps','timeframe','warmup_rows'}),'BASE_EXPERIMENT_MUTATION_FORBIDDEN')
    require(all(_hex(b[k]) for k in ('experiment_id','param_hash','base_run_ref')) and
        type(b['parameters']) is dict and b['param_hash']==_hash(b['parameters']) and b['base_run_ref']==base_hash(b),
        'BASE_EXPERIMENT_MUTATION_FORBIDDEN')
    rows=b['rows'];positions=b['positions']
    require(type(rows) is list and len(rows)>=2 and type(positions) is list and len(positions)==len(rows),
        'PERTURBATION_POLICY_INVALID')
    last=None
    for r in rows:
        require(type(r) is dict and set(r)=={'open_time','close_time','close'} and
            type(r['open_time']) is int and type(r['close_time']) is int and r['open_time']<r['close_time'] and
            (last is None or r['open_time']==last) and finite(r['close']) and r['close']>0,
            'PERTURBATION_POLICY_INVALID')
        last=r['close_time']
    require(all(type(v) in (int,float) and v in (-1,0,1) for v in positions) and
        all(finite(b[k]) and b[k]>=0 for k in ('cost_bps','slippage_bps')) and b['timeframe'] in ('1h','4h','1d','1w'),
        'PERTURBATION_POLICY_INVALID')
    warmup=b.get('warmup_rows',[])
    require(type(warmup) is list,'PERTURBATION_POLICY_INVALID')
    previous=None
    for r in warmup+rows:
        require(type(r) is dict and set(r)=={'open_time','close_time','close'} and
                type(r['open_time']) is int and type(r['close_time']) is int and r['open_time']<r['close_time'] and
                (previous is None or previous==r['open_time']) and finite(r['close']) and r['close']>0,
                'PERTURBATION_POLICY_INVALID')
        previous=r['close_time']
    return b


def _positions(rows,params,signal,warmup=()):
    values=[]
    for i in range(len(rows)-1):
        supplied=snapshot(params)
        value=signal(snapshot(list(warmup)+rows[:i+1]),supplied)
        require(_hash(supplied)==_hash(params),'BASE_EXPERIMENT_MUTATION_FORBIDDEN')
        require(type(value) in (int,float) and value in (-1,0,1),'PERTURBATION_POLICY_INVALID')
        values.append(value)
    return values+[0]


def _returns(b,positions,cost):
    frame=pd.DataFrame(b['rows'])
    result=run_backtest(frame,pd.Series(positions),b['timeframe'],cost['cost_bps'],cost['slippage_bps'])
    return result.net_returns[1:].tolist()


def _shuffle_trades(returns,positions,rng):
    # Maximal constant-held-position blocks include flat gaps as explicit blocks.
    held=positions[:-1];blocks=[]
    for i,value in enumerate(returns):
        if i==0 or held[i]!=held[i-1]:blocks.append([])
        blocks[-1].append(value)
    for i in range(len(blocks)-1,0,-1):
        j=rng.below(i+1);blocks[i],blocks[j]=blocks[j],blocks[i]
    return [value for block in blocks for value in block]


def _quantile(values,q):
    values=sorted(values);at=(len(values)-1)*q;low=int(at);high=min(low+1,len(values)-1)
    return values[low]+(values[high]-values[low])*(at-low)


def run_monte_carlo(base, *, plan_directory, expected_plan_hash, evidence_directory, signal):
    """Consume an already frozen plan, persist every generated scenario.

    Caller must bind the base artifact to its authoritative historical result.
    This adapter validates base contents/replay but reports UPSTREAM_REQUIRED.
    No trial counter/DSR/holdout write capability is accepted.
    """
    plan=read_validation_plan(plan_directory,expected_plan_hash=expected_plan_hash)
    p=plan['mc_policy'];b=_validate_base(base);original_hash=_hash(base)
    store=_Store(evidence_directory)
    report=dict(schema_version='wp5_5.fragility.v2',experiment_id=b['experiment_id'],base_run_ref=b['base_run_ref'],
        mc_policy_hash=_hash(p),validation_plan_hash=expected_plan_hash,base_binding='UPSTREAM_REQUIRED',
        generated_scenario_count=0,valid_generated_scenario_count=0,reported_scenario_count=0,
        scenarios=[],scenarios_summary=None,readiness_effect=None,readiness_effect_derivation=None,
        dropped_scenarios=[],fragility_flags=[],mc_status='INVALID',reason_code=None)
    try:
        store.put('BASE.json',b);store.put('POLICY.json',p)
        try:
            require(_positions(b['rows'],b['parameters'],signal,b.get('warmup_rows',[]))[:-1]==b['positions'][:-1],
                    'BASE_EXPERIMENT_MUTATION_FORBIDDEN')
            baseline=_returns(b,b['positions'],{k:b[k] for k in ('cost_bps','slippage_bps')})
            require(not _metrics(baseline)['ruined'],'BASE_EXPERIMENT_MUTATION_FORBIDDEN')
            costs=p['cost_slippage_grid'];patches=p['param_sensitivity_grid'];grid=len(costs)*len(patches)
            for i in range(p['n_scenarios']):
                require(read_validation_plan(plan_directory,expected_plan_hash=expected_plan_hash)==plan,
                        'MC_POLICY_MUTATED_POST_RESULTS')
                require(read_artifact(evidence_directory,'POLICY.json')==p,'MC_POLICY_MUTATED_POST_RESULTS')
                require(read_artifact(evidence_directory,'BASE.json')==b and _hash(base)==original_hash,
                        'BASE_EXPERIMENT_MUTATION_FORBIDDEN')
                patch_index=(i%grid)//len(costs);cost_index=i%len(costs)
                params=dict(b['parameters'],**patches[patch_index])
                seed=_hash(dict(master_seed=p['master_seed'],mc_policy_hash=_hash(p),scenario_id=i,base_run_ref=b['base_run_ref']))
                rng=_Random(seed)
                positions=_positions(b['rows'],params,signal,b.get('warmup_rows',[]))
                delay=p['execution_delay_bars']
                positions=([0]*min(delay,len(positions))+positions[:max(0,len(positions)-delay)])[:len(positions)]
                held=0;executed=[]
                for target in positions:
                    if target!=held and not rng.missed(p['missed_fill_prob']):held=target
                    executed.append(held)
                returns=_returns(b,executed,costs[cost_index])
                if p['order_perturbation']=='SHUFFLE_TRADES':returns=_shuffle_trades(returns,executed,rng)
                metric=_metrics(returns);rule=p['downgrade_rule']
                survives=(not metric['ruined'] and metric['total_return']>=rule['min_total_return'] and
                          metric['max_drawdown']<=rule['max_drawdown'])
                scenario=dict(scenario_id=i,seed=seed,param_grid_index=patch_index,cost_grid_index=cost_index,
                              metrics=metric,survives=survives,net_returns=returns,returns_hash=_hash(returns))
                report['generated_scenario_count']+=1
                store.put(f'SCENARIO-{i}.json',scenario)
                report['scenarios'].append(scenario)
            require(read_validation_plan(plan_directory,expected_plan_hash=expected_plan_hash)==plan and
                    read_artifact(evidence_directory,'POLICY.json')==p,'MC_POLICY_MUTATED_POST_RESULTS')
            require(read_artifact(evidence_directory,'BASE.json')==b and _hash(base)==original_hash,
                    'BASE_EXPERIMENT_MUTATION_FORBIDDEN')
            scenarios=report['scenarios']
            require(len(scenarios)==p['n_scenarios'] and all(read_artifact(evidence_directory,f'SCENARIO-{i}.json')==v
                    for i,v in enumerate(scenarios)),'SCENARIO_OMITTED')
            metrics=[s['metrics']['total_return'] for s in scenarios]
            fraction=sum(s['survives'] for s in scenarios)/len(scenarios)
            effect='PRESERVED' if fraction>=p['downgrade_rule']['min_fraction_surviving'] else 'DOWNGRADED'
            report.update(valid_generated_scenario_count=len(scenarios),reported_scenario_count=len(scenarios),
                scenarios_summary=dict(metric_quantiles={str(q):_quantile(metrics,q) for q in (.05,.5,.95)},
                    worst_case=min(metrics),fraction_surviving=fraction),readiness_effect=effect,
                readiness_effect_derivation=dict(rule_hash=_hash(p['downgrade_rule']),recomputed=True),
                fragility_flags=['SURVIVAL_RULE_FAILED'] if effect=='DOWNGRADED' else [],mc_status='VALID')
        except (ValueError,TypeError,KeyError,OSError,OverflowError,RecursionError) as exc:
            reason=str(exc)
            known={'BASE_EXPERIMENT_MUTATION_FORBIDDEN','PERTURBATION_POLICY_INVALID','AGGREGATION_INVALID',
                   'MC_POLICY_MUTATED_POST_RESULTS','SCENARIO_OMITTED','VALIDATION_PLAN_MUTATED'}
            report['reason_code']=('MC_POLICY_MUTATED_POST_RESULTS' if reason=='VALIDATION_PLAN_MUTATED'
                                   else reason if reason in known else 'SCENARIO_NONDETERMINISTIC')
        report['mc_report_hash']=_hash(report)
        store.put('REPORT.json',report)
        return report
    finally:
        store.close()
