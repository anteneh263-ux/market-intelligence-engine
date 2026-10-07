"""Fixed, non-H025 integration pilot. Simulated replay, never a strategy selection service."""
import argparse
import csv
import hashlib
import io
import math
import os
import time
from pathlib import Path

import pandas as pd

from app.paper.session.manifest import build_session_manifest
from app.paper.session.profiles import freeze_execution_profile
from app.paper.session.risk import freeze_risk_limits
from app.paper.session.ingest import dataset_hash
from .registry_inspection import _hash
from .validation_plan import require, snapshot
from .walkforward import _Store
from .phase5_identity_contract import validate_experiment_contract
from .trial_registry_v2 import initialize_trial_registry, execute_trial
from .session_trial import run_session_trial, session_method_hash, verify_session_trial
from .statistics_report import evaluate_statistics_report

DAY = 86400000
VERSION = 'wp5.integration_pilot.v1'
EXPOSURES = (.25, .75)


class FixedExposurePilot:
    strategy_id = 'MIE-P5-INTEGRATION-PILOT-001'
    direction = 'long_only'
    version = '1'

    def positions(self, frame, params):
        return pd.Series(params['target'], index=frame.index)


def _source(path, expected_sha256):
    require(not Path(path).is_symlink(), 'SOURCE_SYMLINK_REFUSED')
    raw = Path(path).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected_sha256, 'SOURCE_HASH_MISMATCH')
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8')))
    require(reader.fieldnames == ['timestamp', 'open', 'high', 'low', 'close', 'volume'], 'SOURCE_SCHEMA_INVALID')
    rows = []
    for row in reader:
        item = dict(open_time=int(row['timestamp']) * 1000,
                    **{k: float(row[k]) for k in ('open', 'high', 'low', 'close', 'volume')})
        require(all(math.isfinite(v) for v in item.values()) and
                item['low'] > 0 and item['low'] <= min(item['open'], item['close']) and
                item['high'] >= max(item['open'], item['close']) and item['volume'] >= 0,
                'SOURCE_VALUES_INVALID')
        rows.append(item)
    require(len(rows) >= 34 and all(b['open_time']-a['open_time'] == DAY for a, b in zip(rows, rows[1:])),
            'SOURCE_COVERAGE_INVALID')
    return rows


