import copy
import hashlib
import importlib.util
from importlib.metadata import PackageNotFoundError
import json
import math
from pathlib import Path
import random
import sqlite3
import pytest
from app.research import diagnostic_pilot as d
from app.research import fullstage_pilot as scientific
from app.research.registry_inspection import _hash,_canonical
from test_phase5_fullstage_pilot import prepared as source_fixture


def prepared(tmp_path,kind='strong'):
    seed=source_fixture(tmp_path);source=Path(seed['source_path'])
    if kind!='strong':
        rows=source.read_text().splitlines();rng=random.Random(17);price=100.;out=[rows[0]]
        for row in rows[1:]:
            if kind=='weak':price*=math.exp(rng.gauss(0.,.01))
            out.append(f'{row.split(",")[0]},{price},{price*1.01},{price*.99},{price},100')
        source.write_text('\n'.join(out)+'\n')
    return d.prepare_contract(source_path=source,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        evidence_directory=tmp_path/'diagnostic',owner='synthetic-test',created_utc_ms=seed['created_utc_ms'])


def approval(c):
    return dict(schema_version=d.AUTH_SCHEMA,contract_hash=_hash(c),evidence_directory=c['evidence_directory'],
        approved_by=c['owner'],approved_utc_ms=c['created_utc_ms'],approval_reference='synthetic-test-only',
        scope='TECHNICAL_DIAGNOSTIC_ONLY',unresolved_history_acknowledged=True,retrospective_access_probe_acknowledged=True)


def states(root):
    conn=sqlite3.connect(f'file:{root}/holdout-access/access.sqlite?mode=ro',uri=True)
    try:return [json.loads(r[0])['to_state'] for r in conn.execute('SELECT row_json FROM access_ledger ORDER BY seq')]
    finally:conn.close()


def test_prepare_keeps_history_and_creates_no_evidence(tmp_path,monkeypatch):
    monkeypatch.setattr(scientific.MovingAveragePilot,'positions',lambda *a:pytest.fail('evaluated'))
    c=prepared(tmp_path)
    assert c['known_related_history'] and c['authority']==d.AUTHORITY
    assert not Path(c['evidence_directory']).exists()
    assert c['research_scope']['evaluation_objective_hash']==_hash('TECHNICAL_INTEGRATION_FIXTURE_ONLY_V1')
    assert c['trials'][1]['target']==.75


@pytest.mark.parametrize('field',['contract_hash','scope','unresolved_history_acknowledged','retrospective_access_probe_acknowledged'])
def test_authorization_refuses_before_runtime_and_writes(tmp_path,monkeypatch,field):
    c=prepared(tmp_path);a=approval(c);a[field]=False
    monkeypatch.setattr(d,'runtime_pin',lambda:pytest.fail('runtime reached'))
    with pytest.raises(ValueError,match='AUTHORIZATION'):d.run_diagnostic(c,a)
    assert not Path(c['evidence_directory']).exists()


def test_changed_authority_refuses_even_new_approval_hash(tmp_path,monkeypatch):
    c=prepared(tmp_path);c['authority']['scientific_acceptance']='PASSED'
    monkeypatch.setattr(d,'runtime_pin',lambda:pytest.fail('runtime reached'))
    with pytest.raises(ValueError,match='AUTHORITY'):d.run_diagnostic(c,approval(c))


def test_old_scientific_runner_still_blocks_known_history(tmp_path):
    c=source_fixture(tmp_path)
    from test_phase5_fullstage_pilot import approval as old_approval
    with pytest.raises(ValueError,match='PRIOR_FAMILY_HISTORY'):scientific.run_fullstage_pilot(c,old_approval(c))


