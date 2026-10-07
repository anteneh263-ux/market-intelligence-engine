import json
import pytest
from app.research.monte_carlo import run_monte_carlo,base_hash
from app.research.validation_plan import freeze_validation_plan
from app.research.registry_inspection import _hash,_canonical
from test_phase5_validation_plan import plan


def base():
    result=dict(experiment_id='a'*64,param_hash=_hash({'direction':1}),parameters={'direction':1},
        rows=[dict(open_time=i*10,close_time=(i+1)*10,close=100+i) for i in range(12)],
        positions=[1]*11+[0],cost_bps=0,slippage_bps=0,timeframe='1h')
    result['base_run_ref']=base_hash(result)
    return result


def run(root,p=None,b=None,signal=None):
    policy=plan() if p is None else p
    folder=root/'plan';folder.mkdir();out=root/'out';out.mkdir()
    artifact=freeze_validation_plan(policy,evidence_directory=folder)
    return run_monte_carlo(base() if b is None else b,plan_directory=folder,
        expected_plan_hash=artifact['plan_hash'],evidence_directory=out,
        signal=signal or (lambda history,params:params['direction']))


def test_zero_perturbation_matches_hand_base(tmp_path):
    result=run(tmp_path)
    assert result['mc_status']=='VALID' and result['readiness_effect']=='PRESERVED'
    assert result['scenarios_summary']['worst_case']==pytest.approx(.11)
    assert result['reported_scenario_count']==result['valid_generated_scenario_count']==2
    assert result['dropped_scenarios']==[]


def test_seeded_replay_exact(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    p=plan();p['mc_policy'].update(order_perturbation='SHUFFLE_TRADES',missed_fill_prob=.4,execution_delay_bars=2)
    assert run(a,p)==run(b,p)


def test_extreme_costs_are_adverse_scenarios_not_dropped(tmp_path):
    p=plan();p['mc_policy']['cost_slippage_grid']=[dict(cost_bps=20000,slippage_bps=0)]
    result=run(tmp_path,p)
    assert result['mc_status']=='VALID' and result['readiness_effect']=='DOWNGRADED'
    assert result['scenarios_summary']['worst_case']==-1
    assert result['reported_scenario_count']==2 and all(s['metrics']['ruined'] for s in result['scenarios'])


def test_all_grid_cells_reported(tmp_path):
    p=plan();p['mc_policy'].update(param_sensitivity_grid=[{},dict(direction=-1)],
        cost_slippage_grid=[dict(cost_bps=0,slippage_bps=0),dict(cost_bps=5,slippage_bps=2)],n_scenarios=8)
    result=run(tmp_path,p)
    assert result['mc_status']=='VALID'
    cells=[(s['param_grid_index'],s['cost_grid_index']) for s in result['scenarios']]
    assert all(cells.count(pair)==2 for pair in [(0,0),(0,1),(1,0),(1,1)])
    assert 'best_parameters' not in result


def test_missed_all_fills_and_delay_are_explicit(tmp_path):
    p=plan();p['mc_policy'].update(missed_fill_prob=1)
    result=run(tmp_path,p)
    assert result['mc_status']=='VALID' and result['scenarios_summary']['worst_case']==0


def test_callback_parameter_mutation_refuses(tmp_path):
    def signal(history,params):params['direction']=-1;return -1
    result=run(tmp_path,signal=signal)
    assert result['mc_status']=='INVALID' and result['reason_code']=='BASE_EXPERIMENT_MUTATION_FORBIDDEN'
    assert result['readiness_effect'] is None


def test_policy_file_mutation_refuses(tmp_path):
    called=0
    def signal(history,params):
        nonlocal called
        called+=1
        if called==12:
            path=tmp_path/'out'/'POLICY.json';p=json.loads(path.read_text());p['master_seed']=43;path.write_bytes(_canonical(p))
        return 1
    result=run(tmp_path,signal=signal)
    assert result['mc_status']=='INVALID' and result['reason_code']=='MC_POLICY_MUTATED_POST_RESULTS'
    assert result['scenarios_summary'] is None


def test_adverse_scenario_removal_refuses(tmp_path):
    import app.research.monte_carlo as module
    original=module._Store.put
    # Deletion is injected only into a synthetic test directory.
    def deleting(self,name,value):
        result=original(self,name,value)
        if name=='SCENARIO-1.json':(tmp_path/'out'/'SCENARIO-0.json').unlink()
        return result
    from unittest.mock import patch
    with patch.object(module._Store,'put',deleting):result=run(tmp_path)
    assert result['mc_status']=='INVALID' and result['scenarios_summary'] is None


def test_base_hash_tampering_precedes_any_run_write(tmp_path):
    b=base();b['rows'][1]['close']=999
    with pytest.raises(ValueError):run(tmp_path,b=b)
    assert not list((tmp_path/'out').iterdir())
