"""Historical independent-window orchestration using observed simulated execution."""
import copy
import os
from pathlib import Path
import pandas as pd

from app.forward.manifest import param_hash, code_hash
from app.paper.session.manifest import build_session_manifest
from app.paper.session.ingest import dataset_hash
from app.research.registry_inspection import _hash
from app.research.validation_plan import snapshot, require
from app.research.walkforward import _Store
from app.research.purge_embargo import purge_embargo
from app.research.session_coverage import verify_session_rows
from app.research.observed_session import run_observed_session, verify_observed_session
from app.research import observed_wf_reporting as reporting


def training_partition(window, index, duration, embargo, experiment_id):
    times = [t for t in index if window['train'][0] <= t < window['train'][1]]
    samples = [dict(sample_id=str(t), feature_time_ms=t+duration,
                    label_start_ms=t+duration, label_end_ms=t+2*duration) for t in times]
    request = dict(sample_index=samples, test_window=window['test'], embargo_ms=embargo, experiment_id=experiment_id)
    purge = purge_embargo(request)
    require(purge['purge_status'] == 'VALID', 'PURGE_INVALID')
    retained = set(purge['retained_sample_ids'])
    available = [t for t in times if str(t) in retained and t+2*duration <= window['train'][1] and t+duration in times]
    return dict(request=request, purge=purge, eligible_open_times=available,
                training_row_hashes=[index[t] for t in times], label_rule='NEXT_TRAINING_CLOSE_ONLY')


def build_native(policy, profile, risk, strategy, parameters, window, rows):
    return build_session_manifest(strategy=strategy, params=parameters, asset=policy['asset'], timeframe=policy['timeframe'],
        data_source={'provider': 'caller_verified_closed_rows', 'profile': 'HISTORICAL_OBSERVED_WINDOW_BLOCK'},
        start_timestamp_ms=window['test'][0], initial_capital=policy['initial_capital'],
        position_sizing_policy={'type': 'target_fraction_of_equity', 'max_gross': 1.}, execution_profile=profile,
        absolute_risk_limits=risk, gap_stale_policy={'max_gap_bars': 1},
        regime_policy_hash=_hash(policy['regime_policy'] if policy['schema_version']=='wp5.observed_wf_policy.v3' else {'applicability': 'NOT_EVALUATED_IN_THIS_PROFILE'}),
        consistency_policy_hash=_hash({'profile': policy['schema_version']}),
        reference_range_hash=_hash({'applicability': 'NO_FORWARD_REFERENCE'}),
        comparability_basis={'profile': 'HISTORICAL_OBSERVED_WINDOW_BLOCK', 'window': window},
        dataset_hash=dataset_hash(pd.DataFrame(rows)), code_version='paper-session-observed-v1')


def run_windows(*, frozen, binding, index, root, strategy, selector, read_bars, record_timing):
    """Called after reservation. Trusted callbacks receive isolated causal inputs."""
    policy = frozen['policy']; duration = frozen['dataset_manifest']['timeframe_ms']
    reports = []
    for window in policy['windows']:
        folder = Path(root)/f"window-{window['window_id']:04d}"
        folder.mkdir()
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(parent)
        finally: os.close(parent)
        store = _Store(folder)
        try:
            training = verify_session_rows(read_bars(*window['train']), index, *window['train'])
            partition = training_partition(window, index, duration, policy['embargo_ms'], binding['experiment_id'])
            require(len(partition['eligible_open_times']) >= policy['min_train_rows'], 'INSUFFICIENT_TRAINING')
            training_raw = store.put('TRAINING.json', partition)
            by_time = {r['open_time']: r for r in training}
            features = [snapshot(by_time[t]) for t in partition['eligible_open_times']]
            labels = [dict(sample_open_ms=t, label_end_ms=t+2*duration,
                           one_bar_return=by_time[t+duration]['close']/by_time[t]['close']-1)
                      for t in partition['eligible_open_times']]
            local_selector = copy.deepcopy(selector)
            require(vars(local_selector) == frozen['selector']['state'] and code_hash(local_selector) == frozen['selector']['source_hash'], 'SELECTOR_STATE_DRIFT')
            parameters = snapshot(local_selector.select(snapshot(features), snapshot(labels)))
            require(_hash(parameters) in {_hash(v) for v in policy['candidates']}, 'UNREGISTERED_PARAMETERS')
            selected = dict(window=window, training_hash=_hash(partition), parameters=parameters,
                            param_hash=param_hash(parameters), selector_hash=frozen['selector_hash'],
                            run_hash=_hash(frozen))
            if policy['schema_version']=='wp5.observed_wf_policy.v3':
                regime=reporting.regime_receipt(policy,window,[training[0],training[-1]],index,duration)
                regime_raw=store.put('REGIME.json',regime)
                selected['regime_receipt_hash']=_hash(regime)
            selected_raw = store.put('PARAMETERS.json', selected)
            opened = dict(event='OPEN_OOS', window=window, parameter_receipt_hash=_hash(selected))
            opened_raw = store.put('OPEN_OOS.json', opened)
            rows = verify_session_rows(read_bars(*window['test']), index, *window['test'])
            for name, raw in [('TRAINING.json', training_raw), ('PARAMETERS.json', selected_raw), ('OPEN_OOS.json', opened_raw)]:
                store.check(name, raw)
            if policy['schema_version']=='wp5.observed_wf_policy.v3':store.check('REGIME.json',regime_raw)
            local_strategy = copy.deepcopy(strategy)
            require(vars(local_strategy) == frozen['strategy']['state'], 'STRATEGY_STATE_DRIFT')
            native = build_native(policy, frozen['profile'], frozen['risk_limits'], local_strategy, parameters, window, rows)
            native_raw = store.put('NATIVE_MANIFEST.json', native)
            child = folder/'session'; child.mkdir(); os.fsync(store.fd)
            observed = run_observed_session(manifest=native, profile=frozen['profile'], risk_limits=frozen['risk_limits'],
                strategy=local_strategy, params=parameters, frame=pd.DataFrame(rows), evidence_directory=child, trial_binding=binding)
            observed = verify_observed_session(child, expected_report_hash=observed['report_hash'])
            store.check('NATIVE_MANIFEST.json', native_raw)
            timing = record_timing(window, observed['audit']['timing_rows'])
            store.put('TIMING_VALIDATION.json', timing)
            expected = list(range(window['test'][0]+duration, window['test'][1]+duration, duration))
            complete = observed['native_result']['lifecycle_status'] == 'COMPLETED' and [p['timestamp_ms'] for p in observed['equity_points']] == expected
            report = dict(window=window, parameter_receipt_hash=_hash(selected), native_manifest_hash=_hash(native),
                session_evidence_hash=observed['report_hash'], timing_validation_hash=_hash(timing), complete=complete,
                returns=observed['net_returns'], equity_timestamps=[p['timestamp_ms'] for p in observed['equity_points']],
                native_lifecycle=observed['native_result']['lifecycle_status'])
            if policy['schema_version']=='wp5.observed_wf_policy.v3':
                store.check('REGIME.json',regime_raw)
                accounting=reporting.execution_accounting(child,observed)
                report.update(schema_version='wp5.observed_window_report.v3',selected_param_hash=selected['param_hash'],
                    reporting=reporting.window_reporting(policy,window,regime,accounting,complete,duration))
            report['report_hash'] = _hash(report)
            store.put('REPORT.json', report); reports.append(report)
            if not complete: break
        finally: store.close()
    return reports
