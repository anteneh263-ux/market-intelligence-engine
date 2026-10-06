import copy
import hashlib
import json
from pathlib import Path
from importlib.metadata import PackageNotFoundError

import pandas as pd
import pytest

from app.research import fullstage_pilot as pilot
from app.research.registry_inspection import _hash

REAL_HISTORY_READER=pilot.known_related_history


@pytest.fixture(autouse=True)
def isolated_synthetic_history(monkeypatch):
    # Synthetic fixture has no real research lineage. Numeric authorities are real.
    monkeypatch.setattr(pilot,'known_related_history',lambda:[])


def prepared(tmp_path):
    source = tmp_path / 'synthetic.csv'
    start = pilot.ms('2023-01-01')
    rows = ['timestamp,open,high,low,close,volume']
    for i in range(1096):
        price = 100 * 1.001 ** i * 1.0002 ** (i % 3)
        rows.append(f'{(start+i*pilot.DAY)//1000},{price},{price*1.01},{price*.99},{price},100')
    source.write_text('\n'.join(rows)+'\n')
    return pilot.prepare_contract(source_path=source, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        evidence_directory=tmp_path/'evidence', owner='synthetic-test', created_utc_ms=pilot.ms('2026-10-06'))


def approval(c):
    return dict(schema_version='wp5.fullstage_pilot_authorization.v1', contract_hash=_hash(c),
        evidence_directory=c['evidence_directory'], approved_by=c['owner'], approval_reference='synthetic-test-only',
        approved_utc_ms=c['created_utc_ms'], prior_family_evaluations=0,
        family_history_attestation='NO_PRIOR_EVALUATIONS_IN_DEFINED_FAMILY', retrospective_holdout_acknowledged=True)


def test_prepare_does_not_create_root_or_call_strategy(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot.MovingAveragePilot, 'positions', lambda *a: pytest.fail('strategy evaluated'))
    c=prepared(tmp_path)
    assert not Path(c['evidence_directory']).exists()
    assert c['plan']['cpcv']['cpcv_applicability']=='REQUIRED'
    assert c['policy']['windows'][-1]['test'][1]==pilot.ms('2024-11-26')
    assert c['trials'][0]['request']['research_program_id']==c['trials'][1]['request']['research_program_id']
    assert c['retrospective_holdout'] and not c['unseen_holdout_claimed']


def test_signal_current_close_equality_warmup_and_prefix():
    s=pilot.MovingAveragePilot(); p=dict(target=.75,lookback=3)
    frame=pd.DataFrame({'close':[1.,1.,1.,2.,1.]})
    assert s.positions(frame,p).tolist()==[0.,0.,0.,.75,0.]
    for n in range(1,len(frame)+1):
        assert s.positions(frame.iloc[:n],p).iloc[-1]==s.positions(frame,p).iloc[n-1]
    assert s.positions(frame.iloc[3:],p).tolist()==[0.,0.]


@pytest.mark.parametrize('change',['hash','root','history','unknown','holdout','owner','bool_count'])
def test_approval_refuses_before_runtime_or_root(tmp_path,monkeypatch,change):
    c=prepared(tmp_path);a=approval(c)
    if change=='hash':a['contract_hash']='0'*64
    elif change=='root':a['evidence_directory']+='/other'
    elif change=='history':a['prior_family_evaluations']=1
    elif change=='unknown':a['family_history_attestation']='UNKNOWN'
    elif change=='holdout':a['retrospective_holdout_acknowledged']=False
    elif change=='owner':a['approved_by']='other'
    else:a['prior_family_evaluations']=False
    monkeypatch.setattr(pilot,'runtime_pin',lambda:pytest.fail('runtime reached'))
    with pytest.raises(ValueError,match='AUTHORIZATION'):pilot.run_fullstage_pilot(c,a)
    assert not Path(c['evidence_directory']).exists()


def test_changed_source_and_runtime_refuse_without_writes(tmp_path,monkeypatch):
    c=prepared(tmp_path);root=Path(c['evidence_directory']);root.mkdir()
    monkeypatch.setattr(pilot,'runtime_pin',lambda:{})
    with pytest.raises(ValueError,match='RUNTIME'):pilot.run_fullstage_pilot(c,approval(c))
    assert not list(root.iterdir())
    monkeypatch.setattr(pilot,'runtime_pin',lambda:c['plan']['cpcv']['pinned_lib'])
    with open(c['source_path'],'a') as f:f.write('\n')
    with pytest.raises(ValueError,match='SOURCE_HASH'):pilot.run_fullstage_pilot(c,approval(c))
    assert not list(root.iterdir())


def test_recomputed_approval_cannot_change_fixed_design(tmp_path,monkeypatch):
    c=prepared(tmp_path);c['plan']['holdout_policy']['dsr_min']=.5
    monkeypatch.setattr(pilot,'runtime_pin',lambda:c['plan']['cpcv']['pinned_lib'])
    with pytest.raises(ValueError,match='CONTRACT'):pilot.run_fullstage_pilot(c,approval(c))


