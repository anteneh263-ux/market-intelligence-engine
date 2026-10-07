import copy
import json
import pandas as pd
import pytest
from app.research.session_trial import run_session_trial,session_method_hash
from app.research.phase5_identity_contract import validate_experiment_contract
from app.research.trial_registry_v2 import initialize_trial_registry,execute_trial
from app.research.registry_inspection import _hash,_canonical
from app.paper.session.manifest import manifest_hash
from app.paper.session.profiles import is_trading_time
from app.paper.session.ingest import dataset_hash
from test_phase5_contract_binding import request as identity_request
from test_phase5_observed_session import fixture


def setup(root,mode='CONTINUOUS_24_7',target=.5):
    root=root.resolve();out=root/'run';out.mkdir();reg=root/'registry';anchor=root/'anchor'
    initialize_trial_registry(str(reg),str(anchor))
    args=fixture(target=target,closes=[100*(1.0005**i)*(1.00003**(i%3)) for i in range(366)],asset='SPY' if mode=='SESSION_CALENDAR' else 'BTC/USD')
    all_rows=args['frame'].to_dict('records');raw=identity_request(mode);d=raw['dataset_identity']
    if mode=='SESSION_CALENDAR':all_rows=[r for r in all_rows if is_trading_time(args['profile'],r['open_time'])]
    elif mode=='SPARSE_DECLARED':all_rows=[r for i,r in enumerate(all_rows) if i%3==0 or i==365]
    opens=[r['open_time'] for r in all_rows]
    coverage=dict(schema_version='wp5.session_coverage.v1',coverage_mode=mode,
        calendar_id=d.get('calendar_id'),calendar_policy_hash=_hash(dict(market_hours=args['profile']['market_hours'],timeframe_ms=86400000)),
        open_times=opens)
    dataset=dict(schema_version='wp5.session_dataset.v1',timeframe_ms=86400000,
        rows=[dict(open_time=r['open_time'],row_hash=_hash(r)) for r in all_rows],coverage=coverage)
    d.update(rows=len(all_rows),first_open_ms=opens[0],last_open_ms=opens[-1],dataset_end_exclusive_ms=opens[-1]+86400000,dataset_hash=_hash(dataset))
    if mode=='SESSION_CALENDAR':d['calendar_hash']=_hash(coverage)
    elif mode=='SPARSE_DECLARED':d['coverage_manifest_hash']=_hash(coverage)
    boundary=raw['boundaries']['train']
    def reader(a,b):return copy.deepcopy([r for r in all_rows if a<=r['open_time']<b])
    rows=reader(boundary['start_ms'],boundary['end_ms']);frame=pd.DataFrame(rows)
    native=args['manifest'];native['dataset_hash']=dataset_hash(frame);native['start_timestamp_ms']=rows[0]['open_time']
    if mode=='SPARSE_DECLARED':
        args['risk_limits']['max_missing_bars']=5
        args['risk_limits']['stale_data_tolerance_ms']=86400000*6
        from app.paper.session.risk import risk_limits_hash
        native['absolute_risk_limits']=copy.deepcopy(args['risk_limits'])
        native['absolute_risk_limits_hash']=risk_limits_hash(args['risk_limits'])
    native['manifest_hash']=manifest_hash(native)
    segment=dict(schema_version='wp5.session_segment.v1',start_ms=boundary['start_ms'],end_ms=boundary['end_ms'],
        engine_dataset_hash=dataset_hash(frame),content_hash=_hash(rows))
    sampling=dict(schema_version='wp5.session_returns.v1',baseline='FIRST_CLOSE',
        frequency='SESSION_BAR' if mode=='SESSION_CALENDAR' else 'UTC_GRID',
        step_ms=86400000*(3 if mode=='SPARSE_DECLARED' else 1),calendar_policy_hash=coverage['calendar_policy_hash'])
    method=session_method_hash(native,dataset,segment,strategy_state=vars(args['strategy']),return_policy=sampling)
    raw['experiment_configuration'].update(strategy_id=args['strategy'].strategy_id,param_hash=native['param_hash'],method_hash=method)
    contract=validate_experiment_contract(raw);assert contract['contract_status']=='VALID',contract
    q=dict(mode='RESERVATION',research_program_id=contract['research_program_id'],experiment_id=contract['experiment_id'],
        contract_hash=contract['contract_hash'],contract_record=contract,attempt_fingerprint_record=dict(
            research_program_id=contract['research_program_id'],experiment_id=contract['experiment_id'],method_hash=method,
            ordered_input_hashes=[_hash(dataset),_hash(segment)],intended_dataset_zone='TRAIN'))
    kwargs={k:args[k] for k in ('profile','risk_limits','strategy','params')}
    kwargs.update(registry_path=str(reg),anchor_path=str(anchor),evidence_directory=out,read_bars=reader,prior_result_artifacts={},return_policy=sampling)
    return q,native,dataset,segment,kwargs


