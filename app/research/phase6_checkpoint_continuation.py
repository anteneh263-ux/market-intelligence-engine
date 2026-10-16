"""Local continuation gate using supplied signed evidence; no service authority."""
import copy
from app.research.phase6_checkpoint_anchor_validation import verify_checkpoint_anchors
from app.research.phase6_start_validation import _snapshot, _shape, _need, _Refused, _int
from app.research.phase6_event_validation import _manifest
from app.research.phase6_validation import canonical_hash as H


def verify_checkpoint_continuation(request):
    result=dict(schema_version='p6.checkpoint_continuation_validation.v1',binding_status='INVALID',
                reason_code=None,prior_checkpoint_hash=None,last_anchor_hash=None,next_segment_seq=None,
                runtime_accepted=False,independent_retention_verified=False,
                external_authority_verification='UPSTREAM_REQUIRED')
    try:
        q=_snapshot(request);_shape(q,'manifest proof utc_ms')
        m,p,now=q['manifest'],q['proof'],q['utc_ms'];_manifest(m);_need(_int(now))
        # Re-evaluate head freshness at the supplied opening time, not merely the
        # earlier recorded validation time. Authentic clock/service remain upstream.
        _shape(p,'start checkpoints anchor')
        _need(type(p['anchor']) is dict and _int(p['anchor'].get('now_utc_ms')))
        _need(p['anchor']['now_utc_ms'] <= m['persisted_utc_ms'] <= now,'CLOCK_INVALID')
        original=verify_checkpoint_anchors(p);_need(original['binding_status']=='VALID','ANCHOR_INVALID')
        fresh=copy.deepcopy(p);fresh['anchor']['now_utc_ms']=now
        v=verify_checkpoint_anchors(fresh);_need(v['binding_status']=='VALID','ANCHOR_INVALID')
        prior=p['checkpoints'][-1];pm,k,s=prior['manifest'],prior['checkpoint'],prior['state']
        _need(not k['stopped'] and s['pending'] is None,'STATE_INVALID')
        _need(all(m[key]==pm[key] for key in ('run_id','plan_hash','params_hash','strategy_code_hash','engine_version','engine_code_hash'))
              and m['prior_checkpoint_hash']==v['checkpoint_hash']
              and m['prior_anchor_hash']==v['last_anchor_hash'],'IDENTITY_MISMATCH')
        _need(m['persisted_utc_ms']>=p['anchor']['receipts'][-1]['retained_utc_ms'],'CLOCK_INVALID')
    except _Refused as exc:result['reason_code']=exc.reason
    except (ValueError,TypeError,KeyError,RuntimeError,IndexError,OverflowError,RecursionError):result['reason_code']='SCHEMA_INVALID'
    else:result.update(binding_status='VALID',prior_checkpoint_hash=v['checkpoint_hash'],
                       last_anchor_hash=v['last_anchor_hash'],next_segment_seq=k['segment_seq']+1)
    return result


def open_checkpoint_continuation(writer,manifest,proof,*,utc_ms,monotonic_ns):
    """Require supplied checkpoint admission; does not open a genesis/restart path."""
    from app.research.phase6_event_journal import EventJournal
    if proof is None:raise ValueError('ADMISSION_REQUIRED')
    return EventJournal(writer,manifest,utc_ms=utc_ms,monotonic_ns=monotonic_ns,checkpoint_admission=proof)
