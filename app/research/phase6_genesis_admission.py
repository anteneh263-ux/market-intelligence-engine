"""Local first-segment admission from supplied signatures, not live authority."""
from app.research.phase6_start_validation import _snapshot,_shape,_need,_Refused,_int,verify_start_bindings
from app.research.phase6_event_validation import _manifest
from app.research.phase6_anchor_validation import verify_anchors
from app.research.phase6_checkpoint_validation import exact
from app.research.phase6_validation import canonical_hash as H


def verify_genesis_admission(request):
    result=dict(schema_version='p6.genesis_admission_validation.v1',binding_status='INVALID',reason_code=None,
                genesis_hash=None,last_anchor_hash=None,runtime_accepted=False,
                independent_retention_verified=False,external_authority_verification='UPSTREAM_REQUIRED')
    try:
        q=_snapshot(request);_shape(q,'manifest proof utc_ms')
        m,proof,now=q['manifest'],q['proof'],q['utc_ms'];_manifest(m);_need(_int(now))
        _shape(proof,'start anchor');start,a=proof['start'],proof['anchor']
        v=verify_start_bindings(start);_need(v['binding_status']=='VALID','START_INVALID')
        _shape(a,'trust receipts head challenge now_utc_ms')
        _need(_int(a['now_utc_ms']) and a['head'] is not None and a['challenge'] is not None,'ANCHOR_INVALID')
        _need(exact(a['receipts'],start['anchor']['receipts']),'START_PREFIX_MISMATCH')
        p=start['plan'];subjects=[dict(subject_kind='RESERVATION',subject_hash=v['run_id']),dict(subject_kind='GENESIS',subject_hash=v['genesis_hash'])]
        for observed in (a['now_utc_ms'],now):
            checked=verify_anchors(dict(a,now_utc_ms=observed,expected_trust_hash=p['anchor_trust_hash'],expected_run_id=v['run_id'],expected_subjects=subjects))
            _need(checked['cryptographic_status']=='VALID' and checked['head_status']=='MATCHED','ANCHOR_INVALID')
        _need(start['anchor']['now_utc_ms']<=a['now_utc_ms']<=m['persisted_utc_ms']<=now
              and m['persisted_utc_ms']>=p['start_ms'],'CLOCK_INVALID')
        horizon=p['end_ms']+p['max_lateness_ms']+p['max_decision_delay_ms']+p['max_quote_delay_ms']
        _need(a['head']['retention_until_ms']>horizon,'CLOCK_INVALID')
        _need(m['run_id']==v['run_id'] and m['plan_hash']==H(p) and m['params_hash']==p['params_hash']
              and m['strategy_code_hash']==p['strategy_code_hash']
              and m['prior_checkpoint_hash']==v['genesis_hash']
              and m['prior_anchor_hash']==checked['last_anchor_hash'],'IDENTITY_MISMATCH')
    except _Refused as exc:result['reason_code']=exc.reason
    except (ValueError,TypeError,KeyError,RuntimeError,IndexError,OverflowError,RecursionError):result['reason_code']='SCHEMA_INVALID'
    else:result.update(binding_status='VALID',genesis_hash=v['genesis_hash'],last_anchor_hash=checked['last_anchor_hash'])
    return result


def open_genesis_session(writer,manifest,proof,*,utc_ms,monotonic_ns):
    """Require first-segment local admission; does not assert live readiness."""
    from app.research.phase6_event_journal import EventJournal
    if proof is None:raise ValueError('ADMISSION_REQUIRED')
    return EventJournal(writer,manifest,utc_ms=utc_ms,monotonic_ns=monotonic_ns,genesis_admission=proof)