def run(case):return run_session_trial(*case[:4],**case[4])


@pytest.mark.parametrize('mode',['CONTINUOUS_24_7','SESSION_CALENDAR','SPARSE_DECLARED'])
def test_real_engine_registry_coverage_and_timing_all_modes(tmp_path,mode):
    case=setup(tmp_path,mode);reader=case[4]['read_bars']
    def guarded(a,b):
        state=execute_trial(dict(mode='REGISTRY_STATE',research_program_id=case[0]['research_program_id']),case[4]['registry_path'],case[4]['anchor_path'])
        assert state['N_total']==1
        assert (case[4]['evidence_directory']/'RUN.json').exists()
        return reader(a,b)
    case[4]['read_bars']=guarded;result=run(case)
    assert result['lifecycle_status']=='COMPLETED_VALID',result
    assert result['full_execution_timing_status']=='VERIFIED_FOR_EXECUTED_DECISIONS'
    assert result['coverage_status']=='PREREGISTERED_MEMBERSHIP_VERIFIED'
    assert result['statistics']['binding_status']=='VERIFIED'
    assert result['statistics']['statistics']['reason_code']=='INSUFFICIENT_TRIALS_FOR_DSR'
    artifact=json.loads((case[4]['evidence_directory']/'TRIAL_RESULT.json').read_text())
    assert artifact['execution_evidence_hash']==result['execution_receipt_hash'] and _hash(artifact)==result['result_hash']
    assert result['prospective_verified'] is False
    with pytest.raises(FileExistsError):run(case)


def test_changed_source_cannot_create_completed_outcome(tmp_path):
    case=setup(tmp_path);reader=case[4]['read_bars']
    def changed(a,b):rows=reader(a,b);rows[0]['close']+=1;return rows
    case[4]['read_bars']=changed
    with pytest.raises(ValueError,match='DATASET_CONTENT_MISMATCH'):run(case)
    state=execute_trial(dict(mode='REGISTRY_STATE',research_program_id=case[0]['research_program_id']),case[4]['registry_path'],case[4]['anchor_path'])
    assert state['N_total']==1 and state['result']['outcome_count']==0


def test_changed_manifest_refuses_before_reservation(tmp_path):
    case=setup(tmp_path);case[2]['rows'][0]['row_hash']='f'*64
    with pytest.raises(ValueError):run(case)
    assert not list(case[4]['evidence_directory'].iterdir())
    state=execute_trial(dict(mode='REGISTRY_STATE',research_program_id=case[0]['research_program_id']),case[4]['registry_path'],case[4]['anchor_path'])
    assert state['N_total']==0


def test_missing_coverage_member_cannot_be_hidden_by_resealing(tmp_path):
    from app.research.session_coverage import validate_session_coverage
    case=setup(tmp_path,'SESSION_CALENDAR');data=case[2];contract=case[0]['contract_record']
    data['rows'].pop(5);data['coverage']['open_times'].pop(5)
    contract['dataset_identity'].update(rows=len(data['rows']),dataset_hash=_hash(data),calendar_hash=_hash(data['coverage']))
    with pytest.raises(ValueError,match='COVERAGE_MEMBERSHIP_INVALID'):validate_session_coverage(data,contract,case[4]['profile'])


def test_recording_failure_leaves_counted_reservation_without_false_outcome(tmp_path,monkeypatch):
    import app.research.observed_session as module
    case=setup(tmp_path);original=module._Store.put
    def failed(self,name,value):
        if name=='event-00000001.json':raise OSError('fault')
        return original(self,name,value)
    monkeypatch.setattr(module._Store,'put',failed)
    with pytest.raises(OSError):run(case)
    state=execute_trial(dict(mode='REGISTRY_STATE',research_program_id=case[0]['research_program_id']),case[4]['registry_path'],case[4]['anchor_path'])
    assert state['N_total']==1 and state['result']['outcome_count']==0


