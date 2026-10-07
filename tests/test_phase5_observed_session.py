import copy
import pandas as pd
import pytest
from app.paper.session.observed_engine_v1 import run_replay
from app.paper.session.manifest import build_session_manifest
from app.paper.session.profiles import freeze_execution_profile
from app.paper.session.risk import freeze_risk_limits
from app.paper.session.ingest import dataset_hash


class SyntheticStrategy:
    strategy_id='SYNTHETIC-PHASE5-SESSION'
    direction='long_only'
    version='1'
    def positions(self,frame,params):
        return pd.Series([params['target']]*len(frame),index=frame.index)


def fixture(target=.5,closes=None,profile_overrides=None,asset='BTC/USD'):
    closes=closes or [100.,102.,101.,103.,104.]
    start=1577836800000;day=86400000
    rows=[]
    for i,c in enumerate(closes):
        o=closes[max(0,i-1)]
        rows.append(dict(open_time=start+i*day,open=o,high=max(o,c)+1,low=min(o,c)-1,close=c,volume=1000.))
    frame=pd.DataFrame(rows);strategy=SyntheticStrategy();params={'target':target}
    profile=freeze_execution_profile(asset,**(profile_overrides or {}))
    risk=freeze_risk_limits(max_drawdown=.9,max_session_loss=.9,max_daily_loss=.9,max_loss_per_trade=.9)
    manifest=build_session_manifest(strategy=strategy,params=params,asset=asset,timeframe='1d',
        data_source={'provider':'synthetic'},start_timestamp_ms=start,initial_capital=100000.,
        position_sizing_policy={'type':'target_fraction_of_equity','max_gross':1.},execution_profile=profile,
        absolute_risk_limits=risk,gap_stale_policy={'max_gap_bars':1},regime_policy_hash='synthetic-regime',
        consistency_policy_hash='synthetic-consistency',reference_range_hash='synthetic-reference',
        comparability_basis={'source':'synthetic-only'},dataset_hash=dataset_hash(frame),
        code_version='paper-session-observed-v1')
    return dict(manifest=manifest,profile=profile,risk_limits=risk,strategy=strategy,params=params,frame=frame)


def test_engine_emits_observations_at_real_callback_points(tmp_path):
    events=[]
    result=run_replay(**fixture(),journal_dir=str(tmp_path),_phase5_observer=lambda kind,time,payload:events.append((kind,time,payload)))
    assert result['lifecycle_status']=='COMPLETED'
    kinds=[e[0] for e in events]
    assert 'BAR_AVAILABLE' in kinds and 'FEATURE_OBSERVED' in kinds and 'SIGNAL_DECISION' in kinds
    assert 'ORDER_SUBMITTED' in kinds and 'ORDER_RESULT' in kinds and kinds[-1]=='SESSION_TERMINAL'
    assert kinds.index('BAR_AVAILABLE')<kinds.index('FEATURE_OBSERVED')<kinds.index('SIGNAL_DECISION')<kinds.index('ORDER_SUBMITTED')


def binding():return dict(research_program_id='a'*64,experiment_id='b'*64,trial_id='c'*64,contract_hash='d'*64)


def observed(tmp_path,**kwargs):
    from app.research.observed_session import run_observed_session
    return run_observed_session(**fixture(**kwargs),evidence_directory=tmp_path.resolve(),trial_binding=binding())


def test_durable_six_timestamps_and_actual_journal_agree(tmp_path):
    result=observed(tmp_path)
    assert result['observation_status']=='VERIFIED' and result['prospective_verified'] is False
    assert result['audit']['completed_timing_rows']>0
    for r in result['audit']['timing_rows']:
        assert r['bar_open_ms']<r['bar_available_ms']<=r['feature_observed_ms']<=r['signal_decision_ms']<=r['order_submitted_ms']<=r['fill_ms']
    assert result['audit']['decision_status_counts']['UNEXECUTED']==1
    assert (tmp_path/'RESULT.json').exists()
    with pytest.raises(FileExistsError):observed(tmp_path)


@pytest.mark.parametrize('options,expected',[
    ({'target':0},'NO_TRADE'),
    ({'profile_overrides':{'max_fill_qty':1.}},'PARTIAL'),
    ({'profile_overrides':{'min_notional':1e12}},'REJECTED')])
def test_nonfills_and_partials_have_honest_status(tmp_path,options,expected):
    result=observed(tmp_path,**options)
    assert result['audit']['decision_status_counts'][expected]>0
    if expected!='PARTIAL':assert result['audit']['timing_rows']==[]


