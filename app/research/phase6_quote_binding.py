"""Versioned local barrier adapter and exact quote postconditions."""
import copy,json,re
from fractions import Fraction as F
from app.paper.session import shadow_reducer_v1 as r
from app.research.phase6_validation import canonical_hash as H
POLICY='p6.quote_after_barrier.v1'


def _need(ok,reason):
    if not ok:raise ValueError(reason)


def _bytes(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def prepare(context,observation,receipt_hash,run_id,session_id):
    _need(type(receipt_hash) is str and re.fullmatch('[0-9a-f]{64}',receipt_hash) is not None,'INPUT_INVALID')
    s=context['state'];b=context['barrier'];o=observation
    r.state_check(s)
    _need(not s['stopped'] and s['pending'] is not None,'STATE_INVALID')
    _need(H(b)==context['barrier_hash'] and b['intent_event_hash']==context['event_hash']
          and b['run_id']==run_id and b['session_id']==session_id,'IDENTITY_MISMATCH')
    _need(type(o) is dict and set(o)=={'bid','ask','received_utc_ms','received_monotonic_ns','clock_session_id'},'INPUT_INVALID')
    _need(0<r.amount(o['bid'])<=r.amount(o['ask']),'INPUT_INVALID')
    _need(r.integer(o['received_utc_ms']) and r.integer(o['received_monotonic_ns']),'INPUT_INVALID')
    _need(o['clock_session_id']==s['clock_session_id']==b['clock_session_id']
          and o['received_utc_ms']>=b['utc_ms']>=s['last_action_utc_ms']
          and o['received_monotonic_ns']>b['monotonic_ns']>=s['last_action_monotonic_ns'],'CLOCK_INVALID')
    adapted=copy.deepcopy(s)
    adapted['pending'].update(intent_utc_ms=b['utc_ms'],intent_monotonic_ns=b['monotonic_ns'])
    adapted.update(last_action_utc_ms=b['utc_ms'],last_action_monotonic_ns=b['monotonic_ns'])
    r.state_check(adapted)
    return adapted


def check_output(before,o,after):
    r.state_check(after)
    c=before['config'];p=before['pending'];expected=copy.deepcopy(before)
    expected.update(last_receipt_monotonic_ns=o['received_monotonic_ns'],
                    last_action_utc_ms=o['received_utc_ms'],last_action_monotonic_ns=o['received_monotonic_ns'])
    delay=c['max_quote_delay_ms']
    if o['received_utc_ms']-p['intent_utc_ms']>delay or o['received_monotonic_ns']-p['intent_monotonic_ns']>delay*1000000:
        expected.update(stopped=True,reason_code='QUOTE_TIMEOUT')
        _need(_bytes(expected)==_bytes(after),'EXECUTION_INVALID')
        return dict(kind='STOP',fields=dict(reason_code='QUOTE_TIMEOUT'))
    buy=p['side']=='BUY';units=F(before['units']);cash=F(before['cash']);basis=F(before['cost_basis'])
    mid=(F(o['bid'])+F(o['ask']))/2;step=F(c['quantity_step']);requested=F(p['quantity'])
    price=r.ceil(F(o['ask'])*(1+F(c['slippage_bps'])/10000),F(c['tick_size'])) if buy else r.floor(F(o['bid'])*(1-F(c['slippage_bps'])/10000),F(c['tick_size']))
    _need(price>0,'EXECUTION_INVALID')
    qty=F(after['units'])-units if buy else units-F(after['units'])
    _need(0<=qty<=requested and qty%step==0,'EXECUTION_INVALID')
    def fee_for(q):return r.ceil(q*price*F(c['fee_bps'])/10000,r.MONEY)
    def fits(q):
        remaining=cash-q*price-fee_for(q);held=units+q
        return remaining>=0 and held*mid<=F(c['max_target'])*(remaining+held*mid)
    if buy:
        _need((qty==0 or fits(qty)) and (qty==requested or not fits(qty+step)),'EXECUTION_INVALID')
    else:_need(qty==requested and qty<=units,'EXECUTION_INVALID')
    fee=fee_for(qty);notional=qty*price;realized=F(before['cumulative_realized_pnl'])
    if buy:
        new_cash=cash-notional-fee;new_units=units+qty;new_basis=basis+notional
    else:
        allocated=basis if qty==units else r.floor(basis*qty/units,r.MONEY)
        new_cash=cash+notional-fee;new_units=units-qty;new_basis=basis-allocated;realized+=notional-allocated
    equity=new_cash+new_units*mid;peak=max(F(before['peak_equity']),equity)
    stopped=(peak-equity)/peak>=F(c['max_drawdown'])
    expected.update(cash=r.text(new_cash),units=r.text(new_units),cost_basis=r.text(new_basis),
        cumulative_fees=r.text(F(before['cumulative_fees'])+fee),cumulative_realized_pnl=r.text(realized),
        pending=None,mark_price=r.text(mid),equity=r.text(equity),peak_equity=r.text(peak),
        stopped=stopped,reason_code='RISK_STOP' if stopped else None)
    _need(_bytes(after)==_bytes(expected),'EXECUTION_INVALID')
    if qty==0:return dict(kind='REJECTED',fields=dict(reason_code='CASH_CAP_ZERO'))
    return dict(kind='SIMULATED_FILL',fields=dict(side=p['side'],quantity=r.text(qty),price=r.text(price),fee=r.text(fee)))
