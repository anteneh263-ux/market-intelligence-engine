"""Preregistered retrospective integration pilot; preparation never evaluates prices.

This is orchestration of existing authorities, not a new engine or evidence store.
An operator must approve the exact proposal and explicitly create its empty root.
No automatic retry, evidence repair, tuning, migration, or promotion is provided.
"""
import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.paper.session.profiles import freeze_execution_profile
from app.paper.session.risk import freeze_risk_limits
from .cpcv import runtime_pin
from .holdout import initialize_holdout_store
from .integration_pilot import DAY, _source
from .observed_pipeline import run_observed_pipeline, verify_observed_pipeline
from .observed_plan import implementation_pins, validate_observed_plan
from .observed_walkforward import (observed_wf_method_hash, observed_wf_selector_hash,
    run_observed_walkforward_trial, verify_observed_walkforward_trial)
from .phase5_identity_contract import validate_experiment_contract
from .registry_inspection import _hash, _canonical
from .trial_registry_v2 import initialize_trial_registry, execute_trial
from .validation_plan import require, snapshot, read_artifact
from .walkforward import _Store

VERSION = 'wp5.fullstage_pilot.v1'
EXPOSURES = (.25, .75)
FAMILY = {
    'hypothesis': 'BTC daily close above its trailing arithmetic moving average gates long-only equity exposure',
    'includes': 'All lookback lengths, exposure sizes, cost/risk/window variants, prior implementations and reruns of this hypothesis',
    'excludes': 'Unconditional always-exposed controls, including pilot001; this is a substantive signal-family boundary, not a code-version boundary',
    'relationship_to_pilot001': 'Same repository data and exposure sizes; related engineering precedent, not unseen data or independent strategies',
}


def ms(date):
    return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()*1000)


class MovingAveragePilot:
    strategy_id = 'MIE-P5-MA-GATED-INTEGRATION-002'
    direction = 'long_only'
    version = '1'

    def positions(self, frame, params):
        close = frame['close']
        mean = close.rolling(params['lookback'], min_periods=params['lookback']).mean()
        return (close > mean).astype(float) * params['target']


class FixedSelector:
    def __init__(self, target):
        self.target = target

    def select(self, features, labels):
        return dict(target=self.target, lookback=20)


def _pins():
    import app.research.integration_pilot as source_parser
    return dict(runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        parser_sha256=hashlib.sha256(Path(source_parser.__file__).read_bytes()).hexdigest())


def known_related_history():
    """Read the discovered lineage, not performance; missing records are not zero history.

    This is a bounded known-history guard, not global trial discovery. Clearing it
    requires a separately reviewed lineage decision, never an owner zero-count claim.
    """
    repo=Path(__file__).resolve().parents[2]
    records=[]
    for strategy_id in ('H-013','H-026'):
        for suffix in ('.json','.srp-matrix-v2.json'):
            relative=f'knowledge_base/results/{strategy_id}{suffix}'
            path=repo/relative
            if not path.is_file() or path.is_symlink():
                records.append(dict(path=relative,status='KNOWN_LINEAGE_UNAVAILABLE'))
                continue
            raw=path.read_bytes();value=json.loads(raw)
            records.append(dict(path=relative,sha256=hashlib.sha256(raw).hexdigest(),status='RELATED_EVALUATION_REQUIRES_RECONCILIATION',
                metadata={k:value.get(k) for k in ('strategy_id','assets','provider','is_real','matrix_version','matrix_hash','request_hash')}))
    return records