def test_observer_off_journal_matches_observer_on(tmp_path):
    from app.research.observed_session import run_observed_session
    args=fixture();a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    from app.paper.session.engine import run_replay as legacy_run_replay
    old=legacy_run_replay(**copy.deepcopy({k:v for k,v in args.items() if k!='strategy'}),strategy=args['strategy'],journal_dir=str(a))
    new=run_observed_session(**args,evidence_directory=b.resolve(),trial_binding=binding())
    assert (a/'journal.jsonl').read_bytes()==(b/'journal'/'journal.jsonl').read_bytes()
    assert {k:v for k,v in old.items() if k!='journal_path'}==new['native_result']


def test_observation_write_failure_stops_without_terminal_claim(tmp_path,monkeypatch):
    from app.research.observed_session import _Store
    original=_Store.put
    def fail(self,name,value):
        if name.startswith('event-') and value['kind']=='ORDER_RESULT':raise OSError('synthetic persistence failure')
        return original(self,name,value)
    monkeypatch.setattr(_Store,'put',fail)
    with pytest.raises(OSError,match='persistence'):observed(tmp_path)
    assert not (tmp_path/'RESULT.json').exists()
    assert list(tmp_path.glob('event-*'))


def test_broker_error_is_not_a_fill_and_keeps_decision_correlation(tmp_path,monkeypatch):
    from app.paper.session.broker import PaperBroker,BrokerError
    original=PaperBroker.execute;failed=False
    def fail_once(self,*args,**kwargs):
        nonlocal failed
        if not failed:failed=True;raise BrokerError('synthetic transient')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(PaperBroker,'execute',fail_once)
    result=observed(tmp_path)
    assert result['native_result']['counts']['execution_errors']==1
    assert any(d['attempts']==2 for d in result['audit']['decisions'])
    assert result['audit']['submitted_attempts']==result['native_result']['counts']['orders']+1


def test_risk_exit_is_explicit_and_not_an_invented_strategy_fill(tmp_path):
    result=observed(tmp_path,target=1,closes=[100,110,5,5,5])
    assert result['native_result']['lifecycle_status']=='RISK_STOPPED'
    assert any(d['source']=='RISK' and d['target']==0 for d in result['audit']['decisions'])
    times=[p['timestamp_ms'] for p in result['equity_points']]
    assert len(times)==len(set(times)) and len(result['net_returns'])==len(times)-1
    assert result['equity_points'][-1]['equity']==result['native_result']['final_equity']


def test_missing_native_event_is_refused(tmp_path,monkeypatch):
    import app.research.observed_session as module
    original=module.run_replay
    def omit(**kwargs):
        callback=kwargs['_phase5_observer']
        kwargs['_phase5_observer']=lambda k,t,p:None if k=='ORDER_SUBMITTED' else callback(k,t,p)
        return original(**kwargs)
    monkeypatch.setattr(module,'run_replay',omit)
    with pytest.raises(ValueError,match='OBSERVATION_ORDER_INVALID'):observed(tmp_path)
    assert not (tmp_path/'RESULT.json').exists()


def test_post_fill_broker_error_never_claims_verified_no_fill(tmp_path,monkeypatch):
    from app.paper.session.broker import PaperBroker,BrokerError
    def fail(self,mark):raise BrokerError('error after portfolio mutation')
    monkeypatch.setattr(PaperBroker,'_reconcile',fail)
    with pytest.raises(RuntimeError,match='EXECUTION_OUTCOME_UNKNOWN'):observed(tmp_path)
    assert not (tmp_path/'RESULT.json').exists()


def test_completed_safe_exit_has_no_outstanding_pending_identity(tmp_path):
    import json
    observed(tmp_path,target=1,closes=[100,110,5,5,5])
    terminal=json.loads(sorted(tmp_path.glob('event-*'))[-1].read_text())
    assert terminal['payload']['pending_decision_id'] is None


def test_forged_terminal_pending_identity_refuses(tmp_path,monkeypatch):
    import app.research.observed_session as module
    original=module.run_replay
    def change(**kwargs):
        callback=kwargs['_phase5_observer']
        def record(k,t,p):
            if k=='SESSION_TERMINAL':p['pending_decision_id']='not-an-observed-decision'
            callback(k,t,p)
        kwargs['_phase5_observer']=record
        return original(**kwargs)
    monkeypatch.setattr(module,'run_replay',change)
    with pytest.raises(ValueError,match='OBSERVATION_TERMINAL_INVALID'):observed(tmp_path)
