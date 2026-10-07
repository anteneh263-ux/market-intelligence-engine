"""Bind a historical walk-forward run to a v2 reservation and result outcome.

No registry initialization, implicit evidence roots, holdout access, resume,
prospective timing assertion, or statistical promotion.
"""
import re

from .attempt_fingerprint import check_trial_preflight
from .bound_statistics import evaluate_trial_statistics
from .trial_registry_v2 import execute_trial
from .statistics_math import calculate_statistics
from .walkforward import _Store, _copy, _hash, _policy, _windows, run_walkforward


def walkforward_method_hash(policy):
    """New adaptive method identity; excludes only the recursive experiment ID."""
    p = _policy(policy)
    return _hash(dict(schema_version='wp5_2.adaptive_method.v1',
                      policy={k:v for k,v in p.items() if k!='experiment_id'}))


def walkforward_selector_hash(policy):
    """Bind the selection algorithm version, never a retrospectively chosen set."""
    p = _policy(policy)
    return _hash(dict(schema_version='wp5_2.adaptive_selector.v1',callback_version=p['callback_version']))


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _preflight(request, policy, manifest):
    try:
        q, p, m = _copy(request), _policy(policy), _copy(manifest)
        _require(q.get('mode')=='RESERVATION' and check_trial_preflight(q)['reason_code'] is None,
                 'RESERVATION_BINDING_INVALID')
        c = q['contract_record']
        attempt = q['attempt_fingerprint_record']
        config = c['experiment_configuration']
        zone = attempt['intended_dataset_zone']
        _require(zone in ('TRAIN','VAL'),'HOLDOUT_ACCESS_FORBIDDEN')
        bounds = c['boundaries'][zone.lower()]
        _require(p['experiment_id']==q['experiment_id'] and
                 p['dataset_id']==c['dataset_identity']['dataset_hash'] and
                 p['strategy_id']==config['strategy_id'] and
                 bounds['start_ms']<=p['start_ms']<p['end_ms']<=bounds['end_ms'], 'POLICY_BINDING_INVALID')
        _require(config['method_hash']==attempt['method_hash']==walkforward_method_hash(p) and
                 config['param_hash']==walkforward_selector_hash(p),'METHOD_BINDING_INVALID')
        _require(type(m) is dict and set(m)=={'schema_version','segments'} and
                 m['schema_version']=='wp5_2.segments.v1' and type(m['segments']) is list,
                 'SEGMENT_MANIFEST_INVALID')
        expected = sorted({tuple(w[k]) for w in _windows(p) for k in ('train','test')})
        actual = []
        for s in m['segments']:
            _require(type(s) is dict and set(s)=={'start_ms','end_ms','rows_hash'} and
                     type(s['start_ms']) is int and type(s['end_ms']) is int and
                     type(s['rows_hash']) is str and re.fullmatch('[0-9a-f]{64}',s['rows_hash']),
                     'SEGMENT_MANIFEST_INVALID')
            actual.append((s['start_ms'],s['end_ms']))
        _require(actual==expected and attempt['ordered_input_hashes']==[_hash(m)],'SEGMENT_MANIFEST_INVALID')
        return q,p,m
    except (KeyError,TypeError,OverflowError,RecursionError) as exc:
        raise ValueError('TRIAL_INPUT_INVALID') from exc


