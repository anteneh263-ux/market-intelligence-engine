"""Supplied prior receipt ancestry, not recursively authenticated history."""
from app.research.phase6_start_validation import _snapshot,_Refused,_need,_shape,_hex,_int
from app.research.phase6_prior_state_validation import verify_prior_state,exact
from app.research.phase6_intake_validation import verify_intake_prefix
from app.research.phase6_validation import canonical_hash as H


def record(d,version,fields):
    _shape(d,'schema_version '+fields);_need(d['schema_version']==version)
    for k in fields.split():
        if k.endswith('_hash') or k in ('run_id','session_id'):_need(_hex(d[k]))


def extension(old,new):
    _need(all(exact(old[k],new[k]) for k in ('plan','reservation')),'IDENTITY_MISMATCH')
    _need(exact(old['receipts'],new['receipts'][:len(old['receipts'])]),'RECEIPT_ANCESTRY_INVALID')
    _need(exact(old['segments'],new['segments'][:len(old['segments'])]),'RECEIPT_ANCESTRY_INVALID')
    for name in ('payloads','clock_probes'):
        _need(all(k in new[name] and exact(v,new[name][k]) for k,v in old[name].items()),'RECEIPT_ANCESTRY_INVALID')


def validate_bundle(bundle,result):
    actual=verify_intake_prefix(bundle)
    _need(actual['binding_status']=='VALID' and exact(actual,result),'INTAKE_INVALID')


