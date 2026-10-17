"""Pure binding checks for supplied local checkpoint artifacts, never admission."""
import json
from app.paper.session import diagnostic_strategy_v1 as strategy, shadow_reducer_v1 as reducer
from app.research.phase6_start_validation import _snapshot, _Refused
from app.research.phase6_event_validation import verify_event, _shape, _need, _hash, _integer
from app.research.phase6_validation import canonical_hash as H

AMOUNTS = 'cash units cumulative_fees cumulative_realized_pnl cost_basis mark_price equity peak_equity'.split()
K_FIELDS = 'schema_version run_id session_id segment_seq cash units cumulative_fees cumulative_realized_pnl cost_basis mark_price equity peak_equity strategy_state_hash accepted_prefix_hash last_receipt_seq pending_intent_hash journal_head_hash journal_count previous_checkpoint_hash stopped'


def exact(a,b):
    return json.dumps(a,sort_keys=True,separators=(',',':')) == json.dumps(b,sort_keys=True,separators=(',',':'))


def commit(value,version,fields):
    _shape(value,'schema_version '+fields)
    _need(value['schema_version']==version)
    for key in fields.split():
        if key.endswith('_hash'):
            _need(_hash(value[key]) or (key in ('event_hash','barrier_hash') and value[key] is None))


