import copy
import json
import hashlib
from pathlib import Path
import pytest
from app.research.observed_pipeline import run_observed_pipeline,verify_observed_pipeline
from app.research.observed_plan import implementation_pins
from app.research.registry_inspection import _hash
from app.research.holdout import initialize_holdout_store
from app.research.observed_holdout import verify_observed_holdout
from test_phase5_observed_walkforward import setup as wf_setup,run as wf_run,rebind
from test_phase5_rag_provenance import corpus


def setup(root,prior=True,stress=10.,survival=-1.):
    a=root/'current';a.mkdir();case=wf_setup(a,.75)
    q,p,data,kw=case;reader=kw['read_bars'];bounds=q['contract_record']['boundaries']['holdout'];held=reader(bounds['start_ms'],bounds['end_ms'])
    holdout=root/'holdout';holdout.mkdir();initialize_holdout_store(holdout)
    prior_artifacts={}
    if prior:
        b=root/'previous';b.mkdir();old=wf_setup(b,.25)
        for k in ('registry_path','anchor_path'):old[3][k]=kw[k]
        wf_run(old);artifact=json.loads((old[3]['evidence_directory']/'TRIAL_RESULT.json').read_text());prior_artifacts[_hash(artifact)]=artifact
    sources=corpus()
    plan=dict(schema_version='wp5.observed_plan.v1',strategy_id=p['strategy_id'],dataset_manifest_hash=_hash(data),
        cpcv=dict(cpcv_applicability='NOT_APPLICABLE',applicability_justification='Synthetic fixed candidate fixture; split geometry tested separately',folds_policy=None,pinned_lib=None),
        mc_policy=dict(master_seed=42,profile_grid=[dict(taker_fee_bps=stress,slippage_bps=5.,half_spread_bps=1.)],
            repetitions=2,block_length=3,downgrade_rule=dict(min_total_return=survival,max_drawdown=1.,min_fraction_surviving=1.)),
        holdout_policy=dict(dataset_hash=_hash(held),parameter_source='LAST_WF_TRAIN',dsr_min=.95,min_total_return=-1.,max_drawdown=1.),
        rag_policy=dict(corpus_hash=_hash(sources),max_items=2),code_pins=implementation_pins())
    p.update(schema_version='wp5.observed_wf_policy.v2',validation_plan_hash=_hash(plan));rebind(case)
    kw.update(holdout_directory=holdout,read_holdout=lambda:copy.deepcopy(held),prior_result_artifacts=prior_artifacts,
        corpus=sources,advisory_query='transaction costs',actor='synthetic-observed-pipeline')
    return q,p,data,plan,kw

def run(c):return run_observed_pipeline(*c[:4],**c[4])


def test_complete_native_chain_and_readonly_verification(tmp_path):
    c=setup(tmp_path);reader=c[4]['read_holdout'];calls=[]
    def held():
        root=c[4]['evidence_directory'];assert (root/'PREHOLDOUT_VERIFICATION.json').exists()
        from app.research.holdout import _locked,_verify
        # Store is locked by evaluator; inspect committed sqlite directly read-only.
        import sqlite3
        db=sqlite3.connect(f"file:{c[4]['holdout_directory']}/access.sqlite?mode=ro",uri=True)
        try:states=[json.loads(r[0])['to_state'] for r in db.execute('SELECT row_json FROM access_ledger ORDER BY seq')]
        finally:db.close()
        assert states==['RESERVED','CONSUMED'];calls.append(1);return reader()
    c[4]['read_holdout']=held;r=run(c)
    assert r['pipeline_status']=='COMPLETED',r
    assert r['holdout']['holdout_status']=='HOLDOUT_PASSED' and r['mc']['reported_scenario_count']==2
    assert r['statistics']['N_total']==2 and calls==[1] and not r['promotion_authorized']
    root=c[4]['evidence_directory'];before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    result=verify_observed_pipeline(root,expected_report_hash=r['report_hash'])
    assert result['verification_status']=='VERIFIED' and not result['source_data_read']
    assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()} and calls==[1]


