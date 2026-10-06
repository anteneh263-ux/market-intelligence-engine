"""Read-only independently recomputed R3 Form B statistics, version 1."""
import hashlib
from pathlib import Path
import statistics
import sys

from .registry_inspection import _hash,_hex
from .validation_plan import snapshot,require,finite
from .bound_statistics import evaluate_trial_statistics


def derivation_method_hash():
    """Expose the pin for preregistration; evaluators require an explicit pin."""
    return _hash(dict(schema_version='wp5_3.aggregate_method.v1',
        module_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        statistics_sha256=hashlib.sha256(Path(statistics.__file__).read_bytes()).hexdigest(),
        python=list(sys.version_info[:3]),method='mean/stdev; population pvariance; absolute tolerance 1e-12'))


def _derive(registry_path,anchor_path,program,trial,artifacts,pin):
    require(_hex(pin) and pin==derivation_method_hash(),'DERIVATION_METHOD_MISMATCH')
    copied=snapshot(artifacts)
    bound=evaluate_trial_statistics(registry_path,anchor_path,research_program_id=program,
        trial_id=trial,result_artifacts=copied)
    require(bound['binding_status']=='VERIFIED',bound['reason_code'] or 'TRIAL_VECTOR_BINDING_INVALID')
    stats=bound['statistics']
    require(stats['stats_status']=='VALID',stats['reason_code'] or 'DEFLATION_BENCHMARK_INVALID')
    sharpes=[]
    for result_hash in bound['result_hashes']:
        values=copied[result_hash]['net_returns']
        sharpes.append(statistics.mean(values)/statistics.stdev(values))
    variance=float(statistics.pvariance(sharpes))
    require(finite(variance) and abs(variance-stats['var_sr_trials_recomputed'])<=1e-12,'VAR_SR_TRIALS_MISMATCH')
    record=dict(schema_version='wp5_3.aggregate_verifier.v1',research_program_id=program,trial_id=trial,
        ledger_snapshot_hash=bound['ledger_snapshot_hash'],trial_ids_hash=_hash(bound['trial_ids']),
        result_hashes_hash=_hash(bound['result_hashes']),N_total=stats['N_total'],K=len(sharpes),
        var_sr_trials=variance,derivation_method_hash=pin,verification='RECOMPUTED')
    aggregate={k:record[k] for k in ('var_sr_trials','K','trial_ids_hash','ledger_snapshot_hash','derivation_method_hash')}
    aggregate['verifier_record_hash']=_hash(record)
    return bound,aggregate,record


def derive_aggregate(registry_path,anchor_path,*,research_program_id,trial_id,result_artifacts,expected_method_hash):
    """Build a Form B input from actual ledger/results; invalid inputs raise."""
    _,aggregate,record=_derive(registry_path,anchor_path,research_program_id,trial_id,result_artifacts,expected_method_hash)
    return dict(sharpe_aggregate=aggregate,verifier_record=record)


def evaluate_aggregate_statistics(bundle,registry_path,anchor_path,*,research_program_id,trial_id,
                                  result_artifacts,expected_method_hash):
    report=dict(schema_version='wp5_3.aggregate_report.v1',binding_status='REFUSED',reason_code=None,
        research_program_id=research_program_id if _hex(research_program_id) else None,
        trial_id=trial_id if _hex(trial_id) else None,ledger_snapshot_hash=None,statistics=None,verifier_record=None)
    try:
        q=snapshot(bundle)
        require(type(q) is dict and set(q)=={'sharpe_aggregate','verifier_record'},'TRIAL_VECTOR_BINDING_INVALID')
        supplied=q['sharpe_aggregate']
        require(type(supplied) is dict and set(supplied)=={'var_sr_trials','K','trial_ids_hash',
            'ledger_snapshot_hash','derivation_method_hash','verifier_record_hash'},'TRIAL_VECTOR_BINDING_INVALID')
        bound,actual,record=_derive(registry_path,anchor_path,research_program_id,trial_id,result_artifacts,expected_method_hash)
        require(type(supplied['K']) is int and supplied['K']==actual['K'],'SHARPE_VECTOR_LENGTH_MISMATCH')
        require(all(supplied[k]==actual[k] for k in actual if k not in ('K','var_sr_trials')) and
            _hash(q['verifier_record'])==actual['verifier_record_hash'] and q['verifier_record']==record,
            'TRIAL_VECTOR_BINDING_INVALID')
        require(finite(supplied['var_sr_trials']) and abs(supplied['var_sr_trials']-actual['var_sr_trials'])<=1e-12,
            'VAR_SR_TRIALS_MISMATCH')
        stats=bound['statistics'];stats['variance_convention']['source']='FORM_B'
        report.update(binding_status='VERIFIED',ledger_snapshot_hash=bound['ledger_snapshot_hash'],
            statistics=stats,verifier_record=record)
    except (ValueError,TypeError,KeyError,OSError,OverflowError,RecursionError) as exc:
        allowed={'DERIVATION_METHOD_MISMATCH','VAR_SR_TRIALS_MISMATCH','TRIAL_VECTOR_BINDING_INVALID',
            'SHARPE_VECTOR_LENGTH_MISMATCH','SHARPE_VECTOR_INCOMPLETE','INSUFFICIENT_TRIALS_FOR_DSR',
            'DEFLATION_BENCHMARK_INVALID','SHARPE_VECTOR_NONFINITE'}
        report['reason_code']=str(exc) if str(exc) in allowed else 'TRIAL_VECTOR_BINDING_INVALID'
    report['report_hash']=_hash(report)
    return report
