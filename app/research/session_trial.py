"""Governed observed-session trial: reserve before source read, verify before outcome."""
import hashlib
import os
import re
from pathlib import Path
import pandas as pd

from .validation_plan import require,snapshot,read_artifact
from .registry_inspection import _hash,_hex
from .attempt_fingerprint import check_trial_preflight
from .trial_registry_v2 import execute_trial
from .walkforward import _Store
from .session_coverage import validate_session_coverage,verify_session_rows
from .observed_session import run_observed_session,verify_observed_session,VERSION
from .statistics_math import calculate_statistics
from .bound_statistics import evaluate_trial_statistics
from app.paper.session.ingest import dataset_hash,timeframe_ms
from app.paper.session.manifest import detect_manifest_drift
from app.paper.session.profiles import execution_profile_hash
from app.paper.session.risk import risk_limits_hash
from app.forward.manifest import code_hash,param_hash


def session_method_hash(manifest,dataset_manifest,segment_manifest,*,strategy_state,return_policy):
    # Instrumented engine source is part of this separately versioned method.
    from app.paper.session import observed_engine_v1 as engine
    from . import observed_session,session_coverage
    return _hash(dict(schema_version='wp5.observed_session_method.v1',manifest=manifest,
        dataset_manifest_hash=_hash(dataset_manifest),segment_manifest=segment_manifest,strategy_state=strategy_state,
        return_policy=return_policy,
        engine_code=hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest(),
        observer_code=hashlib.sha256(Path(observed_session.__file__).read_bytes()).hexdigest(),
        coverage_code=hashlib.sha256(Path(session_coverage.__file__).read_bytes()).hexdigest(),
        governance_code=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))