def test_single_trial_stops_before_mc_and_holdout(tmp_path):
    c=setup(tmp_path,prior=False);c[4]['read_holdout']=lambda:pytest.fail('holdout accessed')
    r=run(c);assert r['pipeline_status']=='STOPPED' and r['stopped_at']=='STATISTICS'
    assert not list(c[4]['holdout_directory'].glob('row-*'))


def test_changed_plan_before_run_has_no_trial_or_reads(tmp_path):
    c=setup(tmp_path);c[3]['mc_policy']['master_seed']+=1
    c[4]['read_bars']=lambda a,b:pytest.fail('source read')
    with pytest.raises(ValueError,match='PLAN_BINDING_INVALID'):run(c)
    assert not list(c[4]['evidence_directory'].iterdir())


def test_mc_downgrade_retains_all_scenarios_and_never_reads_holdout(tmp_path):
    c=setup(tmp_path,survival=100.);c[4]['read_holdout']=lambda:pytest.fail('holdout accessed')
    r=run(c);assert r['pipeline_status']=='STOPPED' and r['stopped_at']=='MONTE_CARLO' and r['reason_code']=='MC_DOWNGRADED'
    assert r['mc']['readiness_effect']=='DOWNGRADED' and len(r['mc']['scenarios'])==2
    assert not list(c[4]['holdout_directory'].glob('row-*'))

def bind(c):
    c[1]['validation_plan_hash']=_hash(c[3]);rebind((c[0],c[1],c[2],c[4]));return c


def test_actual_native_cost_stress_changes_returns_without_changing_parameters(tmp_path):
    c=setup(tmp_path,stress=50.);r=run(c)
    assert r['pipeline_status']=='COMPLETED',r
    mc=r['mc'];assert mc['baselines'][0]['net_returns']!=mc['executions'][0][0]['net_returns']
    root=c[4]['evidence_directory'];base=json.loads((root/'wf'/'window-0001'/'PARAMETERS.json').read_text())
    assert r['holdout']['execution_evidence_hash']
    native=json.loads((root/'holdout-execution'/'session'/'MANIFEST.json').read_text())['native_manifest']
    assert native['params']==base['parameters'] and native['execution_profile']==c[4]['profile']
    assert mc['scenarios'][0]['windows'][0]['evidence_kind']=='ANALYTICAL_BOOTSTRAP'


@pytest.mark.parametrize('mutation',['missing','duplicate','changed'])
def test_mc_artifact_tamper_stops_before_access(tmp_path,monkeypatch,mutation):
    import app.research.observed_pipeline as module
    c=setup(tmp_path);original=module.run_observed_mc
    def tampered(*a,**kw):
        report=original(*a,**kw);folder=Path(kw['evidence_directory']);path=folder/'SCENARIO-0000.json'
        if mutation=='missing':path.unlink()
        elif mutation=='duplicate':path.write_bytes((folder/'SCENARIO-0001.json').read_bytes())
        else:
            from app.research.registry_inspection import _canonical
            value=json.loads(path.read_text());value['windows'][0]['indexes'][0]+=1;path.write_bytes(_canonical(value))
        return report
    monkeypatch.setattr(module,'run_observed_mc',tampered);c[4]['read_holdout']=lambda:pytest.fail('holdout accessed')
    r=run(c);assert r['pipeline_status']=='STOPPED' and r['stopped_at']=='HOLDOUT'
    assert not list(c[4]['holdout_directory'].glob('row-*'))


def test_changed_execution_behavior_cannot_pass_baseline_replay(tmp_path,monkeypatch):
    import app.research.observed_pipeline as module
    c=setup(tmp_path);original=module.run_observed_mc;cls=type(c[4]['strategy']);positions=cls.positions
    def changed(*a,**kw):
        monkeypatch.setattr(cls,'positions',lambda self,frame,params:positions(self,frame,{'target':.1}))
        return original(*a,**kw)
    monkeypatch.setattr(module,'run_observed_mc',changed);c[4]['read_holdout']=lambda:pytest.fail('holdout accessed')
    r=run(c);assert r['pipeline_status']=='STOPPED' and r['stopped_at']=='MONTE_CARLO' and r['reason_code']=='BASELINE_REPLAY_MISMATCH'
    assert not list(c[4]['holdout_directory'].glob('row-*'))