def test_broker_retry_gap_retains_evidence_but_cannot_pass_statistics(tmp_path,monkeypatch):
    from app.paper.session.broker import PaperBroker,BrokerError
    original=PaperBroker.execute;failed=False
    def fail_once(self,*args,**kwargs):
        nonlocal failed
        if not failed:failed=True;raise BrokerError('transient')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(PaperBroker,'execute',fail_once)
    case=setup(tmp_path);r=run(case)
    assert r['lifecycle_status']=='COMPLETED_INVALID' and r['return_intervals_status']=='NONCOMPARABLE'
    assert r['full_execution_timing_status']=='VERIFIED_FOR_EXECUTED_DECISIONS'
    assert r['statistics'] is None


def test_incompatible_return_frequencies_cannot_share_sharpe_vector(tmp_path):
    from app.research.bound_statistics import evaluate_trial_statistics
    from test_phase5_bound_statistics import completed
    case=setup(tmp_path);r=run(case)
    artifact=json.loads((case[4]['evidence_directory']/'TRIAL_RESULT.json').read_text());artifacts={_hash(artifact):artifact}
    completed((case[4]['registry_path'],case[4]['anchor_path']),case[0],[-.01,.02]*30,artifacts)
    stats=evaluate_trial_statistics(case[4]['registry_path'],case[4]['anchor_path'],research_program_id=case[0]['research_program_id'],
        trial_id=r['trial_id'],result_artifacts=artifacts)
    assert stats['binding_status']=='REFUSED' and stats['reason_code']=='TRIAL_VECTOR_BINDING_INVALID'


def test_evidence_replay_reads_no_source_and_does_not_increment(tmp_path):
    from app.research.session_trial import verify_session_trial
    case=setup(tmp_path);first=run(case)
    root=case[4]['evidence_directory'];before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    kwargs={k:case[4][k] for k in ('registry_path','anchor_path')}
    report=verify_session_trial(root,**kwargs)
    assert report['verification_status']=='VERIFIED' and report['result_hash']==first['result_hash']
    assert not report['performance_data_accessed'] and not report['counter_incremented']
    assert before=={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    state=execute_trial(dict(mode='REGISTRY_STATE',research_program_id=case[0]['research_program_id']),**kwargs)
    assert state['N_total']==1


def test_evidence_receipt_or_observation_tampering_refuses_replay(tmp_path):
    from app.research.session_trial import verify_session_trial
    case=setup(tmp_path);run(case);root=case[4]['evidence_directory']
    path=root/'session'/'event-00000000.json';event=json.loads(path.read_text());event['timestamp_ms']+=1
    event['event_hash']=_hash({k:v for k,v in event.items() if k!='event_hash'});path.write_bytes(_canonical(event))
    with pytest.raises(ValueError):verify_session_trial(root,registry_path=case[4]['registry_path'],anchor_path=case[4]['anchor_path'])


def test_two_real_session_trials_produce_bound_comparable_statistics(tmp_path):
    from app.research.session_trial import verify_session_trial
    a=tmp_path/'first';a.mkdir();b=tmp_path/'second';b.mkdir()
    first=setup(a,target=.25);one=run(first)
    artifact=json.loads((first[4]['evidence_directory']/'TRIAL_RESULT.json').read_text())
    second=setup(b,target=.75)
    for key in ('registry_path','anchor_path'):second[4][key]=first[4][key]
    second[4]['prior_result_artifacts']={_hash(artifact):artifact}
    two=run(second)
    assert first[0]['research_program_id']==second[0]['research_program_id']
    stats=two['statistics'];assert stats['binding_status']=='VERIFIED'
    assert stats['statistics']['stats_status']=='VALID' and stats['statistics']['N_total']==stats['statistics']['K']==2
    assert 0<=stats['statistics']['dsr']<=1
    verified=verify_session_trial(first[4]['evidence_directory'],registry_path=first[4]['registry_path'],anchor_path=first[4]['anchor_path'])
    assert verified['verification_status']=='VERIFIED'
