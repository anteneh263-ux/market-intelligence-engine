"""Pure local prior-state continuity, not anchored admission or restart authority."""
import json
from app.paper.session import diagnostic_strategy_v1 as strategy,shadow_reducer_v1 as reducer
from app.research.phase6_start_validation import _snapshot,_Refused,_need,_shape
from app.research.phase6_event_validation import _manifest
from app.research.phase6_intake_validation import verify_intake_prefix
from app.research.phase6_checkpoint_validation import verify_checkpoint
from app.research.phase6_validation import canonical_hash as H


def exact(a,b):return json.dumps(a,sort_keys=True,separators=(',',':'))==json.dumps(b,sort_keys=True,separators=(',',':'))


def _check(q, *,capacity=None):
    _shape(q,'manifest intake prior params state strategy_state')
    m,b,prior,pa,s,ss=(q[k] for k in ('manifest','intake','prior','params','state','strategy_state'))
    _manifest(m)
    v=verify_intake_prefix(b, capacity=capacity);_need(v['binding_status']=='VALID','INTAKE_INVALID')
    p=b['plan'];seg=b['segments'][-1];receipt=b['receipts'][-1]
    _need(m['run_id']==H(b['reservation']) and m['plan_hash']==H(p) and m['segment_id']==H(seg)
          and seg['accepted_receipt_hash']==H(receipt),'IDENTITY_MISMATCH')
    pa=strategy.decode(strategy.encode(pa));ss=strategy.decode(strategy.encode(ss))
    _need(pa['schema_version']==strategy.PARAMS and ss['schema_version']==strategy.STATE)
    reducer.state_check(s)
    _need(H(pa)==p['params_hash']==m['params_hash'] and pa['warmup_bars']==p['warmup_bars']
          and m['strategy_code_hash']==p['strategy_code_hash'],'IDENTITY_MISMATCH')
    cfg={k:p[k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT}
    _need(exact(s['config'],cfg),'STATE_INVALID')
    _need(not s['stopped'] and s['pending'] is None,'STATE_INVALID')
    _need(type(prior) is dict)
    if prior.get('schema_version')=='p6.genesis.v1':
        _shape(prior,'schema_version run_id plan_hash cash units cost_basis cumulative_fees cumulative_realized_pnl equity peak_equity strategy_state_hash')
        _need(exact(s,reducer.initial(cfg)) and exact(ss,strategy.initial(pa)),'STATE_INVALID')
        expected=dict(schema_version='p6.genesis.v1',run_id=m['run_id'],plan_hash=H(p),strategy_state_hash=H(ss),
            **{k:s[k] for k in ('cash','units','cost_basis','cumulative_fees','cumulative_realized_pnl','equity','peak_equity')})
        _need(exact(prior,expected) and H(prior)==m['prior_checkpoint_hash'],'IDENTITY_MISMATCH')
        _need(seg['segment_seq']==1 and seg['previous_segment_hash']=='0'*64,'SEQUENCE_INVALID')
        return 'GENESIS',H(prior)
    v=verify_checkpoint(prior, capacity=capacity);_need(v['binding_status']=='VALID','CHECKPOINT_INVALID')
    k=prior['checkpoint'];pm=prior['manifest'];event=prior['event']
    _need(H(k)==m['prior_checkpoint_hash'] and all(m[key]==pm[key] for key in
          ('run_id','plan_hash','params_hash','strategy_code_hash','engine_version','engine_code_hash')),'IDENTITY_MISMATCH')
    _need(exact(s,prior['state']) and exact(ss,prior['strategy_state']),'STATE_INVALID')
    _need(seg['segment_seq']==k['segment_seq']+1 and len(b['segments'])>=2
          and seg['previous_segment_hash']==pm['segment_id']==H(b['segments'][-2]),'SEQUENCE_INVALID')
    prefix=dict(schema_version='p6.accepted_prefix.v1',run_id=m['run_id'],segment_hashes=[H(x) for x in b['segments'][:-1]])
    _need(H(prefix)==k['accepted_prefix_hash'],'HASH_MISMATCH')
    n=k['last_receipt_seq']
    _need(1<=n<receipt['receipt_seq'] and n<=len(b['receipts']),'SEQUENCE_INVALID')
    earlier=b['receipts'][n-1]
    _need(s['last_bar_close_ms']==seg['start_ms'],'SEQUENCE_INVALID')
    _need(receipt['clock_session_id']==s['clock_session_id']
          and receipt['received_utc_ms']>=event['utc_ms']
          and receipt['received_monotonic_ns']>event['monotonic_ns']
          and m['persisted_utc_ms']>=event['utc_ms']
          and earlier['received_utc_ms']<=event['utc_ms']
          and earlier['received_monotonic_ns']<=event['monotonic_ns'],'CLOCK_INVALID')
    return 'CHECKPOINT',H(k)


def verify_prior_state(request, *,capacity=None):
    result=dict(schema_version='p6.prior_state_validation.v1',binding_status='INVALID',reason_code=None,
        prior_kind=None,prior_hash=None,runtime_accepted=False,anchor_verified=False,
        prior_receipt_ancestry_verified=False,external_reference_verification='UPSTREAM_REQUIRED')
    try:kind,digest=_check(_snapshot(request, capacity=capacity), capacity=capacity)
    except _Refused as exc:result['reason_code']=exc.reason
    except (ValueError,TypeError,KeyError,RuntimeError,IndexError,OverflowError,RecursionError):result['reason_code']='SCHEMA_INVALID'
    else:result.update(binding_status='VALID',prior_kind=kind,prior_hash=digest)
    return result