def _check(q):
    _shape(q,'prior_state proof')
    state=q['prior_state'];v=verify_prior_state(state)
    _need(v['binding_status']=='VALID' and v['prior_kind']=='CHECKPOINT','PRIOR_STATE_INVALID')
    p=q['proof'];_shape(p,'diagnostic_invocation intake_binding intake_bundle intake_validation execution_invocation quote_binding quote_bundle quote_validation')
    old=state['prior'];m=old['manifest'];k=old['checkpoint'];sid=H(m)
    d=p['diagnostic_invocation'];i=p['intake_binding'];b=p['intake_bundle'];x=p['execution_invocation']
    _need(type(d) is dict)
    version=d.get('schema_version');extra={'p6.diagnostic_invocation.v2':'','p6.diagnostic_invocation.v3':' prior_state_binding_hash','p6.diagnostic_invocation.v4':' prior_state_binding_hash prior_receipt_binding_hash','p6.diagnostic_invocation.v5':' prior_state_binding_hash prior_receipt_binding_hash checkpoint_admission_hash','p6.diagnostic_invocation.v6':' prior_state_binding_hash genesis_admission_hash'}.get(version)
    _need(extra is not None,'POLICY_UNSUPPORTED')
    record(d,version,'run_id session_id plan_hash params_hash input_state_hash segment_seq segment_hash prior_event_hash intake_binding_hash'+extra)
    _need(_int(d['segment_seq']) and d['segment_seq']==k['segment_seq'],'SEQUENCE_INVALID')
    _need(H(d)==old['diagnostic_commit']['invocation_hash'] and H(x)==old['execution_commit']['invocation_hash'],'HASH_MISMATCH')
    _need(d['run_id']==m['run_id'] and d['session_id']==sid and d['plan_hash']==m['plan_hash']
          and d['params_hash']==m['params_hash'] and d['segment_hash']==m['segment_id'],'IDENTITY_MISMATCH')
    record(i,'p6.intake_binding.v1','run_id session_id bundle_hash validation_hash bar_event_hash')
    validate_bundle(b,p['intake_validation'])
    _need(i['run_id']==m['run_id'] and i['session_id']==sid and H(i)==d['intake_binding_hash']
          and H(b)==i['bundle_hash'] and H(p['intake_validation'])==i['validation_hash']
          and i['bar_event_hash']==d['prior_event_hash'],'HASH_MISMATCH')
    _need(H(b['plan'])==m['plan_hash'] and H(b['reservation'])==m['run_id']
          and H(b['segments'][-1])==m['segment_id']
          and b['segments'][-1]['accepted_receipt_hash']==H(b['receipts'][-1]),'IDENTITY_MISMATCH')
    _need(type(x) is dict)
    if old['execution_commit']['schema_version']=='p6.bar_call_commit.v1':
        record(x,'p6.bar_invocation.v1','run_id session_id diagnostic_commit_hash decision_event_hash input_state_hash observation_hash target')
        count=k['segment_seq'];warm=state['params']['warmup_bars']
        target=None if count<=warm else state['params']['targets'][(count-warm-1)%2]
        _need(exact(x['target'],target) and x['diagnostic_commit_hash']==H(old['diagnostic_commit'])
              and x['decision_event_hash']==old['diagnostic_commit']['event_hash'],'IDENTITY_MISMATCH')
        _need(all(p[key] is None for key in ('quote_binding','quote_bundle','quote_validation')))
        source=b
    else:
        record(x,'p6.quote_invocation.v2','policy run_id session_id bar_commit_hash raw_state_hash adapted_state_hash barrier_hash intent_event_hash observation_hash quote_receipt_hash quote_binding_hash')
        _need(x['policy']=='p6.quote_after_barrier.v1','POLICY_UNSUPPORTED')
        qb=p['quote_binding'];source=p['quote_bundle']
        record(qb,'p6.quote_receipt_binding.v1','run_id session_id intake_binding_hash barrier_hash intent_event_hash bundle_hash validation_hash quote_receipt_hash')
        validate_bundle(source,p['quote_validation']);extension(b,source)
        _need(exact(b['segments'],source['segments']) and exact(b['prefix'],source['prefix'])
              and len(source['receipts'])>len(b['receipts'])
              and all(e['kind']=='QUOTE' for e in source['receipts'][len(b['receipts']):]),'SEQUENCE_INVALID')
        receipt=source['receipts'][-1];price=source['payloads'][receipt['normalized_payload_hash']]
        observation=dict(bid=price['bid'],ask=price['ask'],**{key:receipt[key] for key in ('received_utc_ms','received_monotonic_ns','clock_session_id')})
        _need(qb['run_id']==m['run_id'] and qb['session_id']==sid and qb['intake_binding_hash']==H(i)
              and H(qb)==x['quote_binding_hash'] and qb['bundle_hash']==H(source)
              and qb['validation_hash']==H(p['quote_validation'])
              and all(qb[key]==x[key] for key in ('barrier_hash','intent_event_hash','quote_receipt_hash'))
              and H(receipt)==x['quote_receipt_hash'] and H(observation)==x['observation_hash'],'HASH_MISMATCH')
    _need(x['run_id']==m['run_id'] and x['session_id']==sid,'IDENTITY_MISMATCH')
    _need(len(source['receipts'])==k['last_receipt_seq'] and H(source['prefix'])==k['accepted_prefix_hash'],'SEQUENCE_INVALID')
    extension(source,state['intake'])
    return H(source),H(source['receipts'][-1])


def verify_prior_receipts(request):
    result=dict(schema_version='p6.prior_receipt_validation.v1',binding_status='INVALID',reason_code=None,
        prior_bundle_hash=None,prior_receipt_head_hash=None,supplied_receipt_ancestry_verified=False,
        runtime_accepted=False,anchor_verified=False,external_reference_verification='UPSTREAM_REQUIRED')
    try:bundle,head=_check(_snapshot(request))
    except _Refused as exc:result['reason_code']=exc.reason
    except (ValueError,TypeError,KeyError,RuntimeError,IndexError,OverflowError,RecursionError):result['reason_code']='SCHEMA_INVALID'
    else:result.update(binding_status='VALID',prior_bundle_hash=bundle,prior_receipt_head_hash=head,supplied_receipt_ancestry_verified=True)
    return result