def run_walkforward_trial(reservation_request, policy, segment_manifest, *, registry_path, anchor_path,
                          evidence_directory, walkforward_directory, read_bars, select_parameters,
                          signal, regime, prior_result_artifacts):
    """Reserve before read, persist before outcome, derive statistics from ledger.

    Any exception after a reservation must be reconciled from persisted evidence;
    do not automatically retry. A completed lifecycle is not a statistical PASS.
    """
    q,p,manifest = _preflight(reservation_request,policy,segment_manifest)
    prior = _copy(prior_result_artifacts)
    _require(type(prior) is dict,'RESULT_ARTIFACT_MAPPING_INVALID')
    coordinator = _Store(evidence_directory)
    try:
        # Validate the second explicit directory before any registry mutation.
        probe = _Store(walkforward_directory)
        try:
            import os
            a,b = os.fstat(coordinator.fd),os.fstat(probe.fd)
            _require((a.st_dev,a.st_ino)!=(b.st_dev,b.st_ino),'EVIDENCE_DIRECTORIES_MUST_DIFFER')
        finally:
            probe.close()
        coordinator.put('RUN.json',dict(schema_version='wp5_2.trial_run.v1',reservation_request=q,
            policy=p,segment_manifest=manifest))
        reservation = execute_trial(q,registry_path,anchor_path)
        coordinator.put('RESERVATION.json',reservation)
        _require(reservation['counter_status']=='COMMITTED','RESERVATION_NOT_COMMITTED')
        trial = reservation['result']['trial_id']
        verified = execute_trial(dict(mode='REGISTRY_STATE',research_program_id=q['research_program_id']),
                                 registry_path,anchor_path)
        _require(verified['counter_status']=='VERIFIED','REGISTRY_NOT_READABLE')
        commitments = {(s['start_ms'],s['end_ms']):s['rows_hash'] for s in manifest['segments']}
        duration = q['contract_record']['dataset_identity']['timeframe_ms']
        def bound_reader(start,end):
            _require((start,end) in commitments,'UNREGISTERED_SEGMENT')
            rows = _copy(read_bars(start,end))
            _require(_hash(rows)==commitments[start,end],'SEGMENT_CONTENT_MISMATCH')
            _require(type(rows) is list and all(type(r) is dict and
                type(r.get('open_time')) is int and type(r.get('close_time')) is int and
                r['close_time']-r['open_time']==duration for r in rows),'DATASET_TIMEFRAME_MISMATCH')
            return rows
        wf = run_walkforward(p,evidence_directory=walkforward_directory,read_bars=bound_reader,
                             select_parameters=select_parameters,signal=signal,regime=regime)
        wf_valid = wf['walkforward_status']=='VALID'
        eligibility = None
        valid = False
        completion_reason = wf['reason_code']
        if wf_valid:
            eligibility = calculate_statistics(wf['aggregate']['net']['returns'],n_total=1,sharpe_values=[])
            valid = (eligibility['reason_code']=='INSUFFICIENT_TRIALS_FOR_DSR' and
                     eligibility['sr_observed'] is not None)
            completion_reason = None if valid else eligibility['reason_code']
            coordinator.put('ELIGIBILITY.json',eligibility)
        lifecycle = 'COMPLETED_VALID' if valid else 'COMPLETED_INVALID'
        artifact = dict(schema_version='wp5_3.trial_result.v1',research_program_id=q['research_program_id'],
            experiment_id=q['experiment_id'],trial_id=trial,net_returns=wf['aggregate']['net']['returns']) if wf_valid else wf
        if wf_valid:
            coordinator.put('TRIAL_RESULT.json',artifact)
        result_hash = _hash(artifact)
        outcome = execute_trial(dict(mode='OUTCOME_APPEND',research_program_id=q['research_program_id'],
            trial_id=trial,trial_outcome=lifecycle,result_hash=result_hash),registry_path,anchor_path)
        coordinator.put('OUTCOME.json',outcome)
        _require(outcome['counter_status']=='VERIFIED','OUTCOME_NOT_RECORDED')
        statistics = None
        if valid:
            prior[result_hash] = artifact
            statistics = evaluate_trial_statistics(registry_path,anchor_path,
                research_program_id=q['research_program_id'],trial_id=trial,result_artifacts=prior)
        report = dict(schema_version='wp5_2.trial_report.v1',lifecycle_status=lifecycle,trial_id=trial,
            research_program_id=q['research_program_id'],experiment_id=q['experiment_id'],
            completion_reason=completion_reason,eligibility_hash=_hash(eligibility) if eligibility else None,
            result_hash=result_hash,walkforward_report_hash=wf['walkforward_report_hash'],
            reservation_report_hash=reservation['ledger_report_hash'],outcome_report_hash=outcome['ledger_report_hash'],
            segment_manifest_hash=_hash(manifest),dataset_provenance='UPSTREAM_REQUIRED',
            timing_scope='HISTORICAL_CLOSE_TO_CLOSE',statistics=statistics)
        report['report_hash'] = _hash(report)
        coordinator.put('RESULT.json',report)
        return report
    finally:
        coordinator.close()