@pytest.mark.parametrize('kind',['strong','weak','flat'])
def test_native_chain_local_dsr_is_not_scientific_acceptance(tmp_path,monkeypatch,kind):
    c=prepared(tmp_path,kind);root=Path(c['evidence_directory']);root.mkdir()
    if importlib.util.find_spec('skfolio') is None:
        with pytest.raises(PackageNotFoundError):d.run_diagnostic(c,approval(c))
        assert not list(root.iterdir());return
    original=d._source;reads=[]
    def guarded(*a):
        if (root/'PREHOLDOUT_VERIFICATION.json').exists():
            assert states(root)==['RESERVED','CONSUMED'];reads.append('consumed-before-read')
        return original(*a)
    monkeypatch.setattr(d,'_source',guarded)
    r=d.run_diagnostic(c,approval(c));assert r['authority']==d.AUTHORITY
    assert r['complete_family_N'] is None and not r['promotion_authorized']
    if kind=='flat':
        assert r['technical_status']=='STOPPED' and r['stopped_at']=='WF_1' and r['local_reservations']==1
        assert states(root)==[] and not reads;return
    assert r['technical_status']=='VERIFIED' and r['local_reservations']==2,r
    assert reads==['consumed-before-read'] and states(root)==['RESERVED','CONSUMED','COMPLETED']
    if kind=='weak':assert r['local_statistics']['decision_input']['dsr_pass'] is False
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    v=d.verify_diagnostic(root,expected_contract_hash=_hash(c),expected_report_hash=r['report_hash'])
    assert v['scientific_acceptance']=='UNRESOLVED' and not v['source_data_read']
    assert before=={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    for field,value in [('complete_family_N',2),('promotion_authorized',True),('authority',dict(d.AUTHORITY,scientific_acceptance='PASSED'))]:
        bad=copy.deepcopy(r);bad[field]=value;bad['report_hash']=_hash({k:v for k,v in bad.items() if k!='report_hash'})
        (root/'REPORT.json').write_bytes(_canonical(bad))
        with pytest.raises(ValueError):d.verify_diagnostic(root,expected_contract_hash=_hash(c),expected_report_hash=bad['report_hash'])
    (root/'REPORT.json').write_bytes(_canonical(r))
    from app.research.observed_pipeline import verify_observed_pipeline
    with pytest.raises((ValueError,KeyError)):verify_observed_pipeline(root,expected_report_hash=r['report_hash'])
    monkeypatch.setattr(d,'_source',original)
    with pytest.raises(FileExistsError):d.run_diagnostic(c,approval(c))


@pytest.mark.parametrize('failure',['tampered_mc','after_consumption','receipt_schema','statistics_binding'])
def test_integrity_and_consumption_failures_preserve_stop(tmp_path,monkeypatch,failure):
    c=prepared(tmp_path);root=Path(c['evidence_directory']);root.mkdir()
    if importlib.util.find_spec('skfolio') is None:
        with pytest.raises(PackageNotFoundError):d.run_diagnostic(c,approval(c))
        return
    original=d.run_observed_mc_v2
    def corrupt(*a,**kw):
        result=original(*a,**kw);(Path(kw['evidence_directory'])/'SCENARIO-0000.json').unlink();return result
    if failure=='tampered_mc':monkeypatch.setattr(d,'run_observed_mc_v2',corrupt)
    elif failure=='after_consumption':
        def interrupted(*args):raise OSError('injected interruption after consumption')
        monkeypatch.setattr(d,'_evaluate_probe',interrupted)
    elif failure=='receipt_schema':
        evaluate=d._evaluate_probe
        def resealed(*args):
            result=evaluate(*args);path=root/'holdout-execution'/'RECEIPT.json';receipt=json.loads(path.read_text())
            receipt['schema_version']='wp5.observed_holdout_execution.v1';path.write_bytes(_canonical(receipt))
            result['execution_evidence_hash']=_hash(receipt);return result
        monkeypatch.setattr(d,'_evaluate_probe',resealed)
    else:
        statistics=d._statistics
        def unbound(*args):
            report=statistics(*args);report['ledger_binding']='REFUSED';return report
        monkeypatch.setattr(d,'_statistics',unbound)
    r=d.run_diagnostic(c,approval(c))
    assert r['technical_status']=='STOPPED' and r['stopped_at']==('LOCAL_STATISTICS' if failure=='statistics_binding' else 'ACCESS_PROBE')
    expected=[] if failure in ('tampered_mc','statistics_binding') else ['RESERVED','CONSUMED']
    if failure=='receipt_schema':expected.append('COMPLETED')
    assert states(root)==expected
    if failure=='receipt_schema':assert r['reason_code']=='DIAGNOSTIC_ACCESS_SCHEMA_INVALID'
    if failure=='statistics_binding':assert r['reason_code']=='DIAGNOSTIC_STATISTICS_BINDING_INVALID'
    with pytest.raises(FileExistsError):d.run_diagnostic(c,approval(c))
