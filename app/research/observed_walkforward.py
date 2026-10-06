"""Governance for separately versioned historical observed window-block trials."""
import hashlib
import re
from pathlib import Path
from app.forward.manifest import code_hash, param_hash
from app.paper.session.ingest import timeframe_ms
from .registry_inspection import _hash, _hex
from .validation_plan import require, snapshot, read_artifact, finite
from .walkforward import _Store
from .attempt_fingerprint import check_trial_preflight
from .session_coverage import validate_session_coverage
from .observed_session import verify_observed_session
from . import observed_wf_reporting as reporting
from .trial_registry_v2 import execute_trial
from .statistics_math import calculate_statistics
from .bound_statistics import evaluate_trial_statistics
from app.strategy_research.evaluation.observed_walkforward import run_windows, training_partition


def _descriptor(obj):
    return dict(source_hash=code_hash(obj), state=snapshot(vars(obj)))


def observed_wf_selector_hash(policy, selector):
    return _hash(dict(schema_version='wp5.observed_wf_selector.v1', selector=_descriptor(selector),
        candidates=policy['candidates'], selection_rule=policy['selection_rule']))


def observed_wf_method_hash(policy, dataset_manifest, *, profile, risk_limits, strategy, selector):
    from app.paper.session import observed_engine_v1
    from app.strategy_research.evaluation import observed_walkforward
    from . import observed_session, session_coverage, purge_embargo
    modules = [observed_engine_v1, observed_walkforward, observed_session, session_coverage, purge_embargo]
    if policy['schema_version']=='wp5.observed_wf_policy.v3':modules.append(reporting)
    sources = {m.__name__: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules}
    sources[__name__] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return _hash(dict(schema_version='wp5.observed_wf_method.v1', policy=policy, dataset_manifest_hash=_hash(dataset_manifest),
        profile=profile, risk_limits=risk_limits, strategy=_descriptor(strategy),
        selector_hash=observed_wf_selector_hash(policy, selector), sources=sources))


def _frequency(policy, duration):
    return dict(schema_version='wp5.window_block_returns.v1', frequency='WINDOW_BLOCK', step_ms=duration,
        baseline='FIRST_CLOSE', reset='FRESH_CAPITAL_AND_STATE', initial_capital=policy['initial_capital'],
        terminal='MARK_TO_MARKET_NO_FORCED_EXIT', test_windows=[w['test'] for w in policy['windows']])