def _check(q, *,capacity=None):
    _shape(q,'manifest checkpoint checkpoint_commit event payload previous_event state strategy_state execution_commit diagnostic_commit')
    m,k,c,e,p,previous,s,ss,x,d=(q[key] for key in ('manifest','checkpoint','checkpoint_commit','event','payload','previous_event','state','strategy_state','execution_commit','diagnostic_commit'))
    event_check=verify_event(dict(manifest=m,event=e,payload=p,previous_event=previous))
    _need(event_check['structural_status']=='VALID',event_check['reason_code'] or 'SCHEMA_INVALID')
    _need(e['kind']=='CHECKPOINT_PREPARED' and previous is not None,'SEQUENCE_INVALID')
    _shape(k,K_FIELDS)
    _need(k['schema_version']=='p6.checkpoint.v1' and type(k['stopped']) is bool)
    _need(all(_integer(k[key]) and k[key]>0 for key in ('segment_seq','journal_count','last_receipt_seq')))
    _need(all(_hash(k[key]) for key in ('run_id','session_id','strategy_state_hash','accepted_prefix_hash','journal_head_hash','previous_checkpoint_hash')))
    _need(k['pending_intent_hash'] is None or _hash(k['pending_intent_hash']))
    reducer.state_check(s)
    ss=strategy.decode(strategy.encode(ss))
    _need(ss['schema_version']==strategy.STATE)
    _need(k['run_id']==m['run_id'] and k['session_id']==H(m)
          and k['previous_checkpoint_hash']==m['prior_checkpoint_hash']
          and ss['params_hash']==m['params_hash'] and ss['last_segment_hash']==m['segment_id'],'IDENTITY_MISMATCH')
    _need(k['segment_seq']==ss['bar_count']==s['bar_count'] and k['last_receipt_seq']>=k['segment_seq']
          and k['journal_count']==e['event_seq'],'SEQUENCE_INVALID')
    _need(k['journal_head_hash']==H(e) and p['state_hash']==H(s)
          and k['strategy_state_hash']==p['strategy_state_hash']==H(ss),'HASH_MISMATCH')
    _need(all(exact(k[key],s[key]) for key in AMOUNTS+['stopped']),'STATE_INVALID')
    _need(all(exact(k[key],p[key]) for key in ('strategy_state_hash','accepted_prefix_hash','last_receipt_seq','pending_intent_hash')),'IDENTITY_MISMATCH')
    _need(e['utc_ms']>=s['last_action_utc_ms'] and e['monotonic_ns']>=s['last_action_monotonic_ns'],'CLOCK_INVALID')
    commit(c,'p6.checkpoint_commit.v1','checkpoint_hash event_hash state_hash strategy_state_hash execution_commit_hash diagnostic_commit_hash')
    _need(all(c[key]==H(value) for key,value in [('checkpoint_hash',k),('event_hash',e),('state_hash',s),('strategy_state_hash',ss),('execution_commit_hash',x),('diagnostic_commit_hash',d)]),'HASH_MISMATCH')
    commit(d,'p6.diagnostic_call_commit.v1','invocation_hash event_hash output_state_hash')
    _need(_hash(d['event_hash']) and d['output_state_hash']==H(ss),'HASH_MISMATCH')
    _need(type(x) is dict and type(x.get('schema_version')) is str)
    if x['schema_version']=='p6.bar_call_commit.v1':
        commit(x,'p6.bar_call_commit.v1','invocation_hash output_state_hash event_hash barrier_hash')
        _need(x['barrier_hash'] is None and s['pending'] is None,'STATE_INVALID')
        if x['event_hash'] is None:
            _need(previous['kind']=='WARMUP' and d['event_hash']==H(previous)
                  and s['bar_count']<=s['config']['warmup_bars'] and not s['stopped']
                  and previous['event_seq']==3,'SEQUENCE_INVALID')
        else:
            _need(x['event_hash']==H(previous) and previous['event_seq']==4,'HASH_MISMATCH')
            _need((s['stopped'] and s['reason_code']=='RISK_STOP' and previous['kind']=='STOP')
                  or (not s['stopped'] and previous['kind']=='NO_TRADE'
                      and s['bar_count']>s['config']['warmup_bars']),'STATE_INVALID')
    elif x['schema_version']=='p6.quote_call_commit.v1':
        commit(x,'p6.quote_call_commit.v1','invocation_hash output_state_hash event_hashes')
        events=x['event_hashes']
        _need(type(events) is list and len(events) in (1,2) and all(_hash(h) for h in events)
              and len(set(events))==len(events))
        _need(events[-1]==H(previous) and previous['event_seq']==4+len(events),'HASH_MISMATCH')
        if len(events)==2:
            _need(previous['previous_event_hash']==events[0] and s['stopped']
                  and s['reason_code']=='RISK_STOP' and previous['kind']=='STOP','STATE_INVALID')
        elif s['pending'] is not None:
            _need(s['stopped'] and s['reason_code']=='QUOTE_TIMEOUT' and previous['kind']=='STOP','STATE_INVALID')
        else:
            _need(not s['stopped'] and previous['kind'] in ('SIMULATED_FILL','REJECTED'),'STATE_INVALID')
    else:raise _Refused('SCHEMA_INVALID')
    if s['pending'] is not None:
        _need(x['schema_version']=='p6.quote_call_commit.v1' and len(x['event_hashes'])==1
              and s['stopped'] and s['reason_code']=='QUOTE_TIMEOUT'
              and previous['kind']=='STOP','STATE_INVALID')
    _need(x['output_state_hash']==H(s),'HASH_MISMATCH')
    _need((s['pending'] is None)==(k['pending_intent_hash'] is None),'STATE_INVALID')
    return H(k)


def verify_checkpoint(request, *,capacity=None):
    result=dict(schema_version='p6.checkpoint_validation.v1',binding_status='INVALID',reason_code=None,
        checkpoint_hash=None,runtime_accepted=False,external_reference_verification='UPSTREAM_REQUIRED',
        durability_verified=False,full_journal_verified=False)
    try:
        q=_snapshot(request, capacity=capacity)
        digest=_check(q, capacity=capacity)
    except _Refused as exc:
        result['reason_code']=exc.reason
    except (ValueError,TypeError,KeyError,RuntimeError,IndexError,OverflowError,RecursionError):
        result['reason_code']='SCHEMA_INVALID'
    else:result.update(binding_status='VALID',checkpoint_hash=digest)
    return result