def run_session_trial(request,manifest,dataset_manifest,segment_manifest,*,profile,risk_limits,strategy,params,
                      registry_path,anchor_path,evidence_directory,read_bars,prior_result_artifacts,return_policy):
    q=snapshot(request);native=snapshot(manifest);segment=snapshot(segment_manifest)
    require(q.get('mode')=='RESERVATION' and check_trial_preflight(q)['reason_code'] is None,'RESERVATION_BINDING_INVALID')
    contract=q['contract_record'];config=contract['experiment_configuration'];attempt=q['attempt_fingerprint_record']
    require('H025' not in re.sub('[^A-Z0-9]','',config['strategy_id'].upper()),'H025_EXCLUDED')
    require(attempt['intended_dataset_zone'] in ('TRAIN','VAL'),'HOLDOUT_ACCESS_FORBIDDEN')
    zone=contract['boundaries'][attempt['intended_dataset_zone'].lower()]
    require(type(segment) is dict and set(segment)=={'schema_version','start_ms','end_ms','engine_dataset_hash','content_hash'} and
        segment['schema_version']=='wp5.session_segment.v1' and type(segment['start_ms']) is int and type(segment['end_ms']) is int and
        zone['start_ms']<=segment['start_ms']<segment['end_ms']<=zone['end_ms'] and
        _hex(segment['engine_dataset_hash']) and _hex(segment['content_hash']),'SEGMENT_MANIFEST_INVALID')
    timing=contract['static_timing_policy']
    require(all(timing[k]==0 for k in ('settlement_lag_ms','signal_delay_ms','order_submission_latency_ms')),
        'TIMING_PROFILE_UNSUPPORTED')
    require(native['software_version']==VERSION and not detect_manifest_drift(native)[0] and
        native['strategy']['strategy_id']==config['strategy_id']==strategy.strategy_id and
        native['strategy']['strategy_hash']==code_hash(strategy) and native['param_hash']==param_hash(params)==config['param_hash'] and
        native['execution_profile_hash']==execution_profile_hash(profile) and native['absolute_risk_limits_hash']==risk_limits_hash(risk_limits) and
        native['dataset_hash']==segment['engine_dataset_hash'] and timeframe_ms(native['timeframe'])==contract['dataset_identity']['timeframe_ms'],
        'SESSION_MANIFEST_INVALID')
    data,index=validate_session_coverage(dataset_manifest,contract,profile)
    times=[t for t in index if segment['start_ms']<=t<segment['end_ms']]
    require(times and native['start_timestamp_ms']==times[0],'SEGMENT_MANIFEST_INVALID')
    sampling=snapshot(return_policy)
    duration=contract['dataset_identity']['timeframe_ms'];mode=contract['dataset_identity']['coverage_mode']
    require(type(sampling) is dict and set(sampling)=={'schema_version','baseline','frequency','step_ms','calendar_policy_hash'} and
        sampling['schema_version']=='wp5.session_returns.v1' and sampling['baseline']=='FIRST_CLOSE' and
        sampling['frequency']==('SESSION_BAR' if mode=='SESSION_CALENDAR' else 'UTC_GRID') and
        type(sampling['step_ms']) is int and sampling['step_ms']>=duration and sampling['step_ms']%duration==0 and
        (mode=='SPARSE_DECLARED' or sampling['step_ms']==duration) and
        sampling['calendar_policy_hash']==data['coverage']['calendar_policy_hash'],'RETURN_POLICY_INVALID')
    method=session_method_hash(native,data,segment,strategy_state=snapshot(vars(strategy)),return_policy=sampling)
    require(config['method_hash']==attempt['method_hash']==method and
        attempt['ordered_input_hashes']==[_hash(data),_hash(segment)],'METHOD_BINDING_INVALID')
    artifacts=snapshot(prior_result_artifacts);store=_Store(evidence_directory);folder=Path(evidence_directory)
    try:
        frozen=dict(schema_version='wp5.session_trial_run.v1',request=q,session_manifest=native,
            dataset_manifest=data,segment_manifest=segment,method_hash=method,return_policy=sampling)
        raw=store.put('RUN.json',frozen)
        reservation=execute_trial(q,registry_path,anchor_path);store.put('RESERVATION.json',reservation)
        require(reservation['counter_status']=='COMMITTED','RESERVATION_NOT_COMMITTED')
        trial=reservation['result']['trial_id']
        # No automatic retry or completion on uncertain exceptions; reservation stays consumed.
        rows=verify_session_rows(read_bars(segment['start_ms'],segment['end_ms']),index,segment['start_ms'],segment['end_ms'])
        require(_hash(rows)==segment['content_hash'],'SEGMENT_CONTENT_MISMATCH')
        frame=pd.DataFrame(rows)
        require(dataset_hash(frame)==segment['engine_dataset_hash'],'SEGMENT_CONTENT_MISMATCH')
        store.check('RUN.json',raw)
        child=folder/'session';child.mkdir();os.fsync(store.fd)
        binding={k:q[k] for k in ('research_program_id','experiment_id','contract_hash')};binding['trial_id']=trial
        observed=run_observed_session(manifest=native,profile=profile,risk_limits=risk_limits,strategy=strategy,params=params,
            frame=frame,evidence_directory=child,trial_binding=binding)
        observed=verify_observed_session(child,expected_report_hash=observed['report_hash'])
        store.check('RUN.json',raw)
        require(session_method_hash(native,data,segment,strategy_state=snapshot(vars(strategy)),return_policy=sampling)==method,'METHOD_BINDING_INVALID')
        timing_rows=observed['audit']['timing_rows'];timing_result=None
        if timing_rows:
            # Full membership checks close the deferred session/sparse validator boundary.
            require(all(r['bar_open_ms'] in times and r['fill_ms'] in times for r in timing_rows),'COVERAGE_MEMBERSHIP_INVALID')
            timing_request={k:q[k] for k in ('research_program_id','experiment_id','contract_hash','contract_record')}
            timing_request.update(mode='TIMING_VALIDATION',trial_id=trial,timing_ledger_rows=timing_rows)
            timing_result=execute_trial(timing_request,registry_path,anchor_path)
            require(timing_result['counter_status']=='VERIFIED','TIMING_VALIDATION_FAILED')
            store.put('TIMING_VALIDATION.json',timing_result)
        eligibility=calculate_statistics(observed['net_returns'],n_total=1,sharpe_values=[])
        native_status=observed['native_result']['lifecycle_status']
        point_times=[p['timestamp_ms'] for p in observed['equity_points']]
        if sampling['frequency']=='UTC_GRID':
            comparable=all(b-a==sampling['step_ms'] for a,b in zip(point_times,point_times[1:]))
        else:
            expected_closes=[t+duration for t in times if t+duration<=point_times[-1]]
            comparable=point_times==expected_closes
        valid=comparable and native_status in ('COMPLETED','RISK_STOPPED') and eligibility['reason_code']=='INSUFFICIENT_TRIALS_FOR_DSR'
        lifecycle='COMPLETED_VALID' if valid else 'COMPLETED_INVALID'
        receipt=dict(schema_version='wp5.session_execution_receipt.v1',trial_binding=binding,
            frozen_run_hash=_hash(frozen),session_evidence_hash=observed['report_hash'],
            timing_validation_hash=_hash(timing_result),coverage_manifest_hash=_hash(data['coverage']),
            return_frequency_hash=_hash(sampling),return_intervals_status='VERIFIED' if comparable else 'NONCOMPARABLE',
            eligibility=eligibility,lifecycle_status=lifecycle,net_returns_hash=_hash(observed['net_returns']))
        store.put('EXECUTION_RECEIPT.json',receipt)
        artifact=dict(schema_version='wp5_3.trial_result.v2',research_program_id=q['research_program_id'],
            experiment_id=q['experiment_id'],trial_id=trial,net_returns=observed['net_returns'],
            execution_evidence_hash=_hash(receipt),return_frequency_hash=_hash(sampling))
        store.put('TRIAL_RESULT.json',artifact)
        outcome=execute_trial(dict(mode='OUTCOME_APPEND',research_program_id=q['research_program_id'],trial_id=trial,
            trial_outcome=lifecycle,result_hash=_hash(artifact)),registry_path,anchor_path)
        store.put('OUTCOME.json',outcome);require(outcome['counter_status']=='VERIFIED','OUTCOME_NOT_RECORDED')
        artifacts[_hash(artifact)]=artifact
        stats=evaluate_trial_statistics(registry_path,anchor_path,research_program_id=q['research_program_id'],
            trial_id=trial,result_artifacts=artifacts) if valid else None
        report=dict(schema_version='wp5.session_trial_report.v1',trial_id=trial,lifecycle_status=lifecycle,
            native_lifecycle=native_status,evidence_kind='SIMULATED_REPLAY',prospective_verified=False,
            result_hash=_hash(artifact),session_evidence_hash=observed['report_hash'],execution_receipt_hash=_hash(receipt),statistics=stats,eligibility=eligibility,
            timing_validation=timing_result,coverage_status='PREREGISTERED_MEMBERSHIP_VERIFIED',coverage_manifest_hash=_hash(data['coverage']),
            full_execution_timing_status='VERIFIED_FOR_EXECUTED_DECISIONS' if timing_rows else 'NO_EXECUTIONS',
            decision_status_counts=observed['audit']['decision_status_counts'],return_frequency_hash=_hash(sampling),
            return_intervals_status='VERIFIED' if comparable else 'NONCOMPARABLE')
        report['report_hash']=_hash(report);store.put('RESULT.json',report);return report
    finally:store.close()


