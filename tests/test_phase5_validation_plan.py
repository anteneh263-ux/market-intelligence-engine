import json
import pytest
from app.research.validation_plan import freeze_validation_plan,read_validation_plan,validate_plan


def plan():
    return dict(schema_version='wp5.validation_plan.v1',strategy_id='SYNTHETIC',dataset_manifest_hash='a'*64,
        cpcv=dict(cpcv_applicability='NOT_APPLICABLE',applicability_justification='Synthetic fixture uses rolling validation.',folds_policy=None,pinned_lib=None),
        mc_policy=dict(master_seed=42,order_perturbation='NONE',cost_slippage_grid=[dict(cost_bps=0,slippage_bps=0)],
            missed_fill_prob=0,execution_delay_bars=0,param_sensitivity_grid=[{}],n_scenarios=2,
            downgrade_rule=dict(min_total_return=0,max_drawdown=.5,min_fraction_surviving=1)),
        holdout_policy=dict(dataset_hash='b'*64,dsr_min=.95,min_total_return=0,max_drawdown=.5),
        rag_policy=dict(corpus_hash='c'*64,max_items=5))


def test_freeze_read_and_no_replacement(tmp_path):
    p=plan();artifact=freeze_validation_plan(p,evidence_directory=tmp_path)
    assert read_validation_plan(tmp_path,expected_plan_hash=artifact['plan_hash'])==p
    with pytest.raises(FileExistsError):freeze_validation_plan(p,evidence_directory=tmp_path)


def test_mutation_detected(tmp_path):
    artifact=freeze_validation_plan(plan(),evidence_directory=tmp_path)
    path=tmp_path/'VALIDATION_PLAN.json';body=json.loads(path.read_text());body['plan']['mc_policy']['master_seed']=43
    from app.research.registry_inspection import _canonical
    path.write_bytes(_canonical(body))
    with pytest.raises(ValueError):read_validation_plan(tmp_path,expected_plan_hash=artifact['plan_hash'])


@pytest.mark.parametrize('change',[lambda p:p.update(strategy_id='H-025'),
    lambda p:p['mc_policy'].update(master_seed=True),lambda p:p['mc_policy'].update(n_scenarios=0),
    lambda p:p['holdout_policy'].update(dsr_min=.9),lambda p:p['cpcv'].update(applicability_justification=''),
    lambda p:p['mc_policy']['downgrade_rule'].update(max_drawdown=float('nan'))])
def test_invalid_plan_never_writes(tmp_path,change):
    p=plan();change(p)
    with pytest.raises(ValueError):freeze_validation_plan(p,evidence_directory=tmp_path)
    assert not list(tmp_path.iterdir())


def test_symlink_read_refused(tmp_path):
    artifact=freeze_validation_plan(plan(),evidence_directory=tmp_path)
    path=tmp_path/'VALIDATION_PLAN.json';other=tmp_path/'copy';path.rename(other);path.symlink_to(other)
    with pytest.raises(OSError):read_validation_plan(tmp_path,expected_plan_hash=artifact['plan_hash'])
