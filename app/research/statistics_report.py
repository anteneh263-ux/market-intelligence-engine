"""Ordered R3 statistics checks over actual ledger-bound Form A or Form B."""
import math

from .registry_inspection import _hash,_hex
from .validation_plan import snapshot
from .bound_statistics import evaluate_trial_statistics
from .aggregate_statistics import evaluate_aggregate_statistics
from .statistics_math import calculate_statistics
from .trial_registry_v2 import _open
from .ledger_verification import _checked_verify

_CHECKS=('RETURN_SERIES','MOMENTS','TRIAL_COUNTER','N_DOMAIN','SHARPE_VECTOR_BINDING',
    'CROSS_TRIAL_VARIANCE','PSR_COMPUTE','DEFLATION_BENCHMARK','DSR_COMPUTE',
    'INDEPENDENCE_DISCLOSURE','THRESHOLD_POLICY','STATS_HASH')


class _Stop(Exception):pass


def _finite(v):return type(v) in (int,float) and math.isfinite(v)


def evaluate_statistics_report(registry_path,anchor_path,*,research_program_id,trial_id,result_artifacts,
                               threshold_policy,aggregate_bundle=None,expected_method_hash=None):
    """Derive inputs from authoritative results and execute ordered R3 checks.

    No caller counter, variance, pass flag or threshold-change assertion trusted.
    Ledger snapshot changes during evaluation refuse; no market-data reads/writes.
    """
    report=calculate_statistics([],n_total=None,sharpe_values=[])
    report.update(schema_version='wp5_3.statistics_report.v1',reason_code=None,
        research_program_id=research_program_id if _hex(research_program_id) else None,
        trial_id=trial_id if _hex(trial_id) else None,ledger_snapshot_hash=None,bound_report_hash=None,
        threshold_policy={'dsr_min':.95},checks=[dict(check_id=k,status='NOT_EVALUATED') for k in _CHECKS])
    current=0
    def check(ok,reason):
        report['checks'][current]['status']='PASS' if ok else 'FAIL'
        if not ok:
            report['reason_code']=reason
            raise _Stop()
    try:
        try:artifacts=snapshot(result_artifacts)
        except (ValueError,TypeError,OverflowError,RecursionError):artifacts=None
        targets=[v for v in artifacts.values() if type(v) is dict and v.get('research_program_id')==research_program_id and
            v.get('trial_id')==trial_id] if type(artifacts) is dict else []
        returns=targets[0].get('net_returns') if len(targets)==1 else None
        check(type(returns) is list and all(_finite(v) for v in returns),'RETURN_SERIES_INVALID')
        current=1
        moments=calculate_statistics(returns,n_total=1,sharpe_values=[])
        check(moments['reason_code']=='INSUFFICIENT_TRIALS_FOR_DSR',moments['reason_code'] or 'MOMENTS_INVALID')
        report.update(psr=moments['psr'],sr_observed=moments['sr_observed'],moments=moments['moments'])
        current=2
        state=None
        if _hex(research_program_id) and _hex(trial_id):
            try:
                with _open(registry_path,anchor_path,False) as (conn,anchor,pending):
                    state=_checked_verify(conn,anchor,{'pending_anchor_update':pending} if pending is not None else {},research_program_id)
            except Exception:
                state=None
        check(type(state) is dict and type(state.get('N_total')) is int and state['N_total']>=1,'TRIAL_COUNTER_INVALID')
        report.update(N_total=state['N_total'],N_effective=state['N_total'],ledger_snapshot_hash=state['ledger_chain_hash'])
        current=3
        check(state['N_total']>=2,'INSUFFICIENT_TRIALS_FOR_DSR')
        current=4
        bound=evaluate_trial_statistics(registry_path,anchor_path,research_program_id=research_program_id,
            trial_id=trial_id,result_artifacts=artifacts)
        allowed={'SHARPE_VECTOR_INCOMPLETE','SHARPE_VECTOR_NONFINITE','SHARPE_VECTOR_LENGTH_MISMATCH','TRIAL_VECTOR_BINDING_INVALID'}
        check(bound['binding_status']=='VERIFIED',bound['reason_code'] if bound['reason_code'] in allowed else 'TRIAL_VECTOR_BINDING_INVALID')
        check(bound['ledger_snapshot_hash']==state['ledger_chain_hash'] and bound['statistics']['N_total']==state['N_total'],
            'TRIAL_VECTOR_BINDING_INVALID')
        stats=bound['statistics']
        # Artifacts that supplied moments are the same snapshot verified by Form A.
        check(stats['sr_observed']==report['sr_observed'] and stats['moments']==report['moments'],'TRIAL_VECTOR_BINDING_INVALID')
        vector_reason=stats['reason_code']
        check(vector_reason not in allowed,vector_reason or 'TRIAL_VECTOR_BINDING_INVALID')
        check(type(stats['K']) is int and 2<=stats['K']<=state['N_total'] and len(bound['trial_ids'])==stats['K'],
            'SHARPE_VECTOR_LENGTH_MISMATCH')
        form_b=aggregate_bundle is not None or expected_method_hash is not None
        if form_b:
            check(aggregate_bundle is not None and expected_method_hash is not None,'TRIAL_VECTOR_BINDING_INVALID')
            aggregate=evaluate_aggregate_statistics(aggregate_bundle,registry_path,anchor_path,
                research_program_id=research_program_id,trial_id=trial_id,result_artifacts=artifacts,
                expected_method_hash=expected_method_hash)
            # A variance mismatch belongs to the next check; other binding defects stop here.
            reason=aggregate['reason_code']
            check(aggregate['binding_status']=='VERIFIED' or reason in ('VAR_SR_TRIALS_MISMATCH','DEFLATION_BENCHMARK_INVALID'),
                reason if reason in allowed else 'TRIAL_VECTOR_BINDING_INVALID')
            if aggregate['binding_status']=='VERIFIED':
                check(aggregate['ledger_snapshot_hash']==state['ledger_chain_hash'],'TRIAL_VECTOR_BINDING_INVALID')
                stats=aggregate['statistics'];bound=aggregate
        report.update(K=stats['K'],variance_convention=stats['variance_convention'],
            ledger_binding='VERIFIED',bound_report_hash=bound['report_hash'])
        current=5
        if form_b:check(aggregate['reason_code']!='VAR_SR_TRIALS_MISMATCH','VAR_SR_TRIALS_MISMATCH')
        variance=stats['var_sr_trials_recomputed']
        # The frozen kernel deliberately reports no usable variance for a zero benchmark.
        check((variance is None and stats['reason_code']=='DEFLATION_BENCHMARK_INVALID') or
            (_finite(variance) and variance>=0),'VAR_SR_TRIALS_MISMATCH')
        report['var_sr_trials_recomputed']=variance
        current=6
        check(_finite(stats['psr']) and 0<=stats['psr']<=1,'PSR_OUT_OF_RANGE')
        report['psr']=stats['psr']
        current=7
        check(variance is not None and variance>0 and _finite(stats['sr_star0']),'DEFLATION_BENCHMARK_INVALID')
        report['sr_star0']=stats['sr_star0']
        current=8
        check(_finite(stats['dsr']) and 0<=stats['dsr']<=1 and stats['stats_status']=='VALID','DSR_OUT_OF_RANGE')
        report['dsr']=stats['dsr']
        current=9
        check(type(stats.get('independence_assumption_disclosure')) is str and bool(stats['independence_assumption_disclosure']),
            'DISCLOSURE_MISSING')
        report['independence_assumption_disclosure']=stats['independence_assumption_disclosure']
        current=10
        check(type(threshold_policy) is dict and set(threshold_policy)=={'dsr_min'} and
            type(threshold_policy['dsr_min']) in (int,float) and threshold_policy['dsr_min']==.95,'THRESHOLD_POLICY_VIOLATION')
        report.update(stats_status='VALID',decision_input={'dsr_pass':report['dsr']>=.95})
        current=11
        check(True,'STATS_HASH_MISMATCH')
        # Hash generation uses the full canonical body; failure invalidates this final check.
        report['stats_hash']=_hash(report)
        return report
    except _Stop:
        pass
    except (ValueError,TypeError,KeyError,OSError,OverflowError,RecursionError):
        reasons=('RETURN_SERIES_INVALID','MOMENTS_INVALID','TRIAL_COUNTER_INVALID','TRIAL_COUNTER_INVALID',
            'TRIAL_VECTOR_BINDING_INVALID','VAR_SR_TRIALS_MISMATCH','PSR_OUT_OF_RANGE',
            'DEFLATION_BENCHMARK_INVALID','DSR_OUT_OF_RANGE','DISCLOSURE_MISSING',
            'THRESHOLD_POLICY_VIOLATION','STATS_HASH_MISMATCH')
        report['checks'][current]['status']='FAIL';report['reason_code']=reasons[current]
    report.update(stats_status='INVALID',dsr=None,decision_input={'dsr_pass':False})
    report['stats_hash']=_hash(report)
    return report
