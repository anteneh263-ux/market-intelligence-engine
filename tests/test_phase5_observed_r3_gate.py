import json
import pytest
from test_phase5_observed_pipeline import setup, bind, run, holdout_states
from app.research.observed_pipeline import verify_observed_pipeline
from app.research.registry_inspection import _hash, _canonical


def r3_case(root, survival=100.):
    c=setup(root,survival=survival)
    c[3].update(schema_version='wp5.observed_plan.v2',holdout_access_policy='AFTER_VALID_MC_RETAIN_DOWNGRADE')
    return bind(c)


def test_r3_downgrade_allows_single_holdout_and_remains_downgraded(tmp_path):
    c=r3_case(tmp_path);read=c[4]['read_holdout'];calls=[]
    def held():
        assert holdout_states(c[4]['holdout_directory'])==['RESERVED','CONSUMED']
        calls.append(1);return read()
    c[4]['read_holdout']=held;r=run(c)
    assert r['pipeline_status']=='COMPLETED',r
    assert r['schema_version']=='wp5.observed_pipeline_report.v2'
    assert r['readiness_effect']==r['mc']['readiness_effect']=='DOWNGRADED'
    assert r['holdout']['holdout_status']=='HOLDOUT_PASSED' and not r['promotion_authorized']
    assert r['phase5_acceptance_status']=='LIMITED_PROFILE' and calls==[1]
    root=c[4]['evidence_directory'];before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert verify_observed_pipeline(root,expected_report_hash=r['report_hash'])['verification_status']=='VERIFIED'
    assert before=={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()} and calls==[1]
    # Even rehashing the outer envelope cannot relabel readiness.
    r['readiness_effect']='PRESERVED';r['report_hash']=_hash({k:v for k,v in r.items() if k!='report_hash'})
    (root/'REPORT.json').write_bytes(_canonical(r))
    with pytest.raises(ValueError,match='PIPELINE_READINESS_INVALID'):
        verify_observed_pipeline(root,expected_report_hash=r['report_hash'])


def test_r3_failed_holdout_does_not_erase_downgrade(tmp_path):
    c=r3_case(tmp_path);c[3]['holdout_policy']['min_total_return']=100.;bind(c);r=run(c)
    assert r['pipeline_status']=='COMPLETED' and r['holdout']['holdout_status']=='HOLDOUT_FAILED'
    assert r['readiness_effect']=='DOWNGRADED' and not r['promotion_authorized']


def test_r3_preserved_stays_preserved(tmp_path):
    c=r3_case(tmp_path,survival=-1.);r=run(c)
    assert r['pipeline_status']=='COMPLETED' and r['readiness_effect']=='PRESERVED'
    assert not r['promotion_authorized']


def test_r3_invalid_mc_cannot_consume_holdout(tmp_path,monkeypatch):
    import app.research.observed_pipeline as module
    c=r3_case(tmp_path);original=module.run_observed_mc
    def invalid(*a,**kw):
        r=original(*a,**kw);r['mc_status']='INVALID';return r
    monkeypatch.setattr(module,'run_observed_mc',invalid)
    c[4]['read_holdout']=lambda:pytest.fail('accessed invalid MC')
    r=run(c)
    assert r['pipeline_status']=='STOPPED' and r['reason_code']=='MC_INVALID'
    assert holdout_states(c[4]['holdout_directory'])==[]


@pytest.mark.parametrize('schema,policy',[
    ('wp5.observed_plan.v1','AFTER_VALID_MC_RETAIN_DOWNGRADE'),
    ('wp5.observed_plan.v2','PRESERVED_ONLY'),
])
def test_no_implicit_policy_reinterpretation(tmp_path,schema,policy):
    c=r3_case(tmp_path);c[3].update(schema_version=schema,holdout_access_policy=policy);bind(c)
    c[4]['read_bars']=lambda a,b:pytest.fail('read before valid plan')
    with pytest.raises(ValueError,match='OBSERVED_PLAN_INVALID'):run(c)
    assert not list(c[4]['evidence_directory'].iterdir())

@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('artifact',['RUN.json','REPORT.json'])
def test_resealed_version_drift_is_rejected(tmp_path,version,artifact):
    c=r3_case(tmp_path,survival=-1.) if version==2 else setup(tmp_path)
    r=run(c);assert r['pipeline_status']=='COMPLETED'
    root=c[4]['evidence_directory'];path=root/artifact;value=json.loads(path.read_text())
    value['schema_version']='wp5.observed_pipeline_'+('run' if artifact=='RUN.json' else 'report')+f'.v{3-version}'
    expected=r['report_hash']
    if artifact=='REPORT.json':
        value['report_hash']=_hash({k:v for k,v in value.items() if k!='report_hash'});expected=value['report_hash']
    path.write_bytes(_canonical(value))
    with pytest.raises(ValueError,match='PIPELINE_VERSION_INVALID'):
        verify_observed_pipeline(root,expected_report_hash=expected)