def test_scope_does_not_reset_for_root_or_registration_time(tmp_path):
    c=prepared(tmp_path)
    d=pilot.prepare_contract(source_path=c['source_path'],source_sha256=c['source_sha256'],
        evidence_directory=tmp_path/'other',owner='other-owner',created_utc_ms=c['created_utc_ms']+1)
    assert c['research_scope']==d['research_scope']
    assert c['trials'][0]['request']['research_program_id']==d['trials'][0]['request']['research_program_id']


def test_known_real_history_blocks_even_zero_attestation_before_runtime(tmp_path,monkeypatch):
    monkeypatch.setattr(pilot,'known_related_history',REAL_HISTORY_READER)
    c=prepared(tmp_path)
    assert len(c['known_related_history'])==4
    assert {r['metadata']['strategy_id'] for r in c['known_related_history']}=={'H-013','H-026'}
    monkeypatch.setattr(pilot,'runtime_pin',lambda:pytest.fail('runtime reached'))
    with pytest.raises(ValueError,match='PRIOR_FAMILY_HISTORY_REQUIRES_RECONCILIATION'):
        pilot.run_fullstage_pilot(c,approval(c))
    assert not Path(c['evidence_directory']).exists()


def test_caller_cannot_erase_known_history_with_new_approval_hash(tmp_path,monkeypatch):
    monkeypatch.setattr(pilot,'known_related_history',REAL_HISTORY_READER)
    c=prepared(tmp_path);c['known_related_history']=[]
    monkeypatch.setattr(pilot,'runtime_pin',lambda:c['plan']['cpcv']['pinned_lib'])
    with pytest.raises(ValueError,match='PILOT_CONTRACT_CHANGED'):
        pilot.run_fullstage_pilot(c,approval(c))
    assert not Path(c['evidence_directory']).exists()


def test_nonempty_root_refuses_before_evaluation(tmp_path,monkeypatch):
    c=prepared(tmp_path);root=Path(c['evidence_directory']);root.mkdir();(root/'existing').write_text('keep')
    monkeypatch.setattr(pilot,'runtime_pin',lambda:c['plan']['cpcv']['pinned_lib'])
    monkeypatch.setattr(pilot,'run_observed_walkforward_trial',lambda *a,**kw:pytest.fail('evaluation reached'))
    with pytest.raises(FileExistsError):pilot.run_fullstage_pilot(c,approval(c))
    assert [p.name for p in root.iterdir()]==['existing']


def test_actual_synthetic_runner_and_readonly_replay(tmp_path):
    import importlib.util
    c=prepared(tmp_path);root=Path(c['evidence_directory']);root.mkdir()
    if importlib.util.find_spec('skfolio') is None:
        with pytest.raises(PackageNotFoundError):pilot.run_fullstage_pilot(c,approval(c))
        assert not list(root.iterdir())
        return
    r=pilot.run_fullstage_pilot(c,approval(c))
    assert r['N_total']==2 and r['attempt_status']=='COMPLETED',r
    assert r['full_chain_verified'] and all(s=='EVALUATED' for s in r['stages'].values())
    assert not r['promotion_authorized'] and not r['unseen_holdout_claimed']
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    verify=pilot.verify_observed_pipeline(root/'pipeline',expected_report_hash=r['pipeline_report_hash'])
    assert verify['verification_status']=='VERIFIED'
    assert before=={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(FileExistsError):pilot.run_fullstage_pilot(c,approval(c))


@pytest.mark.parametrize('kind',['flat','weak'])
def test_actual_synthetic_stops_leave_later_stages_unevaluated(tmp_path,kind):
    import importlib.util
    import random
    import math
    c=prepared(tmp_path);source=Path(c['source_path']);rows=source.read_text().splitlines()
    rng=random.Random(17);price=100.;changed=[rows[0]]
    for row in rows[1:]:
        if kind=='weak':price*=math.exp(rng.gauss(0.,.01))
        changed.append(f'{row.split(",")[0]},{price},{price*1.01},{price*.99},{price},100')
    source.write_text('\n'.join(changed)+'\n')
    c=pilot.prepare_contract(source_path=source,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        evidence_directory=c['evidence_directory'],owner=c['owner'],created_utc_ms=c['created_utc_ms'])
    root=Path(c['evidence_directory']);root.mkdir()
    if importlib.util.find_spec('skfolio') is None:
        with pytest.raises(PackageNotFoundError):pilot.run_fullstage_pilot(c,approval(c))
        assert not list(root.iterdir());return
    r=pilot.run_fullstage_pilot(c,approval(c))
    assert r['attempt_status']=='STOPPED' and not r['full_chain_verified'],r
    assert r['stages']['mc']==r['stages']['holdout']=='NOT_EVALUATED'
    import sqlite3
    db=sqlite3.connect(f'file:{root}/holdout-access/access.sqlite?mode=ro',uri=True)
    try:assert db.execute('SELECT COUNT(*) FROM access_ledger').fetchone()[0]==0
    finally:db.close()
    assert (r['stopped_at'],r['N_total'])==(('PREDECESSOR',1) if kind=='flat' else ('STATISTICS',2)),r
    if kind=='weak':assert r['reason_code']=='DSR_THRESHOLD_FAILED'