def _request(policy, data, rows, profile, risk, scope, owner, created, target, boundaries):
    selector = FixedSelector(target)
    method = observed_wf_method_hash(policy, data, profile=profile, risk_limits=risk,
        strategy=MovingAveragePilot(), selector=selector)
    raw = dict(schema_version='wp5_0a1.request.v4', research_scope=scope,
        research_program_registration=dict(schema_version='wp5_0a1.research_program_registration.v1',
            program_label='MA-gated retrospective integration family', owner=owner, created_utc_ms=created),
        dataset_identity=dict(schema_version='wp5_0a1.dataset_identity.v3', dataset_hash=_hash(data),
            timeframe_ms=DAY, first_open_ms=rows[0]['open_time'], last_open_ms=rows[-1]['open_time'],
            dataset_end_exclusive_ms=rows[-1]['open_time']+DAY, rows=len(rows), coverage_mode='CONTINUOUS_24_7'),
        static_timing_policy=dict(schema_version='wp5_0a1.static_timing_policy.v3', bar_duration_ms=DAY,
            settlement_lag_ms=0, feature_availability_rule='AT_BAR_CLOSE', signal_delay_ms=0,
            order_submission_latency_ms=0, order_submission_rule='AT_ORDER_ELIGIBILITY',
            fill_rule='FIRST_OBSERVED_VALID_BAR_OPEN_AT_OR_AFTER_ORDER_ELIGIBILITY'),
        boundaries=dict(schema_version='wp5_0a1.boundaries.v1', **boundaries),
        experiment_configuration=dict(schema_version='wp5_0a1.experiment_configuration.v1',
            strategy_id=MovingAveragePilot.strategy_id, method_hash=method,
            param_hash=observed_wf_selector_hash(policy, selector)),
        preregistration=dict(schema_version='wp5_0a1.preregistration.v1', registered_utc_ms=created,
            registered_by=owner, registration_reference='phase5-fullstage-pilot-002'),
        expected_identities={}, timing_ledger_schema_version='wp5_0.timing_ledger_row.v1')
    c = validate_experiment_contract(raw)
    require(c['contract_status']=='VALID', 'PILOT_CONTRACT_INVALID:'+str(c.get('reason_code')))
    return dict(mode='RESERVATION', **{k:c[k] for k in ('research_program_id','experiment_id','contract_hash')},
        contract_record=c, attempt_fingerprint_record=dict(research_program_id=c['research_program_id'],
            experiment_id=c['experiment_id'], method_hash=method,
            ordered_input_hashes=[_hash(data),_hash(policy)], intended_dataset_zone='VAL'))


