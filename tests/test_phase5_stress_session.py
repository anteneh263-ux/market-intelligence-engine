import copy
import json
import pytest
from test_phase5_observed_session import fixture,binding
from app.research.registry_inspection import _hash


def policy(delay=0,prob=0.):
    return dict(schema_version='paper.stress_policy.v1',seed='a'*64,execution_delay_bars=delay,missed_fill_probability=prob,
        decision_policy='SINGLE_PENDING',safe_exit_policy='SAME_DELAY_AND_MISS_ONCE',broker_error_policy='FAIL_CLOSED')


def stress(root,delay=0,prob=0.,args=None):
    from app.research.stress_session import run_observed_session,stress_manifest
    args=copy.deepcopy(args or fixture());args['manifest']=stress_manifest(args['manifest'],policy(delay,prob))
    return run_observed_session(**args,evidence_directory=root.resolve(),trial_binding=binding())


def test_zero_stress_native_financial_parity(tmp_path):
    from app.research.observed_session import run_observed_session
    a=tmp_path/'a';a.mkdir();b=tmp_path/'b';b.mkdir()
    baseline=run_observed_session(**fixture(),evidence_directory=a.resolve(),trial_binding=binding());r=stress(b)
    assert baseline['equity_points']==r['equity_points'] and baseline['net_returns']==r['net_returns']
    assert baseline['native_result']['broker']==r['native_result']['broker']
    def orders(path):return [json.loads(x)['payload'] for x in (path/'journal'/'journal.jsonl').read_text().splitlines() if json.loads(x)['event_type']=='order']
    assert orders(a)==orders(b)


def test_delay_changes_actual_submission_time_and_suppresses_decisions(tmp_path):
    r=stress(tmp_path,delay=2)
    rows=r['audit']['timing_rows'];assert rows
    assert all(v['order_submitted_ms']==v['bar_available_ms']+2*86400000 for v in rows)
    assert r['audit']['accepted_decisions']==2


def test_miss_all_keeps_actual_balances_and_costs_unchanged(tmp_path):
    r=stress(tmp_path,prob=1.);b=r['native_result']['broker']
    assert b['cash']==100000. and b['position_units']==0 and b['fees_paid']==b['spread_cost']==b['slippage_cost']==0
    assert r['audit']['completed_timing_rows']==0 and r['audit']['decision_status_counts']['REJECTED']==4
    assert r['net_returns']==[0.]*4


def test_seeded_stress_repeats_and_verifies_without_mutation(tmp_path):
    from app.research.stress_session import verify_observed_session
    a=tmp_path/'a';a.mkdir();b=tmp_path/'b';b.mkdir();r=stress(a,delay=1,prob=.5);other=stress(b,delay=1,prob=.5)
    assert r==other
    before={str(p):p.read_bytes() for p in a.rglob('*') if p.is_file()}
    assert verify_observed_session(a,expected_report_hash=r['report_hash'])==r
    assert before=={str(p):p.read_bytes() for p in a.rglob('*') if p.is_file()}


def test_invalid_policy_refuses_before_evidence(tmp_path):
    from app.research.stress_session import stress_manifest
    p=policy();p['execution_delay_bars']=True
    with pytest.raises(ValueError,match='STRESS_POLICY_INVALID'):stress_manifest(fixture()['manifest'],p)
    assert not list(tmp_path.iterdir())


def test_risk_cancels_waiting_strategy_and_delays_safe_exit(tmp_path):
    args=fixture(target=.99,closes=[100.,105.,110.,115.,1.1,1.1,1.1,1.1,1.1,1.1])
    r=stress(tmp_path,delay=2,args=args)
    assert r['native_result']['lifecycle_status']=='RISK_STOPPED'
    assert r['audit']['decision_status_counts']['CANCELLED_BY_RISK']==1
    assert r['native_result']['counts']['safe_exits']==1 and r['native_result']['broker']['position_units']==0
    risk=[d for d in r['audit']['decisions'] if d['source']=='RISK'];assert len(risk)==1 and risk[0]['status']=='FILLED'
    row=next(t for t in r['audit']['timing_rows'] if t['bar_open_ms']==risk[0]['bar_open_ms'])
    assert row['fill_ms']==row['bar_available_ms']+2*86400000