def _validate_policy(policy, contract):
    p = snapshot(policy)
    fields = set('schema_version strategy_id asset timeframe initial_capital min_train_rows min_test_rows embargo_ms label_horizon_bars warmup reset baseline selection_rule candidates windows'.split())
    require(type(p) is dict and ((set(p) == fields and p.get('schema_version') == 'wp5.observed_wf_policy.v1') or
        (set(p) == fields|{'validation_plan_hash'} and p.get('schema_version') == 'wp5.observed_wf_policy.v2' and _hex(p['validation_plan_hash'])) or
        (set(p) == fields|{'validation_plan_hash','window_policy','regime_policy'} and p.get('schema_version') == 'wp5.observed_wf_policy.v3' and _hex(p['validation_plan_hash']))), 'POLICY_INVALID')
    require(type(p['strategy_id']) is str and p['strategy_id'] and 'H025' not in re.sub('[^A-Z0-9]', '', p['strategy_id'].upper()), 'H025_EXCLUDED')
    require(p['warmup'] == 'NONE' and p['reset'] == 'FRESH_CAPITAL_AND_STATE' and p['baseline'] == 'FIRST_CLOSE' and
        type(p['label_horizon_bars']) is int and p['label_horizon_bars'] == 1 and finite(p['initial_capital']) and p['initial_capital'] > 0,
        'POLICY_INVALID')
    require(type(p['min_train_rows']) is int and p['min_train_rows'] >= 2 and type(p['min_test_rows']) is int and p['min_test_rows'] >= 31 and
        type(p['embargo_ms']) is int and p['embargo_ms'] >= 0 and type(p['selection_rule']) is str and p['selection_rule'], 'POLICY_INVALID')
    require(type(p['candidates']) is list and p['candidates'] and all(type(v) is dict and v for v in p['candidates']) and
        len({_hash(v) for v in p['candidates']}) == len(p['candidates']), 'CANDIDATE_SPACE_INVALID')
    d = contract['dataset_identity']; duration = d['timeframe_ms']
    require(d['coverage_mode'] == 'CONTINUOUS_24_7' and timeframe_ms(p['timeframe']) == duration, 'COVERAGE_PROFILE_UNSUPPORTED')
    timing = contract['static_timing_policy']
    require(all(timing[k] == 0 for k in ('settlement_lag_ms', 'signal_delay_ms', 'order_submission_latency_ms')), 'TIMING_PROFILE_UNSUPPORTED')
    require(type(p['windows']) is list and p['windows'], 'WINDOW_POLICY_INVALID')
    prior_end = None
    for i, w in enumerate(p['windows']):
        require(type(w) is dict and set(w) == {'window_id', 'train', 'test'} and type(w['window_id']) is int and w['window_id'] == i,
            'WINDOW_POLICY_INVALID')
        for name in ('train', 'test'):
            b = w[name]
            require(type(b) is list and len(b) == 2 and all(type(t) is int for t in b) and b[0] < b[1] and
                all((t-d['first_open_ms']) % duration == 0 for t in b), 'WINDOW_POLICY_INVALID')
        require(w['train'][1] <= w['test'][0] and (prior_end is None or prior_end <= w['test'][0]) and
            (w['test'][1]-w['test'][0])//duration >= p['min_test_rows'], 'WINDOW_POLICY_INVALID')
        prior_end = w['test'][1]
    if p['schema_version']=='wp5.observed_wf_policy.v3':reporting.validate_reporting_policy(p,duration)
    return p


def _timing(q, trial, window, rows, index, registry_path, anchor_path):
    if not rows: return None
    require(all(window['test'][0] <= r['bar_open_ms'] < window['test'][1] and
        window['test'][0] <= r['fill_ms'] < window['test'][1] and r['bar_open_ms'] in index and r['fill_ms'] in index for r in rows),
        'TIMING_MEMBERSHIP_INVALID')
    request = {k:q[k] for k in ('research_program_id','experiment_id','contract_hash','contract_record')}
    request.update(mode='TIMING_VALIDATION', trial_id=trial, timing_ledger_rows=rows)
    result = execute_trial(request, registry_path, anchor_path)
    require(result['counter_status'] == 'VERIFIED', 'TIMING_VALIDATION_FAILED')
    return result


def _verify_windows(folder, frozen, binding, window_reports, registry_path, anchor_path):
    q = frozen['request']; p = _validate_policy(frozen['policy'], q['contract_record'])
    data, index = validate_session_coverage(frozen['dataset_manifest'], q['contract_record'], frozen['profile'])
    duration = data['timeframe_ms']; schedule = p['windows']
    require(type(window_reports) is list and 0 < len(window_reports) <= len(schedule), 'WINDOW_SET_INVALID')
    names = {v.name for v in Path(folder).glob('window-*')}
    require(names == {f'window-{i:04d}' for i in range(len(window_reports))}, 'WINDOW_SET_INVALID')
    returns, blocks = [], []; filled = 0; complete = True
    for i, expected in enumerate(window_reports):
        child = Path(folder)/f'window-{i:04d}'; w = schedule[i]
        report = read_artifact(child, 'REPORT.json')
        require(report == expected and report['report_hash'] == _hash({k:v for k,v in report.items() if k!='report_hash'}) and report['window'] == w,
            'WINDOW_BINDING_INVALID')
        training = read_artifact(child, 'TRAINING.json')
        require(training == training_partition(w, index, duration, p['embargo_ms'], q['experiment_id']) and
            len(training['eligible_open_times']) >= p['min_train_rows'], 'TRAINING_BINDING_INVALID')
        selected = read_artifact(child, 'PARAMETERS.json'); opened = read_artifact(child, 'OPEN_OOS.json')
        require(selected['window'] == w and selected['run_hash'] == _hash(frozen) and selected['training_hash'] == _hash(training) and
            selected['selector_hash'] == frozen['selector_hash'] and _hash(selected['parameters']) in {_hash(v) for v in p['candidates']} and
            selected['param_hash'] == param_hash(selected['parameters']) and _hash(selected) == report['parameter_receipt_hash'] and
            opened == dict(event='OPEN_OOS', window=w, parameter_receipt_hash=_hash(selected)), 'PARAMETER_BINDING_INVALID')
        native = read_artifact(child, 'NATIVE_MANIFEST.json')
        require(_hash(native) == report['native_manifest_hash'] and native['param_hash'] == selected['param_hash'] and
            native['strategy']['strategy_hash'] == frozen['strategy']['source_hash'] and native['strategy']['strategy_id'] == p['strategy_id'] and
            native['initial_capital'] == p['initial_capital'] and native['start_timestamp_ms'] == w['test'][0] and
            native['execution_profile'] == frozen['profile'] and native['absolute_risk_limits'] == frozen['risk_limits'] and
            native['comparability_basis'] == {'profile':'HISTORICAL_OBSERVED_WINDOW_BLOCK','window':w}, 'NATIVE_BINDING_INVALID')
        observed = verify_observed_session(child/'session', expected_report_hash=report['session_evidence_hash'])
        session_manifest = read_artifact(child/'session', 'MANIFEST.json')
        require(session_manifest['native_manifest'] == native and session_manifest['strategy_state'] == frozen['strategy']['state'] and
            observed['trial_binding'] == binding and observed['net_returns'] == report['returns'] and
            [v['timestamp_ms'] for v in observed['equity_points']] == report['equity_timestamps'] and
            observed['native_result']['lifecycle_status'] == report['native_lifecycle'], 'WINDOW_BINDING_INVALID')
        if p['schema_version']=='wp5.observed_wf_policy.v3':
            regime=read_artifact(child,'REGIME.json')
            require(regime==reporting.regime_receipt(p,w,regime['endpoint_rows'],index,duration) and
                selected.get('regime_receipt_hash')==_hash(regime) and
                native['regime_policy_hash']==_hash(p['regime_policy']) and
                report.get('selected_param_hash')==selected['param_hash'], 'REGIME_BINDING_INVALID')
            accounting=reporting.execution_accounting(child/'session',observed)
            require(report.get('schema_version')=='wp5.observed_window_report.v3' and
                report['reporting']==reporting.window_reporting(p,w,regime,accounting,report['complete'],duration), 'WF_REPORT_BINDING_INVALID')
        timing = _timing(q, binding['trial_id'], w, observed['audit']['timing_rows'], index, registry_path, anchor_path)
        saved = read_artifact(child, 'TIMING_VALIDATION.json')
        require(_hash(saved) == report['timing_validation_hash'] and
            ((saved is None and timing is None) or (saved is not None and timing is not None and saved['result'] == timing['result'])), 'TIMING_BINDING_INVALID')
        endpoints = list(range(w['test'][0]+duration, w['test'][1]+duration, duration))
        good = report['native_lifecycle'] == 'COMPLETED' and report['equity_timestamps'] == endpoints
        require(type(report['complete']) is bool and report['complete'] == good and (good or i == len(window_reports)-1), 'WINDOW_COMPLETENESS_INVALID')
        complete = complete and good; filled += len(observed['audit']['timing_rows'])
        block = dict(window_id=i, test=w['test'], offset=len(returns), count=len(report['returns']),
                     equity_timestamps=report['equity_timestamps'], session_evidence_hash=report['session_evidence_hash'])
        returns.extend(report['returns']); blocks.append(block)
    require(len(window_reports) == len(schedule) or not complete, 'WINDOW_SET_INVALID')
    derived=dict(complete=complete and len(window_reports)==len(schedule), net_returns=returns, blocks=blocks, filled_decisions=filled)
    if p['schema_version']=='wp5.observed_wf_policy.v3':
        derived['walkforward']=reporting.summarize_windows(p,window_reports,derived['complete'])
    return derived


def _verify_outcome(registry_path, anchor_path, binding, result_hash, lifecycle):
    # Reopen the authoritative ledger read-only; a success flag alone is insufficient.
    from .trial_registry_v2 import _open, _find
    from .ledger_verification import _checked_verify
    with _open(registry_path, anchor_path, False) as (connection, anchor, pending):
        _checked_verify(connection, anchor, {'pending_anchor_update':pending} if pending is not None else {}, binding['research_program_id'])
        trial, outcome = _find(connection, binding['trial_id'])
        require(trial is not None and outcome is not None and
            all(trial[k] == binding[k] for k in ('research_program_id','experiment_id')) and
            outcome['result_hash'] == result_hash and outcome['trial_outcome'] == lifecycle, 'OUTCOME_BINDING_INVALID')


def run_observed_walkforward_trial(request, policy, dataset_manifest, *, profile, risk_limits, strategy, selector,
        registry_path, anchor_path, evidence_directory, read_bars, prior_result_artifacts):
    q = snapshot(request)
    require(q.get('mode') == 'RESERVATION' and check_trial_preflight(q)['reason_code'] is None, 'RESERVATION_BINDING_INVALID')
    c = q['contract_record']; p = _validate_policy(policy, c); attempt = q['attempt_fingerprint_record']
    require(attempt['intended_dataset_zone'] in ('TRAIN','VAL'), 'HOLDOUT_ACCESS_FORBIDDEN')
    bounds = c['boundaries'][attempt['intended_dataset_zone'].lower()]
    require(all(bounds['start_ms'] <= w['train'][0] < w['train'][1] <= w['test'][0] < w['test'][1] <= bounds['end_ms'] for w in p['windows']), 'WINDOW_ZONE_INVALID')
    data, index = validate_session_coverage(dataset_manifest, c, profile)
    require(p['strategy_id'] == strategy.strategy_id == c['experiment_configuration']['strategy_id'], 'STRATEGY_BINDING_INVALID')
    sh = observed_wf_selector_hash(p, selector)
    method = observed_wf_method_hash(p, data, profile=profile, risk_limits=risk_limits, strategy=strategy, selector=selector)
    require(c['experiment_configuration']['param_hash'] == sh and c['experiment_configuration']['method_hash'] == attempt['method_hash'] == method and
        attempt['ordered_input_hashes'] == [_hash(data), _hash(p)], 'METHOD_BINDING_INVALID')
    frozen = dict(schema_version='wp5.observed_wf_run.v1', request=q, policy=p, dataset_manifest=data,
        profile=snapshot(profile), risk_limits=snapshot(risk_limits), strategy=_descriptor(strategy), selector=_descriptor(selector),
        selector_hash=sh, method_hash=method, return_policy=_frequency(p, data['timeframe_ms']))
    if p['schema_version']=='wp5.observed_wf_policy.v3':frozen['schema_version']='wp5.observed_wf_run.v3'
    prior = snapshot(prior_result_artifacts); root = _Store(evidence_directory)
    try:
        raw = root.put('RUN.json', frozen)
        reservation = execute_trial(q, registry_path, anchor_path); root.put('RESERVATION.json', reservation)
        require(reservation['counter_status'] == 'COMMITTED', 'RESERVATION_NOT_COMMITTED')
        binding = {k:q[k] for k in ('research_program_id','experiment_id','contract_hash')}
        binding['trial_id'] = reservation['result']['trial_id']
        def source(a,b):
            root.check('RUN.json', raw)
            return read_bars(a,b)
        reports = run_windows(frozen=frozen, binding=binding, index=index, root=evidence_directory,
            strategy=strategy, selector=selector, read_bars=source,
            record_timing=lambda w, rows:_timing(q,binding['trial_id'],w,rows,index,registry_path,anchor_path))
        root.check('RUN.json', raw)
        require(observed_wf_method_hash(p,data,profile=profile,risk_limits=risk_limits,strategy=strategy,selector=selector)==method, 'METHOD_BINDING_INVALID')
        derived = _verify_windows(evidence_directory,frozen,binding,reports,registry_path,anchor_path)
        eligibility = calculate_statistics(derived['net_returns'],n_total=1,sharpe_values=[])
        valid = derived['complete'] and eligibility['reason_code']=='INSUFFICIENT_TRIALS_FOR_DSR'
        lifecycle = 'COMPLETED_VALID' if valid else 'COMPLETED_INVALID'
        receipt = dict(schema_version='wp5.observed_wf_receipt.v1',run_hash=_hash(frozen),trial_binding=binding,
            windows=reports,derived=derived,eligibility=eligibility,lifecycle_status=lifecycle,return_frequency_hash=_hash(frozen['return_policy']))
        if p['schema_version']=='wp5.observed_wf_policy.v3':receipt['schema_version']='wp5.observed_wf_receipt.v3'
        root.put('EXECUTION_RECEIPT.json',receipt)
        artifact = dict(schema_version='wp5_3.trial_result.v2', **{k:binding[k] for k in ('research_program_id','experiment_id','trial_id')},
            net_returns=derived['net_returns'],execution_evidence_hash=_hash(receipt),return_frequency_hash=_hash(frozen['return_policy']))
        root.put('TRIAL_RESULT.json',artifact)
        outcome = execute_trial(dict(mode='OUTCOME_APPEND',research_program_id=q['research_program_id'],trial_id=binding['trial_id'],
            trial_outcome=lifecycle,result_hash=_hash(artifact)),registry_path,anchor_path)
        root.put('OUTCOME.json',outcome);require(outcome['counter_status']=='VERIFIED','OUTCOME_NOT_RECORDED')
        _verify_outcome(registry_path,anchor_path,binding,_hash(artifact),lifecycle)
        prior[_hash(artifact)]=artifact
        stats = evaluate_trial_statistics(registry_path,anchor_path,research_program_id=q['research_program_id'],trial_id=binding['trial_id'],result_artifacts=prior) if valid else None
        report = dict(schema_version='wp5.observed_wf_report.v1',trial_id=binding['trial_id'],lifecycle_status=lifecycle,
            validation_profile='HISTORICAL_OBSERVED_WINDOW_BLOCK',window_count=len(reports),required_window_count=len(p['windows']),
            return_count=len(derived['net_returns']),blocks=derived['blocks'],continuous_portfolio=False,prospective_verified=False,
            full_execution_timing_status='VERIFIED_FOR_EXECUTED_DECISIONS' if derived['filled_decisions'] else 'NO_EXECUTIONS',
            result_hash=_hash(artifact),execution_receipt_hash=_hash(receipt),statistics=stats)
        if p['schema_version']=='wp5.observed_wf_policy.v3':
            report.update(schema_version='wp5.observed_wf_report.v3',walkforward=derived['walkforward'])
        report['report_hash']=_hash(report);root.put('RESULT.json',report);return report
    finally:root.close()


def verify_observed_walkforward_trial(directory, *, registry_path, anchor_path):
    frozen=read_artifact(directory,'RUN.json');receipt=read_artifact(directory,'EXECUTION_RECEIPT.json')
    artifact=read_artifact(directory,'TRIAL_RESULT.json');q=frozen['request'];attempt=q['attempt_fingerprint_record']
    require(_hash(frozen)==receipt['run_hash'] and _hash(receipt)==artifact['execution_evidence_hash'] and
        artifact['return_frequency_hash']==receipt['return_frequency_hash']==_hash(frozen['return_policy']) and
        frozen['return_policy']==_frequency(frozen['policy'],frozen['dataset_manifest']['timeframe_ms']), 'EXECUTION_RECEIPT_MISMATCH')
    binding=receipt['trial_binding']
    require(all(binding[k]==q[k] for k in ('research_program_id','experiment_id','contract_hash')) and
        all(binding[k]==artifact[k] for k in ('research_program_id','experiment_id','trial_id')), 'EXECUTION_RECEIPT_MISMATCH')
    replay=execute_trial(dict(mode='VERIFICATION_REPLAY',research_program_id=q['research_program_id'],performance_data_accessed=False,
        append_reservation_requested=False,verification_replay_record=dict(replay_of_trial_id=binding['trial_id'],original_input_hashes=attempt['ordered_input_hashes'],
        original_method_hash=attempt['method_hash'],original_result_hash=_hash(artifact))),registry_path,anchor_path)
    require(replay['counter_status']=='REPLAY_VERIFIED','EXECUTION_RECEIPT_MISMATCH')
    derived=_verify_windows(directory,frozen,binding,receipt['windows'],registry_path,anchor_path)
    eligibility=calculate_statistics(derived['net_returns'],n_total=1,sharpe_values=[])
    lifecycle='COMPLETED_VALID' if derived['complete'] and eligibility['reason_code']=='INSUFFICIENT_TRIALS_FOR_DSR' else 'COMPLETED_INVALID'
    require(derived==receipt['derived'] and artifact['net_returns']==derived['net_returns'] and eligibility==receipt['eligibility'] and
        lifecycle==receipt['lifecycle_status'],'EXECUTION_RECEIPT_MISMATCH')
    _verify_outcome(registry_path,anchor_path,binding,_hash(artifact),lifecycle)
    if frozen['policy']['schema_version']=='wp5.observed_wf_policy.v3':
        final=read_artifact(directory,'RESULT.json')
        expected=dict(schema_version='wp5.observed_wf_report.v3',trial_id=binding['trial_id'],lifecycle_status=lifecycle,
            validation_profile='HISTORICAL_OBSERVED_WINDOW_BLOCK',window_count=len(receipt['windows']),
            required_window_count=len(frozen['policy']['windows']),return_count=len(derived['net_returns']),blocks=derived['blocks'],
            continuous_portfolio=False,prospective_verified=False,
            full_execution_timing_status='VERIFIED_FOR_EXECUTED_DECISIONS' if derived['filled_decisions'] else 'NO_EXECUTIONS',
            result_hash=_hash(artifact),execution_receipt_hash=_hash(receipt),walkforward=derived['walkforward'])
        require(set(final)==set(expected)|{'statistics','report_hash'} and
            all(final[k]==v and type(final[k]) is type(v) for k,v in expected.items()),'WF_REPORT_BINDING_INVALID')
        require(frozen['schema_version']=='wp5.observed_wf_run.v3' and receipt['schema_version']=='wp5.observed_wf_receipt.v3' and
            final['schema_version']=='wp5.observed_wf_report.v3' and final['walkforward']==derived['walkforward'] and
            final['report_hash']==_hash({k:v for k,v in final.items() if k!='report_hash'}) and
            final['result_hash']==_hash(artifact) and final['execution_receipt_hash']==_hash(receipt) and
            final['lifecycle_status']==lifecycle,'WF_REPORT_BINDING_INVALID')
    return dict(schema_version='wp5.observed_wf_verification.v1',verification_status='VERIFIED',trial_id=binding['trial_id'],
        result_hash=_hash(artifact),execution_receipt_hash=_hash(receipt),performance_data_accessed=False,counter_incremented=False,
        cached_statistics_revalidated=False,prospective_verified=False)