def prepare_contract(*, source_path, source_sha256, evidence_directory, owner, created_utc_ms=None):
    """Pure preparation: parse/seal all rows; no strategy, statistics, or store calls."""
    require(type(owner) is str and bool(owner.strip()), 'OWNER_REQUIRED')
    root = Path(evidence_directory)
    require(root.is_absolute() and root.resolve()==root and not root.is_symlink(), 'ROOT_INVALID')
    require('H025' not in ''.join(c for c in str(root).upper() if c.isalnum()), 'H025_EXCLUDED')
    created = time.time_ns()//1000000 if created_utc_ms is None else created_utc_ms
    require(type(created) is int and created>0, 'CREATED_TIME_INVALID')
    rows = _source(source_path,source_sha256)
    profile = freeze_execution_profile('BTC/USD')
    risk = freeze_risk_limits(max_drawdown=.9,max_session_loss=.9,max_daily_loss=.9,max_loss_per_trade=.9)
    coverage = dict(schema_version='wp5.session_coverage.v1',coverage_mode='CONTINUOUS_24_7',calendar_id=None,
        calendar_policy_hash=_hash(dict(market_hours=profile['market_hours'],timeframe_ms=DAY)),
        open_times=[r['open_time'] for r in rows])
    data = dict(schema_version='wp5.session_dataset.v1',timeframe_ms=DAY,
        rows=[dict(open_time=r['open_time'],row_hash=_hash(r)) for r in rows],coverage=coverage)
    # TRAIN is identity context; every actual training/test window is in VAL.
    bounds = dict(train=dict(start_ms=rows[0]['open_time'],end_ms=ms('2024-01-01')),
        val=dict(start_ms=ms('2024-01-01'),end_ms=ms('2025-01-01')),
        holdout=dict(start_ms=ms('2025-01-01'),end_ms=ms('2026-01-01')))
    require(rows[0]['open_time']<bounds['train']['end_ms'] and rows[-1]['open_time']+DAY>=bounds['holdout']['end_ms'], 'SOURCE_PERIOD_INVALID')
    held = [r for r in rows if bounds['holdout']['start_ms']<=r['open_time']<bounds['holdout']['end_ms']]
    text = 'Execution costs reduce net returns. Stress results can only preserve or downgrade readiness; they cannot choose parameters.'
    corpus = [dict(source_id='preregistered-pilot-method-note-v1',content=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),claim_key='costs',stance='reduce')]
    pin = json.loads((Path(__file__).resolve().parents[2]/'docs/specs/phase5-cpcv-runtime-pin-macos-py314.json').read_text())
    plan = dict(schema_version='wp5.observed_plan.v3',strategy_id=MovingAveragePilot.strategy_id,
        dataset_manifest_hash=_hash(data),code_pins=implementation_pins(),
        holdout_access_policy='AFTER_VALID_MC_RETAIN_DOWNGRADE',
        cpcv=dict(cpcv_applicability='REQUIRED',applicability_justification='Preregistered real-library fold geometry and diagnostics',
            folds_policy=dict(n_folds=3,n_test_folds=2,purged_size=1,embargo_size=0,embargo_ms=0),pinned_lib=pin),
        mc_policy=dict(master_seed=20261006,profile_grid=[
            dict(taker_fee_bps=10.,slippage_bps=5.,half_spread_bps=1.),
            dict(taker_fee_bps=20.,slippage_bps=10.,half_spread_bps=2.)],
            parameter_grid=[{},dict(lookback=30)],repetitions=2,n_scenarios=8,
            execution_delay_bars=1,missed_fill_probability=.1,order_perturbation='SHUFFLE_EXPOSURE_BLOCKS',
            downgrade_rule=dict(min_total_return=0.,max_drawdown=.3,min_fraction_surviving=.75)),
        holdout_policy=dict(dataset_hash=_hash(held),parameter_source='LAST_WF_TRAIN',dsr_min=.95,
            min_total_return=0.,max_drawdown=.3),rag_policy=dict(corpus_hash=_hash(corpus),max_items=1))
    validate_observed_plan(plan)
    start = bounds['val']['start_ms']
    windows = [dict(window_id=i,train=[start+i*60*DAY,start+(i*60+90)*DAY],
        test=[start+(i*60+90)*DAY,start+(i*60+150)*DAY]) for i in range(4)]
    policy = dict(schema_version='wp5.observed_wf_policy.v3',strategy_id=MovingAveragePilot.strategy_id,
        asset='BTC/USD',timeframe='1d',initial_capital=100000.,min_train_rows=30,min_test_rows=31,
        embargo_ms=0,label_horizon_bars=1,warmup='NONE',reset='FRESH_CAPITAL_AND_STATE',baseline='FIRST_CLOSE',
        selection_rule='PREREGISTERED_FIXED_SELECTOR_NO_RANKING',
        candidates=[dict(target=t,lookback=20) for t in EXPOSURES],windows=windows,validation_plan_hash=_hash(plan),
        window_policy=dict(mode='rolling',start_ms=start,train_span_ms=90*DAY,test_span_ms=60*DAY,step_ms=60*DAY,n_windows=4),
        regime_policy=dict(rule='TRAIN_CLOSE_RETURN_V1',down_threshold=-.01,up_threshold=.01))
    scope = dict(schema_version='wp5_0a1.research_scope.v1',hypothesis_family_hash=_hash(FAMILY['hypothesis']),
        strategy_family_hash=_hash(dict(rule='LONG_ONLY_CLOSE_ABOVE_TRAILING_ARITHMETIC_MEAN',family_extent=FAMILY['includes'])),
        asset_universe_hash=_hash(['BTC/USD']),target_definition_hash=_hash('NEXT_OPEN_TARGET_EQUITY_FRACTION'),
        evaluation_objective_hash=_hash('RETROSPECTIVE_NET_RETURN_GOVERNED_INTEGRATION'))
    trials = [dict(target=t,role='COUNTED_WF_PREDECESSOR' if i==0 else 'DESIGNATED_PIPELINE',
        request=_request(policy,data,rows,profile,risk,scope,owner,created,t,bounds)) for i,t in enumerate(EXPOSURES)]
    return dict(schema_version=VERSION,owner=owner,created_utc_ms=created,evidence_directory=str(root),
        source_path=str(Path(source_path).resolve()),source_sha256=source_sha256,source_provenance='REPOSITORY_SAMPLE_NOT_INDEPENDENTLY_VERIFIED',
        preparation_pins=_pins(),family=FAMILY,known_related_history=known_related_history(),
        research_scope=scope,boundaries=bounds,dataset_manifest=data,
        profile=profile,risk_limits=risk,plan=plan,policy=policy,trials=trials,corpus=corpus,
        advisory_query='execution costs',sealing_reads_all_rows=True,retrospective_holdout=True,unseen_holdout_claimed=False,
        signal_rule='Completed current close > trailing 20-close mean INCLUDING current close; first 19 and equality flat; fresh window/holdout state, no warmup',
        stop_policy='One attempt; DSR below 0.95 or invalid predecessor stops. No tuning/replacement/retry. Downstream stages NOT_EVALUATED after stop.',
        family_history_requirement='Owner must attest no prior performance evaluations anywhere in defined family; unknown/nonzero requires reconciliation before this runner',
        promotion_authorized=False,prospective_verified=False)