def run_integration_pilot(*, source_path, source_sha256, evidence_directory, boundaries, owner):
    """Caller creates the approved empty evidence root; this function claims it once.

    Sealing parses the whole CSV for hashes. Only VAL rows reach strategy execution.
    Source provenance is declared, not independently certified. No holdout evaluator
    is called, including when both DSRs pass; further stages need their own contract.
    """
    require(type(owner) is str and bool(owner.strip()), 'OWNER_REQUIRED')
    bounds = snapshot(boundaries)
    require(set(bounds) == {'train', 'val', 'holdout'}, 'BOUNDARIES_INVALID')
    rows = _source(source_path, source_sha256)
    profile = freeze_execution_profile('BTC/USD')
    risk = freeze_risk_limits(max_drawdown=.9, max_session_loss=.9, max_daily_loss=.9, max_loss_per_trade=.9)
    coverage = dict(schema_version='wp5.session_coverage.v1', coverage_mode='CONTINUOUS_24_7', calendar_id=None,
                    calendar_policy_hash=_hash(dict(market_hours=profile['market_hours'], timeframe_ms=DAY)),
                    open_times=[r['open_time'] for r in rows])
    data = dict(schema_version='wp5.session_dataset.v1', timeframe_ms=DAY,
                rows=[dict(open_time=r['open_time'], row_hash=_hash(r)) for r in rows], coverage=coverage)
    sampling = dict(schema_version='wp5.session_returns.v1', baseline='FIRST_CLOSE', frequency='UTC_GRID',
                    step_ms=DAY, calendar_policy_hash=coverage['calendar_policy_hash'])
    val = bounds['val']
    selected = [r for r in rows if val['start_ms'] <= r['open_time'] < val['end_ms']]
    require(len(selected) >= 31, 'VALIDATION_TOO_SHORT')
    segment = dict(schema_version='wp5.session_segment.v1', start_ms=val['start_ms'], end_ms=val['end_ms'],
                   engine_dataset_hash=dataset_hash(pd.DataFrame(selected)), content_hash=_hash(selected))
    now = time.time_ns() // 1000000
    plan = dict(schema_version=VERSION, owner=owner, created_utc_ms=now, asset='BTC/USD', timeframe='1d',
                exposures=list(EXPOSURES), source_path=str(Path(source_path).resolve()), source_sha256=source_sha256,
                dataset_manifest_hash=_hash(data), boundaries=bounds, return_policy=sampling,
                execution_profile=profile, risk_limits=risk, threshold_policy={'dsr_min': .95},
                runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                evidence_kind='SIMULATED_REPLAY', prospective_verified=False,
                source_provenance='REPOSITORY_SAMPLE_NOT_INDEPENDENTLY_VERIFIED',
                sealing_reads_all_source_rows=True, holdout_evaluation_authorized=False,
                purpose='Test registration, reservation, execution timing, outcomes, statistics and read-only replay; no tuning')
    requests = []
    for target in EXPOSURES:
        strategy = FixedExposurePilot()
        params = {'target': target}
        native = build_session_manifest(strategy=strategy, params=params, asset='BTC/USD', timeframe='1d',
            data_source={'provider': 'repository_csv', 'source_sha256': source_sha256,
                         'provenance': plan['source_provenance']}, start_timestamp_ms=selected[0]['open_time'], initial_capital=100000.,
            position_sizing_policy={'type': 'target_fraction_of_equity', 'max_gross': 1.}, execution_profile=profile,
            absolute_risk_limits=risk, gap_stale_policy={'max_gap_bars': 1},
            regime_policy_hash=_hash({'applicability': 'NOT_APPLICABLE_FIXED_EXPOSURE'}),
            consistency_policy_hash=_hash({'applicability': 'NOT_APPLICABLE_INTEGRATION_ONLY'}),
            reference_range_hash=_hash({'applicability': 'NO_FORWARD_REFERENCE'}),
            comparability_basis={'profile': VERSION, 'purpose': 'integration-only'},
            dataset_hash=segment['engine_dataset_hash'], code_version='paper-session-observed-v1')
        method = session_method_hash(native, data, segment, strategy_state=vars(strategy), return_policy=sampling)
        scope = {k: _hash({'pilot_plan': _hash(plan), 'scope': k}) for k in (
            'hypothesis_family_hash', 'strategy_family_hash', 'asset_universe_hash', 'target_definition_hash', 'evaluation_objective_hash')}
        raw = dict(schema_version='wp5_0a1.request.v4',
            research_scope=dict(schema_version='wp5_0a1.research_scope.v1', **scope),
            research_program_registration=dict(schema_version='wp5_0a1.research_program_registration.v1',
                program_label='Phase 5 integration pilot 001', owner=owner, created_utc_ms=now),
            dataset_identity=dict(schema_version='wp5_0a1.dataset_identity.v3', dataset_hash=_hash(data),
                timeframe_ms=DAY, first_open_ms=rows[0]['open_time'], last_open_ms=rows[-1]['open_time'],
                dataset_end_exclusive_ms=rows[-1]['open_time']+DAY, rows=len(rows), coverage_mode='CONTINUOUS_24_7'),
            static_timing_policy=dict(schema_version='wp5_0a1.static_timing_policy.v3', bar_duration_ms=DAY,
                settlement_lag_ms=0, feature_availability_rule='AT_BAR_CLOSE', signal_delay_ms=0, order_submission_latency_ms=0,
                order_submission_rule='AT_ORDER_ELIGIBILITY', fill_rule='FIRST_OBSERVED_VALID_BAR_OPEN_AT_OR_AFTER_ORDER_ELIGIBILITY'),
            boundaries=dict(schema_version='wp5_0a1.boundaries.v1', **bounds),
            experiment_configuration=dict(schema_version='wp5_0a1.experiment_configuration.v1',
                strategy_id=strategy.strategy_id, param_hash=native['param_hash'], method_hash=method),
            preregistration=dict(schema_version='wp5_0a1.preregistration.v1', registered_utc_ms=now,
                registered_by=owner, registration_reference='phase5-integration-pilot-001'),
            expected_identities={}, timing_ledger_schema_version='wp5_0.timing_ledger_row.v1')
        contract = validate_experiment_contract(raw)
        require(contract['contract_status'] == 'VALID', 'PILOT_CONTRACT_INVALID:'+str(contract.get('reason_code')))
        q = dict(mode='RESERVATION', **{k: contract[k] for k in ('research_program_id', 'experiment_id', 'contract_hash')},
                 contract_record=contract, attempt_fingerprint_record=dict(research_program_id=contract['research_program_id'],
                    experiment_id=contract['experiment_id'], method_hash=method,
                    ordered_input_hashes=[_hash(data), _hash(segment)], intended_dataset_zone='VAL'))
        requests.append((q, native, params))
    require(requests[0][0]['research_program_id'] == requests[1][0]['research_program_id'], 'PROGRAM_MISMATCH')
    folder = Path(evidence_directory)
    store = _Store(folder)
    try:
        frozen = dict(plan=plan, dataset_manifest=data, segment_manifest=segment,
                      trials=[dict(request=q, manifest=m, params=p) for q, m, p in requests])
        frozen_bytes = store.put('PLAN.json', frozen)
        reg, anchor = str(folder/'registry.sqlite'), str(folder/'anchor.json')
        initialize_trial_registry(reg, anchor)
        results, artifacts = [], {}
        def reader(a, b):
            require((a, b) == (val['start_ms'], val['end_ms']), 'SOURCE_ZONE_FORBIDDEN')
            store.check('PLAN.json', frozen_bytes)
            current = _source(source_path, source_sha256)
            return [r for r in current if a <= r['open_time'] < b]
        for i, (q, native, params) in enumerate(requests):
            store.check('PLAN.json', frozen_bytes)
            child = folder/f'trial-{i+1:02d}'
            child.mkdir(); os.fsync(store.fd)
            result = run_session_trial(q, native, data, segment, profile=profile, risk_limits=risk,
                strategy=FixedExposurePilot(), params=params, registry_path=reg, anchor_path=anchor,
                evidence_directory=child, read_bars=reader, prior_result_artifacts=artifacts, return_policy=sampling)
            from .validation_plan import read_artifact
            artifact = read_artifact(child, 'TRIAL_RESULT.json')
            artifacts[_hash(artifact)] = artifact
            results.append(result)
        verifications, statistics = [], []
        program = requests[0][0]['research_program_id']
        before = execute_trial(dict(mode='REGISTRY_STATE', research_program_id=program), reg, anchor)
        for i, result in enumerate(results):
            verifications.append(verify_session_trial(folder/f'trial-{i+1:02d}', registry_path=reg, anchor_path=anchor))
            statistics.append(evaluate_statistics_report(reg, anchor, research_program_id=program,
                trial_id=result['trial_id'], result_artifacts=artifacts, threshold_policy=plan['threshold_policy']))
        after = execute_trial(dict(mode='REGISTRY_STATE', research_program_id=program), reg, anchor)
        require(before == after and after['N_total'] == 2, 'REPLAY_CHANGED_REGISTRY')
        valid = all(r['lifecycle_status'] == 'COMPLETED_VALID' for r in results)
        passed = valid and all(s['stats_status'] == 'VALID' and s['decision_input']['dsr_pass'] for s in statistics)
        report = dict(schema_version=VERSION, plan_hash=_hash(frozen), research_program_id=program,
            engineering_status='VERIFIED' if valid else 'INVALID_TRIAL_RECORDED', N_total=after['N_total'],
            exposures=list(EXPOSURES), trials=results, verification=verifications, statistics=statistics,
            statistical_gate='PASS' if passed else 'STOP', holdout_evaluated=False, monte_carlo_evaluated=False,
            promotion_authorized=False, prospective_verified=False, source_provenance=plan['source_provenance'],
            limitations=['Two correlated fixed exposures; not independent strategies',
                         'This pilot does not execute the separate historical WF/CPCV/MC/holdout pipeline',
                         'No prospective or profitability conclusion'])
        report['report_hash'] = _hash(report)
        store.check('PLAN.json', frozen_bytes)
        store.put('REPORT.json', report)
        return report
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--evidence-root', required=True)
    parser.add_argument('--owner', required=True)
    args = parser.parse_args()
    def ms(date):
        from datetime import datetime, timezone
        return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()*1000)
    bounds = dict(train=dict(start_ms=ms('2018-01-01'), end_ms=ms('2024-01-01')),
                  val=dict(start_ms=ms('2024-01-01'), end_ms=ms('2025-01-01')),
                  holdout=dict(start_ms=ms('2025-01-01'), end_ms=ms('2026-07-09')))
    report = run_integration_pilot(source_path=args.source, source_sha256=args.sha256,
        evidence_directory=args.evidence_root, boundaries=bounds, owner=args.owner)
    import json
    print(json.dumps({k: report[k] for k in ('engineering_status', 'statistical_gate', 'N_total', 'report_hash')}))


if __name__ == '__main__':
    main()