def verify_session_trial(directory,*,registry_path,anchor_path):
    """Verify committed execution receipts without data reads or counter increments.

    Returns execution verification only; cached historical statistics are not
    relabeled as current statistics. Does not repair or reconstruct any file.
    """
    folder=Path(directory);run=read_artifact(folder,'RUN.json');artifact=read_artifact(folder,'TRIAL_RESULT.json')
    receipt=read_artifact(folder,'EXECUTION_RECEIPT.json');q=run['request'];attempt=q['attempt_fingerprint_record']
    require(artifact['schema_version']=='wp5_3.trial_result.v2' and _hash(receipt)==artifact['execution_evidence_hash'] and
        _hash(run)==receipt['frozen_run_hash'] and _hash(run['return_policy'])==artifact['return_frequency_hash']==receipt['return_frequency_hash'],
        'EXECUTION_RECEIPT_MISMATCH')
    require(all(artifact[k]==q[k]==receipt['trial_binding'][k] for k in ('research_program_id','experiment_id')) and
        artifact['trial_id']==receipt['trial_binding']['trial_id'] and q['contract_hash']==receipt['trial_binding']['contract_hash'],
        'EXECUTION_RECEIPT_MISMATCH')
    replay=execute_trial(dict(mode='VERIFICATION_REPLAY',research_program_id=q['research_program_id'],
        performance_data_accessed=False,append_reservation_requested=False,verification_replay_record=dict(
            replay_of_trial_id=artifact['trial_id'],original_input_hashes=attempt['ordered_input_hashes'],
            original_method_hash=attempt['method_hash'],original_result_hash=_hash(artifact))),registry_path,anchor_path)
    require(replay['counter_status']=='REPLAY_VERIFIED','EXECUTION_RECEIPT_MISMATCH')
    observed=verify_observed_session(folder/'session',expected_report_hash=receipt['session_evidence_hash'])
    require(observed['trial_binding']==receipt['trial_binding'] and artifact['net_returns']==observed['net_returns'] and
        _hash(artifact['net_returns'])==receipt['net_returns_hash'],'EXECUTION_RECEIPT_MISMATCH')
    profile=run['session_manifest']['execution_profile']
    data,index=validate_session_coverage(run['dataset_manifest'],q['contract_record'],profile)
    require(_hash(data['coverage'])==receipt['coverage_manifest_hash'],'EXECUTION_RECEIPT_MISMATCH')
    rows=observed['audit']['timing_rows'];timing=None
    if rows:
        bounds=run['segment_manifest'];members={t for t in index if bounds['start_ms']<=t<bounds['end_ms']}
        require(all(r['bar_open_ms'] in members and r['fill_ms'] in members for r in rows),'COVERAGE_MEMBERSHIP_INVALID')
        request={k:q[k] for k in ('research_program_id','experiment_id','contract_hash','contract_record')}
        request.update(mode='TIMING_VALIDATION',trial_id=artifact['trial_id'],timing_ledger_rows=rows)
        timing=execute_trial(request,registry_path,anchor_path)
        require(timing['counter_status']=='VERIFIED','TIMING_VALIDATION_FAILED')
        # Saved validation is bound in the outcome receipt; current ledger heads may have advanced.
        saved=read_artifact(folder,'TIMING_VALIDATION.json')
        require(_hash(saved)==receipt['timing_validation_hash'] and saved['result']==timing['result'],'EXECUTION_RECEIPT_MISMATCH')
    else:require(receipt['timing_validation_hash']==_hash(None),'EXECUTION_RECEIPT_MISMATCH')
    return dict(schema_version='wp5.session_verification.v1',verification_status='VERIFIED',
        trial_id=artifact['trial_id'],execution_receipt_hash=_hash(receipt),session_evidence_hash=observed['report_hash'],
        result_hash=_hash(artifact),performance_data_accessed=False,counter_incremented=False,
        prospective_verified=False,cached_statistics_revalidated=False)
