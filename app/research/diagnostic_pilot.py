"""Non-inferential component integration; never an alternate scientific approval gate."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import pandas as pd

from . import fullstage_pilot as original
from .integration_pilot import _source
from .cpcv import runtime_pin
from .registry_inspection import _hash,_canonical
from .validation_plan import require,snapshot,read_artifact
from .walkforward import _Store
from .trial_registry_v2 import initialize_trial_registry,execute_trial
from .holdout import initialize_holdout_store
from .observed_walkforward import run_observed_walkforward_trial,verify_observed_walkforward_trial,_descriptor
from .observed_plan import validate_observed_plan
from .observed_pipeline import _cpcv,_statistics,_holdout_request,_verify_holdout_execution
from .observed_mc_v2 import run_observed_mc_v2,verify_observed_mc_v2
from .observed_holdout import run_observed_holdout,verify_observed_holdout
from .observed_session import run_observed_session,verify_observed_session
from .session_coverage import validate_session_coverage,verify_session_rows
from .timing_rows import check_timing_rows
from .rag_provenance import retrieve_advisory
from app.strategy_research.evaluation.observed_walkforward import build_native

SCHEMA='wp5.technical_diagnostic.v1'
AUTH_SCHEMA='wp5.technical_diagnostic_authorization.v1'
AUTHORITY=dict(scope='TECHNICAL_DIAGNOSTIC_ONLY',scientific_acceptance='UNRESOLVED',
    complete_family_inference=False,scientific_holdout_validated=False,promotion_authorized=False,
    prospective_verified=False,local_statistics_role='COMPONENT_DIAGNOSTIC_NOT_A_GATE',
    access_role='RETROSPECTIVE_SINGLE_USE_PROTOCOL_PROBE')
SELECTION='PREREGISTERED_DIAGNOSTIC_FIXTURE_NO_SELECTION_OR_INFERENCE'


def _pins():
    return dict(diagnostic_runner=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scientific_runner_preserved=hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest())


def prepare_contract(**kwargs):
    # Existing pure preparation does not evaluate a strategy or create any store.
    c=original.prepare_contract(**kwargs)
    c.update(schema_version=SCHEMA,authority=snapshot(AUTHORITY),diagnostic_pins=_pins(),
        complete_family_N=None,family_history_requirement='UNRESOLVED_RETAINED_NO_ZERO_HISTORY_CLAIM',
        stop_policy='Stop on structural/integrity/lifecycle failure; local DSR and component performance verdicts never grant scientific acceptance')
    c['research_scope']['evaluation_objective_hash']=_hash('TECHNICAL_INTEGRATION_FIXTURE_ONLY_V1')
    c['policy']['selection_rule']=SELECTION
    rows=_source(c['source_path'],c['source_sha256'])
    for i,t in enumerate(original.EXPOSURES):
        c['trials'][i].update(role='LOCAL_FIXTURE_PREDECESSOR' if i==0 else 'DESIGNATED_DIAGNOSTIC_FIXTURE',
            request=original._request(c['policy'],c['dataset_manifest'],rows,c['profile'],c['risk_limits'],
                c['research_scope'],c['owner'],c['created_utc_ms'],t,c['boundaries']))
    return c


def _static(c):
    require(c.get('schema_version')==SCHEMA and _hash(c.get('authority'))==_hash(AUTHORITY) and
        c.get('complete_family_N') is None and c.get('promotion_authorized') is False and
        c.get('prospective_verified') is False and c.get('unseen_holdout_claimed') is False and
        c.get('retrospective_holdout') is True and c.get('diagnostic_pins')==_pins() and
        c['research_scope']['evaluation_objective_hash']==_hash('TECHNICAL_INTEGRATION_FIXTURE_ONLY_V1') and
        c['policy']['selection_rule']==SELECTION,'DIAGNOSTIC_AUTHORITY_INVALID')
    validate_observed_plan(c['plan'])


def _authorize(c,a):
    _static(c)
    fields={'schema_version','contract_hash','evidence_directory','approved_by','approved_utc_ms','approval_reference',
        'scope','unresolved_history_acknowledged','retrospective_access_probe_acknowledged'}
    require(type(a) is dict and set(a)==fields and a['schema_version']==AUTH_SCHEMA and a['contract_hash']==_hash(c) and
        a['evidence_directory']==c['evidence_directory'] and a['approved_by']==c['owner'] and
        type(a['approved_utc_ms']) is int and a['approved_utc_ms']>=c['created_utc_ms'] and
        type(a['approval_reference']) is str and a['approval_reference'].strip() and
        a['scope']=='TECHNICAL_DIAGNOSTIC_ONLY' and a['unresolved_history_acknowledged'] is True and
        a['retrospective_access_probe_acknowledged'] is True,'DIAGNOSTIC_AUTHORIZATION_INVALID')


def preflight(contract,authorization):
    c=snapshot(contract);a=snapshot(authorization);_authorize(c,a)
    require(runtime_pin()==c['plan']['cpcv']['pinned_lib'],'DIAGNOSTIC_RUNTIME_MISMATCH')
    expected=prepare_contract(source_path=c['source_path'],source_sha256=c['source_sha256'],evidence_directory=c['evidence_directory'],
        owner=c['owner'],created_utc_ms=c['created_utc_ms'])
    require(c==expected,'DIAGNOSTIC_CONTRACT_CHANGED')
    return c,a


def _context(c,root,prior):
    return dict(schema_version='wp5.diagnostic_component_context.v1',request=c['trials'][1]['request'],policy=c['policy'],
        dataset_manifest=c['dataset_manifest'],plan=c['plan'],profile=c['profile'],risk_limits=c['risk_limits'],
        strategy=_descriptor(original.MovingAveragePilot()),selector=_descriptor(original.FixedSelector(.75)),
        registry_path=str(root/'registry.sqlite'),anchor_path=str(root/'anchor.json'),holdout_directory=str(root/'holdout-access'),
        prior_result_artifacts=prior,corpus=c['corpus'],advisory_query=c['advisory_query'],actor=c['owner'])


def _state(c,root):
    return execute_trial(dict(mode='REGISTRY_STATE',research_program_id=c['trials'][0]['request']['research_program_id']),
        str(root/'registry.sqlite'),str(root/'anchor.json'))


def _advisory(c):
    return retrieve_advisory(dict(query=c['advisory_query'],phase='POST_FREEZE',max_items=c['plan']['rag_policy']['max_items'],
        action='RETRIEVE',requested_mutations=[]),c['corpus'],expected_corpus_hash=c['plan']['rag_policy']['corpus_hash'])


def _local_statistics(run,root):
    stats=_statistics(run,root)
    require(stats.get('ledger_binding')=='VERIFIED','DIAGNOSTIC_STATISTICS_BINDING_INVALID')
    return stats


def _proof(c,root):
    _static(c)
    require(read_artifact(root,'CONTRACT.json')==c and read_artifact(root,'PLAN.json')==c['plan'],'DIAGNOSTIC_BINDING_INVALID')
    artifacts={};wf_hashes=[]
    for i,name in enumerate(('predecessor','wf')):
        folder=root/name
        verify_observed_walkforward_trial(folder,registry_path=str(root/'registry.sqlite'),anchor_path=str(root/'anchor.json'))
        frozen=read_artifact(folder,'RUN.json');trial=read_artifact(folder,'RESULT.json');artifact=read_artifact(folder,'TRIAL_RESULT.json')
        require(frozen['request']==c['trials'][i]['request'] and frozen['policy']==c['policy'] and
            frozen['dataset_manifest']==c['dataset_manifest'] and frozen['profile']==c['profile'] and
            frozen['risk_limits']==c['risk_limits'] and frozen['strategy']==_descriptor(original.MovingAveragePilot()) and
            frozen['selector']==_descriptor(original.FixedSelector(original.EXPOSURES[i])) and
            trial['lifecycle_status']=='COMPLETED_VALID','DIAGNOSTIC_WF_INVALID')
        artifacts[_hash(artifact)]=artifact;wf_hashes.append(trial['report_hash'])
    prior=read_artifact(root/'predecessor','TRIAL_RESULT.json')
    run=_context(c,root,{_hash(prior):prior})
    require(read_artifact(root,'RUN.json')==run,'DIAGNOSTIC_BINDING_INVALID')
    splits=_cpcv(c['plan'],c['trials'][1]['request'],c['policy'],c['dataset_manifest'])
    require(splits['adapter_status']=='VALID' and read_artifact(root,'CPCV.json')==splits,'DIAGNOSTIC_CPCV_INVALID')
    stats=_local_statistics(run,root)
    require(read_artifact(root,'LOCAL_STATISTICS.json')==stats,'DIAGNOSTIC_STATISTICS_MISMATCH')
    mc=read_artifact(root/'mc','REPORT.json')
    mc=verify_observed_mc_v2(c['plan'],wf_directory=root/'wf',evidence_directory=root/'mc',expected_report_hash=mc['report_hash'])
    require(mc['mc_status']=='VALID' and mc['readiness_effect'] in ('PRESERVED','DOWNGRADED'),'DIAGNOSTIC_MC_INVALID')
    require(read_artifact(root,'ADVISORY.json')==_advisory(c),'DIAGNOSTIC_ADVISORY_INVALID')
    state=_state(c,root);require(state['N_total']==2,'DIAGNOSTIC_LOCAL_LEDGER_CHANGED')
    return dict(schema_version='wp5.diagnostic_structural_proof.v1',authority=snapshot(AUTHORITY),contract_hash=_hash(c),
        wf_report_hashes=wf_hashes,cpcv_hash=splits['adapter_report_hash'],local_statistics_hash=_hash(stats),
        mc_hash=mc['report_hash'],local_registry_state_hash=_hash(state),local_reservations=2,complete_family_N=None)


def _evaluate_probe(c,root,run,hq,proof,rows,params):
    data,index=validate_session_coverage(c['dataset_manifest'],hq_contract:=run['request']['contract_record'],c['profile'])
    bounds=c['boundaries']['holdout'];rows=verify_session_rows(rows,index,bounds['start_ms'],bounds['end_ms'])
    require(_hash(rows)==c['plan']['holdout_policy']['dataset_hash'],'HOLDOUT_DATASET_INVALID')
    strategy=original.MovingAveragePilot();window=dict(window_id=0,train=[bounds['start_ms'],bounds['start_ms']],test=[bounds['start_ms'],bounds['end_ms']])
    native=build_native(c['policy'],c['profile'],c['risk_limits'],strategy,params,window,rows)
    binding=read_artifact(root/'wf','EXECUTION_RECEIPT.json')['trial_binding'];child=root/'holdout-execution'
    store=_Store(child)
    try:
        session=child/'session';session.mkdir();os.fsync(store.fd)
        observed=run_observed_session(manifest=native,profile=c['profile'],risk_limits=c['risk_limits'],strategy=strategy,params=params,
            frame=pd.DataFrame(rows),evidence_directory=session,trial_binding=binding)
        observed=verify_observed_session(session,expected_report_hash=observed['report_hash'])
        timing=check_timing_rows(observed['audit']['timing_rows'],hq_contract,intended_dataset_zone='HOLDOUT') if observed['audit']['timing_rows'] else None
        receipt=dict(schema_version='wp5.diagnostic_access_execution.v1',holdout_request=hq,plan_hash=_hash(c['plan']),
            session_evidence_hash=observed['report_hash'],timing=timing,returns_hash=_hash(observed['net_returns']),prerequisites_hash=_hash(proof))
        store.put('RECEIPT.json',receipt)
        returns=_verify_holdout_execution(run,root,_hash(receipt))
        return dict(net_returns=returns,execution_evidence_hash=_hash(receipt))
    finally:store.close()


def _completed_report(c,a,root,proof,held):
    mc=read_artifact(root/'mc','REPORT.json')
    result=dict(schema_version=SCHEMA,technical_status='VERIFIED',stopped_at=None,reason_code=None,
        authority=snapshot(AUTHORITY),contract_hash=_hash(c),authorization_hash=_hash(a),local_reservations=2,complete_family_N=None,
        promotion_authorized=False,scientific_holdout_validated=False,prospective_verified=False,
        structural_proof_hash=_hash(proof),local_statistics=read_artifact(root,'LOCAL_STATISTICS.json'),
        component_mc_readiness=mc['readiness_effect'],component_access_verdict=held['holdout_status'],
        access_component_hash=held['holdout_report_hash'])
    result['report_hash']=_hash(result);return result


def _verified_access(run,root,held):
    require(read_artifact(root/'holdout-execution','RECEIPT.json')['schema_version']=='wp5.diagnostic_access_execution.v1','DIAGNOSTIC_ACCESS_SCHEMA_INVALID')
    actual=verify_observed_holdout(run['holdout_directory'],held['reservation_key'],
        verify_execution=lambda h:_verify_holdout_execution(run,root,h))
    require(held==actual,'DIAGNOSTIC_ACCESS_INVALID')
    return actual


def verify_diagnostic(directory,*,expected_contract_hash,expected_report_hash):
    root=Path(directory);c=read_artifact(root,'CONTRACT.json');a=read_artifact(root,'AUTHORIZATION.json');report=read_artifact(root,'REPORT.json')
    _authorize(c,a)
    require(str(root)==c['evidence_directory'] and _hash(c)==expected_contract_hash,'DIAGNOSTIC_BINDING_INVALID')
    proof=_proof(c,root);require(proof==read_artifact(root,'PREHOLDOUT_VERIFICATION.json'),'DIAGNOSTIC_PROOF_INVALID')
    run=read_artifact(root,'RUN.json');held=read_artifact(root,'ACCESS_COMPONENT.json')
    actual=_verified_access(run,root,held)
    expected=_completed_report(c,a,root,proof,actual)
    require(_hash(report)==_hash(expected) and report['report_hash']==expected_report_hash,'DIAGNOSTIC_REPORT_INVALID')
    return dict(verification_status='VERIFIED',scope='TECHNICAL_DIAGNOSTIC_ONLY',scientific_acceptance='UNRESOLVED',
        report_hash=expected_report_hash,source_data_read=False,counter_incremented=False,promotion_authorized=False)


def run_diagnostic(contract,authorization):
    c,a=preflight(contract,authorization);root=Path(c['evidence_directory']);store=_Store(root)
    stage='INITIALIZATION'
    try:
        frozen=store.put('CONTRACT.json',c);store.put('AUTHORIZATION.json',a);store.put('PLAN.json',c['plan'])
        reg=str(root/'registry.sqlite');anchor=str(root/'anchor.json');initialize_trial_registry(reg,anchor)
        hold=root/'holdout-access';hold.mkdir();os.fsync(store.fd);initialize_holdout_store(hold)
        def check():store.check('CONTRACT.json',frozen)
        allowed={tuple(w[z]) for w in c['policy']['windows'] for z in ('train','test')}
        def read(start,end):
            check();require((start,end) in allowed,'SOURCE_ZONE_FORBIDDEN')
            return [r for r in _source(c['source_path'],c['source_sha256']) if start<=r['open_time']<end]
        try:
            stage='CPCV';split=_cpcv(c['plan'],c['trials'][1]['request'],c['policy'],c['dataset_manifest'])
            store.put('CPCV.json',split);require(split['adapter_status']=='VALID','DIAGNOSTIC_CPCV_INVALID')
            store.put('ADVISORY.json',_advisory(c));prior={}
            for i,name in enumerate(('predecessor','wf')):
                stage=f'WF_{i+1}';child=root/name;child.mkdir();os.fsync(store.fd);check()
                result=run_observed_walkforward_trial(c['trials'][i]['request'],c['policy'],c['dataset_manifest'],
                    profile=c['profile'],risk_limits=c['risk_limits'],strategy=original.MovingAveragePilot(),selector=original.FixedSelector(original.EXPOSURES[i]),
                    registry_path=reg,anchor_path=anchor,evidence_directory=child,read_bars=read,prior_result_artifacts=prior)
                verify_observed_walkforward_trial(child,registry_path=reg,anchor_path=anchor)
                require(result['lifecycle_status']=='COMPLETED_VALID','DIAGNOSTIC_WF_INVALID')
                if i==0:
                    artifact=read_artifact(child,'TRIAL_RESULT.json');prior[_hash(artifact)]=artifact
            run=_context(c,root,prior);store.put('RUN.json',run)
            stage='LOCAL_STATISTICS';store.put('LOCAL_STATISTICS.json',_local_statistics(run,root))
            stage='MONTE_CARLO';child=root/'mc';child.mkdir();os.fsync(store.fd)
            run_observed_mc_v2(c['plan'],wf_directory=root/'wf',registry_path=reg,anchor_path=anchor,evidence_directory=child,
                strategy=original.MovingAveragePilot(),read_bars=read)
            stage='ACCESS_PROBE';check()
            require(hashlib.sha256(Path(c['source_path']).read_bytes()).hexdigest()==c['source_sha256'],'SOURCE_HASH_MISMATCH')
            proof=_proof(c,root);store.put('PREHOLDOUT_VERIFICATION.json',proof);hq=_holdout_request(run,root)
            child=root/'holdout-execution';child.mkdir();os.fsync(store.fd)
            def prerequisites(q):
                check();require(q==hq and _proof(c,root)==proof,'DIAGNOSTIC_PROOF_INVALID')
            def access_data():
                check();b=c['boundaries']['holdout']
                return [r for r in _source(c['source_path'],c['source_sha256']) if b['start_ms']<=r['open_time']<b['end_ms']]
            held=run_observed_holdout(hq,directory=hold,verify_prerequisites=prerequisites,read_data=access_data,
                evaluate=lambda rows,params:_evaluate_probe(c,root,run,hq,proof,rows,params))
            store.put('ACCESS_COMPONENT.json',held);check()
            require(_proof(c,root)==proof,'DIAGNOSTIC_PROOF_INVALID')
            _verified_access(run,root,held)
            report=_completed_report(c,a,root,proof,held);store.put('REPORT.json',report)
        except (ValueError,OSError,KeyError,TypeError,OverflowError) as exc:
            report=dict(schema_version=SCHEMA,technical_status='STOPPED',stopped_at=stage,reason_code=str(exc),
                authority=snapshot(AUTHORITY),contract_hash=_hash(c),authorization_hash=_hash(a),local_reservations=_state(c,root)['N_total'],
                complete_family_N=None,promotion_authorized=False,scientific_holdout_validated=False,prospective_verified=False)
            report['report_hash']=_hash(report);store.put('REPORT.json',report)
            return report
        verify_diagnostic(root,expected_contract_hash=_hash(c),expected_report_hash=report['report_hash'])
        return report
    finally:store.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare')
    for flag in ('source','sha256','evidence-root','owner','output'):prep.add_argument('--'+flag,required=True)
    run=sub.add_parser('run');run.add_argument('--contract',required=True);run.add_argument('--authorization',required=True)
    verify=sub.add_parser('verify');verify.add_argument('--root',required=True);verify.add_argument('--contract-hash',required=True);verify.add_argument('--report-hash',required=True)
    a=p.parse_args()
    if a.command=='prepare':
        c=prepare_contract(source_path=a.source,source_sha256=a.sha256,evidence_directory=a.evidence_root,owner=a.owner)
        with open(a.output,'xb') as f:f.write(_canonical(c));f.flush();os.fsync(f.fileno())
        result=dict(contract_hash=_hash(c),scope=AUTHORITY['scope'],evaluation_performed=False)
    elif a.command=='verify':result=verify_diagnostic(a.root,expected_contract_hash=a.contract_hash,expected_report_hash=a.report_hash)
    else:result=run_diagnostic(json.loads(Path(a.contract).read_text()),json.loads(Path(a.authorization).read_text()))
    print(json.dumps(result))


if __name__=='__main__':main()
