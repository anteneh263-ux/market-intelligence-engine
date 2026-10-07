import copy
import json
from pathlib import Path
import importlib.util
import pytest
from app.research.cpcv import evaluate_cpcv, applicability_hash, _native, runtime_pin


def request(applicable='NOT_APPLICABLE'):
    return dict(experiment_id='a'*64,cpcv_applicability=applicable,
        applicability_justification='Synthetic case uses separately specified walk-forward validation.',
        sample_index=[],folds_policy=None,pinned_lib=None)


def test_not_applicable_bound_and_deterministic():
    q=request();h=applicability_hash(q)
    result=evaluate_cpcv(q,frozen_applicability_hash=h)
    assert result['adapter_status']=='VALID' and result['folds']==[]
    assert result==evaluate_cpcv(q,frozen_applicability_hash=h)


@pytest.mark.parametrize('field,value',[('applicability_justification',''),('cpcv_applicability','INVALID'),
    ('cpcv_applicability','REQUIRED'),('experiment_id','b'*64)])
def test_applicability_change_refused(field,value):
    q=request();h=applicability_hash(q);q[field]=value
    assert evaluate_cpcv(q,frozen_applicability_hash=h)['reason_code']=='APPLICABILITY_INVALID'


def test_hand_calculated_native_fixture():
    policy=dict(n_folds=3,n_test_folds=2,purged_size=1,embargo_size=0,embargo_ms=0)
    folds=_native(12,policy)
    assert [f['train_idx'] for f in folds]==[[9,10,11],[5,6],[0,1,2]]
    assert [f['test_idx'] for f in folds]==[list(range(8)),[0,1,2,3,8,9,10,11],list(range(4,12))]


def required():
    q=request('REQUIRED')
    q['folds_policy']=dict(n_folds=3,n_test_folds=2,purged_size=1,embargo_size=0,embargo_ms=0)
    q['sample_index']=[dict(sample_id=str(i),feature_time_ms=i*10,label_start_ms=i*10,label_end_ms=(i+1)*10) for i in range(12)]
    q['pinned_lib']=dict(name='skfolio',version='1.0.0',version_hash='f'*64)
    return q


def test_missing_or_wrong_dependency_fails_closed():
    q=required();result=evaluate_cpcv(q,frozen_applicability_hash=applicability_hash(q))
    assert result['reason_code']=='DEP_PIN_MISMATCH' and result['folds'] is None


def test_library_mismatch_fails_closed(monkeypatch):
    import app.research.cpcv as module
    q=required();monkeypatch.setattr(module,'runtime_pin',lambda:q['pinned_lib'])
    monkeypatch.setattr(module,'_library',lambda *a:[])
    assert evaluate_cpcv(q,frozen_applicability_hash=applicability_hash(q))['reason_code']=='FIXTURE_PARITY_MISMATCH'


def test_real_pinned_library_in_explicit_runtime():
    # Separate integration command selects isolated deps through PYTHONPATH.
    # Base environment tests exercise missing-dependency refusal instead.
    if importlib.util.find_spec('skfolio') is None:
        assert evaluate_cpcv(required(),frozen_applicability_hash=applicability_hash(required()))['reason_code']=='DEP_PIN_MISMATCH'
        return
    q=required();q['pinned_lib']=json.loads((Path(__file__).resolve().parents[1]/'docs/specs/phase5-cpcv-runtime-pin-macos-py314.json').read_text())
    assert runtime_pin()==q['pinned_lib']
    result=evaluate_cpcv(q,frozen_applicability_hash=applicability_hash(q))
    assert result['adapter_status']=='VALID',result
    assert [f['train_idx'] for f in result['folds']]==[[9,10,11],[5,6],[0,1,2]]
    assert len({f['fold_id'] for f in result['folds']})==3
    assert result==evaluate_cpcv(q,frozen_applicability_hash=applicability_hash(q))
