"""Exact local bar-call preconditions/postconditions, not data provenance."""
import copy
import json
from fractions import Fraction as F
from app.paper.session import shadow_reducer_v1 as r


def _need(ok, reason):
    if not ok:raise ValueError(reason)


def _bytes(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def prepare(plan, decision, state, observation):
    config={k:plan[k] for k in r.CONFIG_DEC+r.CONFIG_INT}
    r.config_check(config);r.state_check(state)
    _need(_bytes(state['config'])==_bytes(config),'IDENTITY_MISMATCH')
    _need(not state['stopped'] and state['pending'] is None,'STATE_INVALID')
    count=decision['output']['state']['bar_count']
    _need(state['bar_count']+1==count,'SEQUENCE_INVALID')
    expected_previous=None if state['bar_count']==0 else plan['start_ms']+state['bar_count']*plan['timeframe_ms']
    _need(state['last_bar_close_ms']==expected_previous,'SEQUENCE_INVALID')
    o=observation;d=decision['payload']
    _need(type(o) is dict and set(o)=={'close_ms','close','received_utc_ms','received_monotonic_ns','clock_session_id','decision_utc_ms','decision_monotonic_ns'},'INPUT_INVALID')
    _need(all(r.integer(o[k]) for k in ('close_ms','received_utc_ms','received_monotonic_ns','decision_utc_ms','decision_monotonic_ns')),'INPUT_INVALID')
    _need(r.amount(o['close'])>0,'INPUT_INVALID')
    _need(o['close_ms']==plan['start_ms']+count*plan['timeframe_ms'] and o['close_ms']<=plan['end_ms'],'SEQUENCE_INVALID')
    _need(o['decision_utc_ms']==d['callback_finished_utc_ms'] and o['decision_monotonic_ns']==d['callback_finished_monotonic_ns'] and o['clock_session_id']==d['clock_session_id'],'CLOCK_INVALID')
    _need(o['close_ms']<=o['received_utc_ms']<=d['callback_started_utc_ms'] and o['received_monotonic_ns']<=d['callback_started_monotonic_ns'],'CLOCK_INVALID')
    r._clock(state,o)
    delay=plan['max_decision_delay_ms']
    _need(0<=o['decision_utc_ms']-o['received_utc_ms']<=delay and 0<=o['decision_monotonic_ns']-o['received_monotonic_ns']<=delay*1000000,'CLOCK_INVALID')
    if state['last_action_monotonic_ns'] is not None:
        _need(o['decision_monotonic_ns']>state['last_action_monotonic_ns'] and o['decision_utc_ms']>=state['last_action_utc_ms'],'CLOCK_INVALID')
    return decision['output']['target']


def check_output(before, observation, target, after):
    r.state_check(after)
    expected=copy.deepcopy(before);o=observation;c=before['config']
    mark=F(o['close']);equity=F(before['cash'])+F(before['units'])*mark
    peak=max(F(before['peak_equity']),equity)
    stopped=(peak-equity)/peak>=F(c['max_drawdown'])
    expected.update(bar_count=before['bar_count']+1,last_bar_close_ms=o['close_ms'],
        last_receipt_monotonic_ns=o['received_monotonic_ns'],last_action_utc_ms=o['decision_utc_ms'],
        last_action_monotonic_ns=o['decision_monotonic_ns'],clock_session_id=o['clock_session_id'],
        mark_price=r.text(mark),equity=r.text(equity),peak_equity=r.text(peak),
        stopped=stopped,reason_code='RISK_STOP' if stopped else None,pending=None)
    if before['bar_count']>=c['warmup_bars'] and not stopped:
        step=F(c['quantity_step']);desired=(F(target)*equity/mark//step)*step
        delta=desired-F(before['units'])
        if delta:expected['pending']=dict(side='BUY' if delta>0 else 'SELL',quantity=r.text(abs(delta)),intent_utc_ms=o['decision_utc_ms'],intent_monotonic_ns=o['decision_monotonic_ns'])
    _need(_bytes(after)==_bytes(expected),'EXECUTION_INVALID')