def test_missed_safe_exit_is_single_attempt_with_remaining_position(tmp_path):
    from app.paper.session.stress_policy import missed
    from app.research.stress_session import run_observed_session,stress_manifest
    args=fixture(target=.99,closes=[100.,105.,110.,115.,1.1,1.1,1.1,1.1,1.1,1.1]);start=int(args['frame'].iloc[0]['open_time']);d=86400000
    p=policy(2,.5)
    # Choose the deterministic fixture seed before executing any performance.
    for i in range(1000):
        p['seed']=_hash({'fixture_seed':i})
        if not missed(p,f'strategy:{start}',start+3*d) and missed(p,f'risk:{start+4*d}',start+7*d):break
    else:pytest.fail('fixture seed missing')
    args['manifest']=stress_manifest(args['manifest'],p)
    r=run_observed_session(**args,evidence_directory=tmp_path.resolve(),trial_binding=binding())
    assert r['native_result']['lifecycle_status']=='RISK_STOPPED' and r['native_result']['counts']['safe_exits']==1
    risk=[x for x in r['audit']['decisions'] if x['source']=='RISK'];assert len(risk)==1 and risk[0]['status']=='REJECTED'
    assert r['native_result']['broker']['position_units']>0


def test_broker_error_fails_closed_without_retry(tmp_path,monkeypatch):
    from app.paper.session import stress_engine_v1 as engine
    calls=[]
    def fail(*a,**k):calls.append(1);raise engine.BrokerError('fixture failure')
    monkeypatch.setattr(engine.PaperBroker,'execute',fail)
    r=stress(tmp_path)
    assert calls==[1] and r['native_result']['lifecycle_status']=='DATA_STOPPED'
    assert r['native_result']['counts']['execution_errors']==1
    assert r['native_result']['broker']['cash']==100000.


@pytest.mark.parametrize('mutation',['delay','miss','cancel'])
def test_altered_schedule_semantics_are_refused(tmp_path,mutation):
    from app.research.stress_session import _verify_stress
    args=fixture(target=.99,closes=[100.,105.,110.,115.,1.1,1.1,1.1,1.1,1.1,1.1]);stress(tmp_path,delay=2,args=args)
    native=json.loads((tmp_path/'MANIFEST.json').read_text())['native_manifest']
    events=[json.loads(p.read_text()) for p in sorted(tmp_path.glob('event-*.json'))]
    journal=[json.loads(line) for line in (tmp_path/'journal'/'journal.jsonl').read_text().splitlines()]
    if mutation=='delay':native['comparability_basis']['stress_execution']['execution_delay_bars']=3
    elif mutation=='miss':native['comparability_basis']['stress_execution']['missed_fill_probability']=1.
    else:journal=[e for e in journal if e['event_type']!='risk_event']
    with pytest.raises(ValueError,match='STRESS_'):_verify_stress(events,native,journal)


def test_due_attempt_cannot_be_omitted_from_completed_schedule():
    from app.research.stress_session import _verify_stress,stress_manifest
    native=stress_manifest(fixture()['manifest'],policy());start=native['start_timestamp_ms'];due=start+86400000
    events=[dict(kind='SIGNAL_DECISION',timestamp_ms=due,payload=dict(source='STRATEGY',target=.5,decision_id='s',bar_open_ms=start)),
        dict(kind='SESSION_TERMINAL',timestamp_ms=due+86400000,payload=dict(pending_decision_id='s',lifecycle_status='COMPLETED'))]
    journal=[dict(event_type='bar_ingested',timestamp=due,payload={})]
    with pytest.raises(ValueError,match='STRESS_DUE_ATTEMPT_OMITTED'):_verify_stress(events,native,journal)


def test_post_mutation_broker_failure_preserves_uncertain_evidence(tmp_path,monkeypatch):
    from app.paper.session import stress_engine_v1 as engine
    original=engine.PaperBroker.execute
    def fail(self,*a,**k):
        original(self,*a,**k)
        raise engine.BrokerError('after mutation')
    monkeypatch.setattr(engine.PaperBroker,'execute',fail)
    with pytest.raises(RuntimeError,match='SIMULATED_EXECUTION_OUTCOME_UNKNOWN'):stress(tmp_path)
    assert (tmp_path/'MANIFEST.json').exists() and not (tmp_path/'RESULT.json').exists()
