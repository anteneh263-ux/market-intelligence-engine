"""Preregistered bounded observed pipeline profile; new policies never inferred."""
import hashlib
import importlib
from pathlib import Path
from .registry_inspection import _hash,_hex
from .validation_plan import snapshot,require,finite


def implementation_pins():
    names=('app.research.observed_plan','app.research.observed_pipeline','app.research.observed_mc','app.research.observed_mc_v2','app.research.stress_session','app.paper.session.stress_engine_v1','app.paper.session.stress_policy','app.research.observed_holdout',
        'app.research.observed_walkforward','app.research.observed_wf_reporting','app.strategy_research.evaluation.observed_walkforward',
        'app.research.observed_session','app.research.session_coverage','app.research.holdout','app.research.monte_carlo',
        'app.research.cpcv','app.research.statistics_report','app.research.bound_statistics','app.research.statistics_math',
        'app.research.trial_registry_v2','app.research.timing_rows','app.paper.session.observed_engine_v1',
        'app.paper.session.broker','app.paper.session.manifest','app.paper.session.profiles','app.paper.session.risk',
        'app.paper.session.sanitizer','app.paper.session.ingest','app.paper.session.journal')
    return {n:hashlib.sha256(Path(importlib.import_module(n).__file__).read_bytes()).hexdigest() for n in names}


def validate_observed_plan(value):
    p=snapshot(value)
    require(type(p) is dict, 'OBSERVED_PLAN_INVALID')
    version=p.get('schema_version')
    fields={'schema_version','strategy_id','dataset_manifest_hash','cpcv','mc_policy','holdout_policy','rag_policy','code_pins'}
    if version in ('wp5.observed_plan.v2','wp5.observed_plan.v3'):
        fields.add('holdout_access_policy')
        require(p.get('holdout_access_policy')=='AFTER_VALID_MC_RETAIN_DOWNGRADE','OBSERVED_PLAN_INVALID')
    require(set(p)==fields and version in ('wp5.observed_plan.v1','wp5.observed_plan.v2','wp5.observed_plan.v3') and _hex(p['dataset_manifest_hash']) and p['code_pins']==implementation_pins(), 'OBSERVED_PLAN_INVALID')
    require(type(p['strategy_id']) is str and p['strategy_id'],'OBSERVED_PLAN_INVALID')
    c=p['cpcv']
    require(type(c) is dict and set(c)=={'cpcv_applicability','applicability_justification','folds_policy','pinned_lib'} and
        c['cpcv_applicability'] in ('REQUIRED','OPTIONAL_USED','NOT_APPLICABLE'),'APPLICABILITY_INVALID')
    if c['cpcv_applicability']=='NOT_APPLICABLE':
        require(type(c['applicability_justification']) is str and c['applicability_justification'].strip() and c['folds_policy'] is None and c['pinned_lib'] is None,'APPLICABILITY_INVALID')
    else:
        f=c['folds_policy'];pin=c['pinned_lib']
        require(type(f) is dict and set(f)=={'n_folds','n_test_folds','purged_size','embargo_size','embargo_ms'} and all(type(v) is int for v in f.values()) and
            3<=f['n_folds'] and 2<=f['n_test_folds']<f['n_folds'] and min(f['purged_size'],f['embargo_size'],f['embargo_ms'])>=0 and
            type(pin) is dict and set(pin)=={'name','version','version_hash'} and pin['name']=='skfolio' and _hex(pin['version_hash']),'ADAPTER_CONFIG_INVALID')
    m=p['mc_policy']
    if version=='wp5.observed_plan.v3':
        from .observed_mc_v2 import validate_policy
        validate_policy(m)
    else:
        require(type(m) is dict and set(m)=={'master_seed','profile_grid','repetitions','block_length','downgrade_rule'} and
            type(m['master_seed']) is int and 0<=m['master_seed']<2**64 and type(m['repetitions']) is int and m['repetitions']>0 and
            type(m['block_length']) is int and m['block_length']>0 and type(m['profile_grid']) is list and m['profile_grid'] and
            len(m['profile_grid'])*m['repetitions']<=10000,'MC_POLICY_INVALID')
        require(len({_hash(v) for v in m['profile_grid']})==len(m['profile_grid']),'MC_POLICY_INVALID')
        for v in m['profile_grid']:
            require(type(v) is dict and set(v)=={'taker_fee_bps','slippage_bps','half_spread_bps'} and all(finite(n) and n>=0 for n in v.values()),'MC_POLICY_INVALID')
        rule=m['downgrade_rule']
        require(type(rule) is dict and set(rule)=={'min_total_return','max_drawdown','min_fraction_surviving'} and all(finite(v) for v in rule.values()) and
            0<=rule['max_drawdown']<=1 and 0<=rule['min_fraction_surviving']<=1,'MC_POLICY_INVALID')
    h=p['holdout_policy']
    require(type(h) is dict and set(h)=={'dataset_hash','parameter_source','dsr_min','min_total_return','max_drawdown'} and _hex(h['dataset_hash']) and
        h['parameter_source']=='LAST_WF_TRAIN' and h['dsr_min']==.95 and finite(h['min_total_return']) and finite(h['max_drawdown']) and 0<=h['max_drawdown']<=1,'HOLDOUT_POLICY_INVALID')
    r=p['rag_policy']
    require(type(r) is dict and set(r)=={'corpus_hash','max_items'} and _hex(r['corpus_hash']) and type(r['max_items']) is int and 1<=r['max_items']<=100,'RAG_POLICY_INVALID')
    return p
