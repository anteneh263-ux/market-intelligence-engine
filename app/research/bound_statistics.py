"""Read-only v2 ledger/result binding for the Form A statistics path."""
import hashlib
import sqlite3

from . import registry_inspection as base
from .trial_registry_v2 import _open, _seal
from .ledger_verification import _checked_verify
from .statistics_math import calculate_statistics

__all__ = ['evaluate_trial_statistics']
_RESULT_FIELDS = frozenset(('schema_version','research_program_id','experiment_id','trial_id','net_returns'))


def evaluate_trial_statistics(registry_path, anchor_path, *, research_program_id, trial_id, result_artifacts):
    """Derive the COMPLETE Sharpe vector from content-bound result artifacts.

    Artifacts are supplied as a hash-keyed mapping; their content is verified
    against committed outcome hashes. No performance reader or write capability.
    This integrates Form A/default .95 math only, not Form B or policy changes.
    """
    report = dict(schema_version='wp5_3.bound_report.v1',binding_status='REFUSED',reason_code=None,
                  research_program_id=research_program_id if base._hex(research_program_id) else None,
                  trial_id=trial_id if base._hex(trial_id) else None,ledger_snapshot_hash=None,
                  trial_ids=None,result_hashes=None,statistics=None)
    try:
        base._require(base._hex(research_program_id) and base._hex(trial_id),'TRIAL_VECTOR_BINDING_INVALID')
        base._require(type(result_artifacts) is dict and all(base._hex(k) for k in result_artifacts),
                      'TRIAL_VECTOR_BINDING_INVALID')
        with _open(registry_path,anchor_path,False) as (connection,anchor,pending):
            state = _checked_verify(connection,anchor,{'pending_anchor_update':pending} if pending is not None else {},research_program_id)
            reservations = []
            outcomes = {}
            for (raw,) in connection.execute('SELECT row_json FROM trial_ledger WHERE research_program_id=? ORDER BY chain_index',
                                             (research_program_id,)):
                row = base._json(raw)
                if 'reservation_sequence' in row:
                    reservations.append(row)
                else:
                    outcomes[row['trial_id']] = row
            ids, hashes, sharpes, frequencies = [], [], [], []
            target_returns = None
            for reservation in reservations:
                outcome = outcomes.get(reservation['trial_id'])
                if outcome is None or outcome['trial_outcome'] != 'COMPLETED_VALID':
                    continue
                result_hash = outcome['result_hash']
                artifact = result_artifacts.get(result_hash)
                raw = base._canonical(artifact)
                artifact = base._json(raw)
                base._require(type(artifact) is dict and ((artifact.get('schema_version')=='wp5_3.trial_result.v1' and set(artifact)==_RESULT_FIELDS) or
                    (artifact.get('schema_version')=='wp5_3.trial_result.v2' and set(artifact)==_RESULT_FIELDS|{'execution_evidence_hash','return_frequency_hash'} and
                     base._hex(artifact['execution_evidence_hash']) and base._hex(artifact['return_frequency_hash']))),'SHARPE_VECTOR_INCOMPLETE')
                base._require(artifact['schema_version'] in ('wp5_3.trial_result.v1','wp5_3.trial_result.v2') and
                    all(artifact[k] == reservation[k] for k in ('research_program_id','experiment_id','trial_id')),
                    'TRIAL_VECTOR_BINDING_INVALID')
                base._require(type(artifact['net_returns']) is list and
                    all(type(v) in (int,float) for v in artifact['net_returns']),'SHARPE_VECTOR_NONFINITE')
                # The checked identities/types and hashed bytes belong to the same snapshot.
                base._require(hashlib.sha256(raw).hexdigest()==result_hash,'TRIAL_VECTOR_BINDING_INVALID')
                moments = calculate_statistics(artifact['net_returns'],n_total=1,sharpe_values=[])
                base._require(moments['reason_code']=='INSUFFICIENT_TRIALS_FOR_DSR' and
                              moments['sr_observed'] is not None,'SHARPE_VECTOR_INCOMPLETE')
                ids.append(reservation['trial_id']);hashes.append(result_hash);sharpes.append(moments['sr_observed'])
                frequencies.append(artifact.get('return_frequency_hash'))
                if reservation['trial_id']==trial_id:
                    target_returns = artifact['net_returns']
            base._require(target_returns is not None and len(set(frequencies))<=1,'TRIAL_VECTOR_BINDING_INVALID')
            statistics = calculate_statistics(target_returns,n_total=state['N_total'],sharpe_values=sharpes)
            statistics['ledger_binding']='VERIFIED'
            report.update(binding_status='VERIFIED',ledger_snapshot_hash=state['ledger_chain_hash'],
                          trial_ids=ids,result_hashes=hashes,statistics=statistics)
    except (base._Refused,OSError,sqlite3.Error,ValueError,TypeError,OverflowError,RecursionError) as exc:
        report.update(binding_status='REFUSED',ledger_snapshot_hash=None,trial_ids=None,result_hashes=None,statistics=None)
        report['reason_code'] = str(exc) if isinstance(exc,base._Refused) else 'TRIAL_VECTOR_BINDING_INVALID'
    return _seal(report,'report_hash')