def _authorize(c, a):
    fields = {'schema_version','contract_hash','evidence_directory','approved_by','approval_reference','approved_utc_ms',
        'prior_family_evaluations','family_history_attestation','retrospective_holdout_acknowledged'}
    require(type(a) is dict and set(a)==fields and a['schema_version']=='wp5.fullstage_pilot_authorization.v1' and
        a['contract_hash']==_hash(c) and a['evidence_directory']==c['evidence_directory'] and a['approved_by']==c['owner'] and
        type(a['approval_reference']) is str and bool(a['approval_reference'].strip()) and
        type(a['approved_utc_ms']) is int and a['approved_utc_ms']>=c['created_utc_ms'] and
        type(a['prior_family_evaluations']) is int and a['prior_family_evaluations']==0 and
        a['family_history_attestation']=='NO_PRIOR_EVALUATIONS_IN_DEFINED_FAMILY' and
        a['retrospective_holdout_acknowledged'] is True, 'PILOT_AUTHORIZATION_INVALID')
    require(c.get('known_related_history')==[], 'PILOT_PRIOR_FAMILY_HISTORY_REQUIRES_RECONCILIATION')


def preflight(c, a):
    c=snapshot(c);a=snapshot(a);_authorize(c,a)
    require(runtime_pin()==c['plan']['cpcv']['pinned_lib'], 'PILOT_RUNTIME_MISMATCH')
    expected=prepare_contract(source_path=c['source_path'],source_sha256=c['source_sha256'],
        evidence_directory=c['evidence_directory'],owner=c['owner'],created_utc_ms=c['created_utc_ms'])
    require(c==expected, 'PILOT_CONTRACT_CHANGED')
    return c,a