def test_required_cpcv_real_pin_or_explicit_refusal(tmp_path):
    import importlib.util
    c=setup(tmp_path);pin=json.loads((Path(__file__).resolve().parents[1]/'docs/specs/phase5-cpcv-runtime-pin-macos-py314.json').read_text())
    c[3]['cpcv']=dict(cpcv_applicability='REQUIRED',applicability_justification='Synthetic full pipeline requires split geometry',
        folds_policy=dict(n_folds=3,n_test_folds=2,purged_size=1,embargo_size=0,embargo_ms=0),pinned_lib=pin)
    bind(c);r=run(c)
    if importlib.util.find_spec('skfolio') is None:
        assert r['pipeline_status']=='STOPPED' and r['stopped_at']=='CPCV' and r['reason_code']=='DEP_PIN_MISMATCH'
    else:
        assert r['pipeline_status']=='COMPLETED',r
        assert len(r['cpcv']['folds'])==3 and r['cpcv']['adapter_provenance']==pin


def test_failed_holdout_is_completed_evaluation_without_promotion(tmp_path):
    c=setup(tmp_path);c[3]['holdout_policy']['min_total_return']=100.;bind(c);r=run(c)
    assert r['pipeline_status']=='COMPLETED' and r['holdout']['holdout_status']=='HOLDOUT_FAILED'
    assert not r['promotion_authorized'] and r['phase5_acceptance_status']=='LIMITED_PROFILE'


def holdout_states(root):
    import sqlite3
    db=sqlite3.connect(f'file:{root}/access.sqlite?mode=ro',uri=True)
    try:return [json.loads(r[0])['to_state'] for r in db.execute('SELECT row_json FROM access_ledger ORDER BY seq')]
    finally:db.close()

@pytest.mark.parametrize('failure',['before_complete','report_publish'])
def test_holdout_crash_preserves_consumption_and_cannot_reexecute(tmp_path,monkeypatch,failure):
    import app.research.observed_holdout as module
    c=setup(tmp_path)
    if failure=='before_complete':
        original=module.access._append
        def broken(conn,store,q,state,**kw):
            if state=='COMPLETED':raise OSError('injected completion interruption')
            return original(conn,store,q,state,**kw)
        monkeypatch.setattr(module.access,'_append',broken)
    else:
        original=module.access._Directory.put
        def broken(self,name,value):
            if name.startswith('observed-report-'):raise OSError('injected publication interruption')
            return original(self,name,value)
        monkeypatch.setattr(module.access._Directory,'put',broken)
    r=run(c);assert r['pipeline_status']=='STOPPED' and r['stopped_at']=='HOLDOUT'
    assert holdout_states(c[4]['holdout_directory'])==(['RESERVED','CONSUMED'] if failure=='before_complete' else ['RESERVED','CONSUMED','COMPLETED'])
    from app.research.observed_pipeline import _holdout_request
    from app.research.validation_plan import read_artifact
    root=c[4]['evidence_directory'];q=_holdout_request(read_artifact(root,'RUN.json'),root)
    with pytest.raises(ValueError,match='HOLDOUT_ALREADY_CONSUMED'):
        module.run_observed_holdout(q,directory=c[4]['holdout_directory'],verify_prerequisites=lambda q:None,
            read_data=lambda:pytest.fail('reopened'),evaluate=lambda a,b:pytest.fail('reexecuted'))


