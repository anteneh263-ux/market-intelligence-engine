import copy
from decimal import localcontext
import pytest
from app.paper.session import shadow_reducer_v1 as e

def config(**kw):
    c=dict(initial_cash='1000',quantity_step='1',tick_size='1',max_target='1',fee_bps='100',slippage_bps='0',max_drawdown='0.5',warmup_bars=0,max_decision_delay_ms=10,max_quote_delay_ms=10);c.update(kw);return c

def bar(close='100',tick=100):
    return dict(close_ms=tick,close=close,received_utc_ms=tick,received_monotonic_ns=tick*1000000,clock_session_id='test',decision_utc_ms=tick+1,decision_monotonic_ns=(tick+1)*1000000)

def quote(price='100',tick=102):
    return dict(bid=price,ask=price,received_utc_ms=tick,received_monotonic_ns=tick*1000000,clock_session_id='test')

def bought():return e.quote(e.bar(e.initial(config()),bar(),'1'),quote())

def test_no_fill_on_bar_or_before_intent():
    s=e.initial(config());pending=e.bar(s,bar(),'1')
    assert pending['cash']=='1000' and pending['units']=='0'
    with pytest.raises(ValueError,match='CLOCK_INVALID'):e.quote(pending,quote(tick=100))
    assert s==e.initial(config())

def test_buy_sell_exact_reconciliation():
    s=bought();assert (s['cash'],s['units'],s['cumulative_fees'],s['cost_basis'])==('91','9','9','900')
    s=e.quote(e.bar(s,bar('110',200),'0'),quote('110',202))
    assert (s['cash'],s['units'],s['cumulative_fees'],s['cumulative_realized_pnl'],s['cost_basis'])==('1071.1','0','18.9','90','0')

def test_no_duplicate_fill():
    with pytest.raises(ValueError,match='NO_PENDING_INTENT'):e.quote(bought(),quote(tick=103))

def test_bar_boundary_preserves_position():
    s=bought();n=e.bar(s,bar('100',200),'1')
    assert n['units']=='9' and n['cash']=='91'

def test_pending_not_replaced():
    s=e.bar(e.initial(config()),bar(),'1')
    with pytest.raises(ValueError,match='PENDING_INTENT'):e.bar(s,bar(tick=200),'0')

def test_quote_timeout_preserves_intent():
    s=e.bar(e.initial(config()),bar(),'1');n=e.quote(s,quote(tick=112))
    assert n['stopped'] and n['reason_code']=='QUOTE_TIMEOUT' and n['pending']==s['pending'] and n['units']=='0'

def test_risk_stop_keeps_position():
    n=e.bar(bought(),bar('1',200),'1')
    assert n['stopped'] and n['reason_code']=='RISK_STOP' and n['units']=='9'
    with pytest.raises(ValueError,match='RUN_STOPPED'):e.bar(n,bar(tick=300),'0')

def test_warmup():
    s=e.initial(config(warmup_bars=1));n=e.bar(s,bar(),None)
    assert n['pending'] is None and n['bar_count']==1
    with pytest.raises(ValueError,match='TARGET_INVALID'):e.bar(s,bar(),'1')

def test_midpoint_precision():
    s=e.initial(config(initial_cash='1',quantity_step='0.00000001',tick_size='0.00000001',fee_bps='0'))
    s=e.bar(s,bar('1'),'1');q=quote('1');q['ask']='1.00000001';n=e.quote(s,q)
    assert n['mark_price']=='1.000000005'
    assert n['equity']=='0.99999999500000005'

def test_decimal_context_independence():
    with localcontext() as c:c.prec=3;low=bought()
    with localcontext() as c:c.prec=50;high=bought()
    assert low==high

@pytest.mark.parametrize('field,value',[('units','0.1'),('cost_basis','1'),('cash','-1'),('bar_count',True)])
def test_invalid_snapshot(field,value):
    s=e.initial(config());s[field]=value
    with pytest.raises(ValueError,match='STATE_INVALID'):e.bar(s,bar(),'1')

def test_input_not_mutated():
    s=e.initial(config());b=bar();old=copy.deepcopy((s,b));e.bar(s,b,'1');assert (s,b)==old

def test_stop_keeps_pending():
    s=e.bar(e.initial(config()),bar(),'1');n=e.stop(s,'OPERATOR_STOP')
    assert n['pending']==s['pending'] and n['stopped'] and not s['stopped']

def test_action_backdating():
    s=e.bar(e.initial(config()),bar(),'0');b=bar(tick=101);b['decision_monotonic_ns']=101000000
    with pytest.raises(ValueError,match='CLOCK_INVALID'):e.bar(s,b,'0')

def test_partial_basis_rounding_and_final_remainder():
    s=e.initial(config(initial_cash='10',fee_bps='0'))
    s=e.quote(e.bar(s,bar('3'),'0.6'),quote('3'))
    s=e.quote(e.bar(s,bar('4',200),'1'),quote('4',202))
    assert s['units']=='3' and s['cost_basis']=='10'
    s=e.quote(e.bar(s,bar('4',300),'0.7'),quote('4',302))
    assert s['cost_basis']=='6.6666666666666667' and s['cumulative_realized_pnl']=='0.6666666666666667'
    s=e.quote(e.bar(s,bar('4',400),'0'),quote('4',402))
    assert s['cash']=='12' and s['cost_basis']=='0' and s['cumulative_realized_pnl']=='2'

def test_quote_price_caps_exposure():
    s=e.bar(e.initial(config(max_target='0.5',fee_bps='0')),bar(),'0.5')
    n=e.quote(s,quote('200'))
    assert n['units']=='2' and n['cash']=='600'
    assert e.F(n['units'])*e.F(n['mark_price'])<=e.F('0.5')*e.F(n['equity'])

def test_slippage_and_tick_rounding():
    s=e.bar(e.initial(config(fee_bps='0',slippage_bps='1')),bar(),'0.1')
    n=e.quote(s,quote());assert n['cost_basis']=='101' and n['cash']=='899'
    n=e.quote(e.bar(n,bar(tick=200),'0'),quote(tick=202))
    assert n['cash']=='998' and n['cumulative_realized_pnl']=='-2'

def test_timeout_state_is_valid_but_cannot_resume():
    n=e.quote(e.bar(e.initial(config()),bar(),'1'),quote(tick=112));e.state_check(n)
    with pytest.raises(ValueError,match='RUN_STOPPED'):e.quote(n,quote(tick=113))

def test_no_io(monkeypatch):
    import builtins,socket
    def refuse(*a,**kw):pytest.fail('I/O from pure reducer')
    monkeypatch.setattr(builtins,'open',refuse);monkeypatch.setattr(socket,'socket',refuse)
    assert bought()['units']=='9'

def test_cash_identity_over_many_quotes():
    import random
    rng=random.Random(81);s=e.initial(config(max_drawdown='0.99'))
    for i in range(50):
        price=str(rng.randrange(90,111));tick=100+100*i
        s=e.bar(s,bar(price,tick),str(rng.randrange(0,2)))
        if s['pending']:s=e.quote(s,quote(price,tick+2))
        assert e.F(s['cash'])+e.F(s['cost_basis'])==e.F('1000')+e.F(s['cumulative_realized_pnl'])-e.F(s['cumulative_fees'])
        assert not s['stopped']

def test_invented_cash_with_rebalanced_equity_refused():
    s=bought();s.update(cash='191',equity='1091',peak_equity='1091')
    with pytest.raises(ValueError,match='STATE_INVALID'):e.bar(s,bar(tick=200),'0')