def run_fullstage_pilot(contract, authorization):
    c,a=preflight(contract,authorization)
    root=Path(c['evidence_directory']);store=_Store(root)
    try:
        frozen=store.put('PLAN.json',c);store.put('AUTHORIZATION.json',a)
        reg=str(root/'registry.sqlite');anchor=str(root/'anchor.json')
        initialize_trial_registry(reg,anchor)
        hold=root/'holdout-access';hold.mkdir();os.fsync(store.fd);initialize_holdout_store(hold)
        allowed={tuple(w[z]) for w in c['policy']['windows'] for z in ('train','test')}
        def read(a,b):
            require((a,b) in allowed,'SOURCE_ZONE_FORBIDDEN');store.check('PLAN.json',frozen)
            return [r for r in _source(c['source_path'],c['source_sha256']) if a<=r['open_time']<b]
        def held():
            store.check('PLAN.json',frozen);b=c['boundaries']['holdout']
            return [r for r in _source(c['source_path'],c['source_sha256']) if b['start_ms']<=r['open_time']<b['end_ms']]
        common=dict(profile=c['profile'],risk_limits=c['risk_limits'],strategy=MovingAveragePilot(),
            registry_path=reg,anchor_path=anchor,read_bars=read)
        first=root/'predecessor';first.mkdir();os.fsync(store.fd)
        result=run_observed_walkforward_trial(c['trials'][0]['request'],c['policy'],c['dataset_manifest'],
            **common,selector=FixedSelector(.25),evidence_directory=first,prior_result_artifacts={})
        verify_observed_walkforward_trial(first,registry_path=reg,anchor_path=anchor)
        pipeline=None;verification=None
        if result['lifecycle_status']=='COMPLETED_VALID':
            artifact=read_artifact(first,'TRIAL_RESULT.json');child=root/'pipeline';child.mkdir();os.fsync(store.fd)
            pipeline=run_observed_pipeline(c['trials'][1]['request'],c['policy'],c['dataset_manifest'],c['plan'],
                **common,selector=FixedSelector(.75),evidence_directory=child,holdout_directory=hold,
                read_holdout=held,prior_result_artifacts={_hash(artifact):artifact},corpus=c['corpus'],
                advisory_query=c['advisory_query'],actor=c['owner'])
            if pipeline['pipeline_status']=='COMPLETED':
                verification=verify_observed_pipeline(child,expected_report_hash=pipeline['report_hash'])
        program=c['trials'][0]['request']['research_program_id']
        state=execute_trial(dict(mode='REGISTRY_STATE',research_program_id=program),reg,anchor)
        report=dict(schema_version=VERSION,contract_hash=_hash(c),authorization_hash=_hash(a),research_program_id=program,
            N_total=state['N_total'],predecessor_report_hash=result['report_hash'],
            pipeline_report_hash=pipeline['report_hash'] if pipeline else None,
            attempt_status=pipeline['pipeline_status'] if pipeline else 'STOPPED',
            stopped_at=pipeline['stopped_at'] if pipeline else 'PREDECESSOR',
            reason_code=pipeline['reason_code'] if pipeline else 'PREDECESSOR_INVALID',
            stages={k:('EVALUATED' if pipeline and pipeline[k] is not None else 'NOT_EVALUATED') for k in ('cpcv','trial','statistics','mc','holdout')},
            full_chain_verified=verification is not None,retrospective_holdout=True,unseen_holdout_claimed=False,
            promotion_authorized=False,prospective_verified=False)
        report['report_hash']=_hash(report);store.check('PLAN.json',frozen);store.put('REPORT.json',report)
        return report
    finally:
        store.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare')
    for flag in ('source','sha256','evidence-root','owner','output'):prep.add_argument('--'+flag,required=True)
    run=sub.add_parser('run');run.add_argument('--contract',required=True);run.add_argument('--authorization',required=True)
    args=p.parse_args()
    if args.command=='prepare':
        c=prepare_contract(source_path=args.source,source_sha256=args.sha256,evidence_directory=args.evidence_root,owner=args.owner)
        with open(args.output,'xb') as f:f.write(_canonical(c));f.flush();os.fsync(f.fileno())
        print(json.dumps(dict(contract_hash=_hash(c),output=args.output,evaluation_performed=False)))
    else:
        c=json.loads(Path(args.contract).read_text());a=json.loads(Path(args.authorization).read_text())
        print(json.dumps(run_fullstage_pilot(c,a)))


if __name__=='__main__':main()