def test_anchored_execution_receipt_tamper_refuses_readonly_replay(tmp_path):
    from app.research.registry_inspection import _canonical
    c=setup(tmp_path);r=run(c);assert r['pipeline_status']=='COMPLETED'
    root=c[4]['evidence_directory'];path=root/'holdout-execution'/'RECEIPT.json';value=json.loads(path.read_text());value['session_evidence_hash']='f'*64;path.write_bytes(_canonical(value))
    with pytest.raises(ValueError,match='HOLDOUT_EXECUTION_MISMATCH'):
        verify_observed_pipeline(root,expected_report_hash=r['report_hash'])

def test_holdout_core_anchors_preaccess_proof(tmp_path):
    c=setup(tmp_path);r=run(c);assert r['pipeline_status']=='COMPLETED'
    root=c[4]['evidence_directory'];receipt=json.loads((root/'holdout-execution'/'RECEIPT.json').read_text())
    proof=json.loads((root/'PREHOLDOUT_VERIFICATION.json').read_text())
    assert receipt['prerequisites_hash']==_hash(proof)

@pytest.mark.parametrize('legacy_first',[True,False])
def test_holdout_uniqueness_spans_legacy_and_observed_cores(tmp_path,legacy_first):
    from app.research.holdout import run_holdout,verify_holdout_report,reservation_key
    from app.research.observed_holdout import run_observed_holdout
    initialize_holdout_store(tmp_path)
    data={'net':[.01,-.005,.02]*10}
    q=dict(strategy_identity='a'*64,research_program_id='b'*64,experiment_id='c'*64,trial_id='d'*64,holdout_dataset_hash=_hash(data),
        parameters={'x':1},param_hash=_hash({'x':1}),decision_rule=dict(min_total_return=0.,max_drawdown=1.),actor='synthetic')
    args=dict(directory=tmp_path,verify_prerequisites=lambda q:None,read_data=lambda:data)
    if legacy_first:
        run_holdout(q,**args,evaluate=lambda data,p:data['net'])
        with pytest.raises(ValueError,match='HOLDOUT_ALREADY_CONSUMED'):
            run_observed_holdout(dict(q,experiment_id='e'*64),directory=tmp_path,verify_prerequisites=lambda q:None,
                read_data=lambda:pytest.fail('second read'),evaluate=lambda a,b:pytest.fail('second evaluation'))
    else:
        run_observed_holdout(q,**args,evaluate=lambda data,p:dict(net_returns=data['net'],execution_evidence_hash='f'*64))
        report=run_holdout(dict(q,experiment_id='e'*64),directory=tmp_path,verify_prerequisites=lambda q:None,
            read_data=lambda:pytest.fail('second read'),evaluate=lambda a,b:pytest.fail('second evaluation'))
        assert report['reason_code']=='HOLDOUT_ALREADY_CONSUMED'
        with pytest.raises((ValueError,OSError)):verify_holdout_report(tmp_path,reservation_key(q))


def test_dangling_reserved_is_not_recovered_or_reused(tmp_path,monkeypatch):
    import app.research.observed_holdout as module
    initialize_holdout_store(tmp_path);data={'net':[.01]*30}
    q=dict(strategy_identity='a'*64,research_program_id='b'*64,experiment_id='c'*64,trial_id='d'*64,holdout_dataset_hash=_hash(data),
        parameters={'x':1},param_hash=_hash({'x':1}),decision_rule=dict(min_total_return=0.,max_drawdown=1.),actor='synthetic')
    original=module.access._append
    def fail(conn,store,q,state,**kw):
        if state=='CONSUMED':raise OSError('interrupted before consumption')
        return original(conn,store,q,state,**kw)
    monkeypatch.setattr(module.access,'_append',fail)
    kw=dict(directory=tmp_path,verify_prerequisites=lambda q:None,read_data=lambda:pytest.fail('opened'),evaluate=lambda a,b:pytest.fail('evaluated'))
    with pytest.raises(OSError):module.run_observed_holdout(q,**kw)
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(ValueError):module.run_observed_holdout(q,**kw)
    assert holdout_states(tmp_path)==['RESERVED'] and before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}
