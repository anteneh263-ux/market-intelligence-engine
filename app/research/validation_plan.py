"""Durable pre-results validation-plan artifact; no implicit root or policy defaults."""
import json
import math
import os
from pathlib import Path
import re
import stat

from .registry_inspection import _canonical, _hash, _hex
from .walkforward import _Store


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def snapshot(value):
    return json.loads(_canonical(value))


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def validate_mc_policy(value):
    p=snapshot(value)
    require(type(p) is dict and set(p)=={'master_seed','order_perturbation','cost_slippage_grid',
        'missed_fill_prob','execution_delay_bars','param_sensitivity_grid','n_scenarios','downgrade_rule'},
        'PERTURBATION_POLICY_INVALID')
    require(type(p['master_seed']) is int and 0<=p['master_seed']<2**64,'SEED_CONTRACT_INVALID')
    require(p['order_perturbation'] in ('NONE','SHUFFLE_TRADES'),'PERTURBATION_POLICY_INVALID')
    require(finite(p['missed_fill_prob']) and 0<=p['missed_fill_prob']<=1,'PERTURBATION_POLICY_INVALID')
    require(type(p['execution_delay_bars']) is int and p['execution_delay_bars']>=0,'PERTURBATION_POLICY_INVALID')
    require(type(p['cost_slippage_grid']) is list and p['cost_slippage_grid'],'PERTURBATION_POLICY_INVALID')
    for item in p['cost_slippage_grid']:
        require(type(item) is dict and set(item)=={'cost_bps','slippage_bps'} and
                all(finite(v) and v>=0 for v in item.values()),'PERTURBATION_POLICY_INVALID')
    require(type(p['param_sensitivity_grid']) is list and p['param_sensitivity_grid'] and
            all(type(v) is dict for v in p['param_sensitivity_grid']),'PERTURBATION_POLICY_INVALID')
    grid=len(p['cost_slippage_grid'])*len(p['param_sensitivity_grid'])
    require(type(p['n_scenarios']) is int and 0<p['n_scenarios']<=10000 and p['n_scenarios']%grid==0,
            'PERTURBATION_POLICY_INVALID')
    rule=p['downgrade_rule']
    require(type(rule) is dict and set(rule)=={'min_total_return','max_drawdown','min_fraction_surviving'} and
            all(finite(v) for v in rule.values()) and 0<=rule['max_drawdown']<=1 and
            0<=rule['min_fraction_surviving']<=1,'PERTURBATION_POLICY_INVALID')
    return p


def validate_plan(value):
    p=snapshot(value)
    require(type(p) is dict and set(p)=={'schema_version','strategy_id','dataset_manifest_hash',
        'cpcv','mc_policy','holdout_policy','rag_policy'} and p['schema_version']=='wp5.validation_plan.v1',
        'VALIDATION_PLAN_INVALID')
    require(type(p['strategy_id']) is str and p['strategy_id'].strip() and
            'H025' not in re.sub('[^A-Z0-9]','',p['strategy_id'].upper()) and _hex(p['dataset_manifest_hash']),
            'VALIDATION_PLAN_INVALID')
    c=p['cpcv']
    require(type(c) is dict and set(c)=={'cpcv_applicability','applicability_justification','folds_policy','pinned_lib'},
            'APPLICABILITY_INVALID')
    require(c['cpcv_applicability'] in ('NOT_APPLICABLE','REQUIRED','OPTIONAL_USED'),'APPLICABILITY_INVALID')
    if c['cpcv_applicability']=='NOT_APPLICABLE':
        require(type(c['applicability_justification']) is str and c['applicability_justification'].strip() and
                c['folds_policy'] is None and c['pinned_lib'] is None,'APPLICABILITY_INVALID')
    else:
        f=c['folds_policy'];pin=c['pinned_lib']
        require(type(f) is dict and set(f)=={'n_folds','n_test_folds','purged_size','embargo_size','embargo_ms'} and
                all(type(v) is int for v in f.values()) and 3<=f['n_folds'] and 2<=f['n_test_folds']<f['n_folds'] and
                min(f['purged_size'],f['embargo_size'],f['embargo_ms'])>=0,'ADAPTER_CONFIG_INVALID')
        require(type(pin) is dict and set(pin)=={'name','version','version_hash'} and pin['name']=='skfolio' and
                type(pin['version']) is str and pin['version'] and _hex(pin['version_hash']),'DEP_PIN_MISMATCH')
    validate_mc_policy(p['mc_policy'])
    h=p['holdout_policy']
    require(type(h) is dict and set(h) in ({'dataset_hash','dsr_min','min_total_return','max_drawdown'},
            {'dataset_hash','dsr_min','min_total_return','max_drawdown','parameter_source','warmup_policy'}) and
            _hex(h['dataset_hash']) and all(finite(h[k]) for k in ('dsr_min','min_total_return','max_drawdown')) and
            .95<=h['dsr_min']<=1 and 0<=h['max_drawdown']<=1,'HOLDOUT_POLICY_INVALID')
    if 'parameter_source' in h:
        require(h['parameter_source']=='LAST_WF_TRAIN' and h['warmup_policy']=='NONE','HOLDOUT_POLICY_INVALID')
    r=p['rag_policy']
    require(type(r) is dict and set(r)=={'corpus_hash','max_items'} and _hex(r['corpus_hash']) and
            type(r['max_items']) is int and 1<=r['max_items']<=100,'RAG_POLICY_INVALID')
    return p


def freeze_validation_plan(plan, *, evidence_directory):
    p=validate_plan(plan)
    store=_Store(evidence_directory)
    try:
        artifact=dict(schema_version='wp5.validation_plan_artifact.v1',plan=p,plan_hash=_hash(p))
        store.put('VALIDATION_PLAN.json',artifact)
        return artifact
    finally:
        store.close()


def read_artifact(directory, name):
    """Read one canonical regular artifact via an explicit nonsymlink directory."""
    root=Path(directory)
    require(root.is_absolute() and root.resolve()==root and not root.is_symlink(),'EVIDENCE_PATH_INVALID')
    require(type(name) is str and Path(name).name==name and name not in ('.','..'),'EVIDENCE_PATH_INVALID')
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        filefd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        with os.fdopen(filefd,'rb') as f:
            require(stat.S_ISREG(os.fstat(f.fileno()).st_mode),'EVIDENCE_PATH_INVALID')
            raw=f.read()
        value=json.loads(raw)
        require(_canonical(value)==raw,'EVIDENCE_NONCANONICAL')
        return value
    finally:
        os.close(fd)


def read_validation_plan(directory, *, expected_plan_hash):
    value=read_artifact(directory,'VALIDATION_PLAN.json')
    require(type(value) is dict and set(value)=={'schema_version','plan','plan_hash'} and
            value['schema_version']=='wp5.validation_plan_artifact.v1','VALIDATION_PLAN_INVALID')
    plan=validate_plan(value['plan'])
    require(_hex(expected_plan_hash) and expected_plan_hash==value['plan_hash']==_hash(plan),'VALIDATION_PLAN_MUTATED')
    return plan
