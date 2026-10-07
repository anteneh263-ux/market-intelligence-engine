"""Versioned execution-bound evaluations on the existing single-use access ledger."""
from . import holdout as access
from .registry_inspection import _hash,_hex
from .validation_plan import require,snapshot,finite,read_artifact
from .monte_carlo import _metrics


def _report(core,reserved,consumed,completed):
    r=access._make_report(core,reserved,consumed,completed)
    r.pop('holdout_report_hash')
    r.update(schema_version='wp5_6.observed_report.v1',execution_evidence_hash=core['execution_evidence_hash'])
    r['holdout_report_hash']=_hash(r)
    return r


def run_observed_holdout(request,*,directory,verify_prerequisites,read_data,evaluate):
    """No recovery or retry. Exceptions preserve the last durable access state."""
    q=access._validate_request(request);key=access.reservation_key(q)
    with access._locked(directory) as (conn,store):
        # Across legacy and observed cores alike, any previous access forbids reuse.
        existing=access._verify(conn,store)[3]
        require(not any(r['strategy_identity']==q['strategy_identity'] and r['dataset_hash']==q['holdout_dataset_hash'] for r in existing),
                'HOLDOUT_ALREADY_CONSUMED')
        verify_prerequisites(snapshot(q))
        reserved=access._append(conn,store,q,'RESERVED')
        consumed=access._append(conn,store,q,'CONSUMED')
        data=snapshot(read_data())
        require(_hash(data)==q['holdout_dataset_hash'],'HOLDOUT_DATASET_INVALID')
        params=snapshot(q['parameters']);result=snapshot(evaluate(data,params))
        require(params==q['parameters'] and snapshot(request)==q,'POST_ACCESS_MUTATION')
        require(type(result) is dict and set(result)=={'net_returns','execution_evidence_hash'} and _hex(result['execution_evidence_hash']) and
            type(result['net_returns']) is list and result['net_returns'] and all(finite(v) for v in result['net_returns']),'HOLDOUT_EXECUTION_INVALID')
        returns=result['net_returns'];metric=_metrics(returns);rule=q['decision_rule']
        passed=not metric['ruined'] and metric['total_return']>=rule['min_total_return'] and metric['max_drawdown']<=rule['max_drawdown']
        core=dict(schema_version='wp5_6.observed_evaluation.v1',request=q,metrics=metric,net_returns=returns,returns_hash=_hash(returns),
            execution_evidence_hash=result['execution_evidence_hash'],decision='HOLDOUT_PASSED' if passed else 'HOLDOUT_FAILED')
        store.put(f'observed-evaluation-{key}.json',core)
        completed=access._append(conn,store,q,'COMPLETED',evaluation_hash=_hash(core))
        report=_report(core,reserved,consumed,completed)
        store.put(f'observed-report-{key}.json',report)
        return report


def verify_observed_holdout(directory,key,*,verify_execution):
    with access._locked(directory) as (conn,store):
        rows=access._verify(conn,store)[3]
        chain=[r for r in rows if r['reservation_key']==key and r['note'] is None]
        require(len(chain)==3 and [r['to_state'] for r in chain]==['RESERVED','CONSUMED','COMPLETED'],'HOLDOUT_HASH_MISMATCH')
        core=read_artifact(directory,f'observed-evaluation-{key}.json')
        report=read_artifact(directory,f'observed-report-{key}.json')
        require(type(core) is dict and set(core)=={'schema_version','request','metrics','net_returns','returns_hash','execution_evidence_hash','decision'} and
            core['schema_version']=='wp5_6.observed_evaluation.v1' and _hash(core)==chain[-1]['evaluation_hash'],'HOLDOUT_HASH_MISMATCH')
        q=access._validate_request(core['request'])
        require(access.reservation_key(q)==key and all(q[k]==chain[-1][k] for k in ('strategy_identity','experiment_id','trial_id')) and
            _hash(core['net_returns'])==core['returns_hash'] and _hex(core['execution_evidence_hash']),'HOLDOUT_HASH_MISMATCH')
        actual=verify_execution(core['execution_evidence_hash'])
        require(actual==core['net_returns'],'HOLDOUT_EXECUTION_MISMATCH')
        metrics=_metrics(actual);rule=q['decision_rule'];passed=not metrics['ruined'] and metrics['total_return']>=rule['min_total_return'] and metrics['max_drawdown']<=rule['max_drawdown']
        require(core['metrics']==metrics and core['decision']==('HOLDOUT_PASSED' if passed else 'HOLDOUT_FAILED') and
            report==_report(core,*chain),'HOLDOUT_HASH_MISMATCH')
        return report
