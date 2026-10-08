"""Pure diagnostic simulation transitions. No clock, feed, storage or approval authority."""
import copy
import re
from fractions import Fraction as F

METHOD='p6.shadow.reducer.v1'
MONEY=F(1,10**16)
TOKEN=re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')
DEC=re.compile(r'-?(0|[1-9][0-9]*)(\.[0-9]*[1-9])?\Z')
CONFIG_DEC=('initial_cash','quantity_step','tick_size','max_target','fee_bps','slippage_bps','max_drawdown')
CONFIG_INT=('warmup_bars','max_decision_delay_ms','max_quote_delay_ms')
PREC={'cash':16,'units':8,'cost_basis':16,'cumulative_fees':16,'cumulative_realized_pnl':16,'equity':17,'peak_equity':17}
STATE=set(PREC)|{'schema_version','config','mark_price','bar_count','last_bar_close_ms','last_receipt_monotonic_ns','last_action_utc_ms','last_action_monotonic_ns','clock_session_id','pending','stopped','reason_code'}


def need(ok,reason):
    if not ok:raise ValueError(reason)


def integer(v):return type(v) is int and 0<=v<2**63


def amount(v,precision=8,limit=10**12,reason='INPUT_INVALID'):
    need(type(v) is str and len(v)<=128 and DEC.fullmatch(v) is not None and v!='-0',reason)
    need(len(v.partition('.')[2])<=precision,reason)
    x=F(v);need(abs(x)<=limit,reason);return x


def text(x):
    x=F(x)
    need(abs(x)<=10**30,'NUMERIC_INVALID')
    for n in range(33):
        y=x*10**n
        if y.denominator==1:
            a=str(abs(y.numerator)).zfill(n+1)
            if n:a=a[:-n]+'.'+a[-n:];a=a.rstrip('0').rstrip('.')
            return ('-' if y<0 else '')+a
    raise ValueError('NUMERIC_INVALID')


def floor(x,step):return (x//step)*step


def ceil(x,step):return -((-x)//step)*step


def config_check(c):
    need(type(c) is dict and set(c)==set(CONFIG_DEC+CONFIG_INT),'INPUT_INVALID')
    v={k:amount(c[k]) for k in CONFIG_DEC}
    need(all(integer(c[k]) for k in CONFIG_INT),'INPUT_INVALID')
    need(all(v[k]>0 for k in ('initial_cash','quantity_step','tick_size')) and 0<v['max_target']<=1 and 0<v['max_drawdown']<1 and all(0<=v[k]<10000 for k in ('fee_bps','slippage_bps')),'INPUT_INVALID')
    return v


def state_check(s):
    try:
        need(type(s) is dict and set(s)==STATE and s['schema_version']==METHOD,'STATE_INVALID')
        c=config_check(s['config']);v={k:amount(s[k],n,10**30,'STATE_INVALID') for k,n in PREC.items()}
        need(all(v[k]>=0 for k in PREC if k!='cumulative_realized_pnl'),'STATE_INVALID')
        need(integer(s['bar_count']) and type(s['stopped']) is bool,'STATE_INVALID')
        need((s['reason_code'] in ('RISK_STOP','OPERATOR_STOP','QUOTE_TIMEOUT')) if s['stopped'] else s['reason_code'] is None,'STATE_INVALID')
        need(v['units']%c['quantity_step']==0 and (v['units']!=0 or v['cost_basis']==0),'STATE_INVALID')
        times=('last_bar_close_ms','last_receipt_monotonic_ns','last_action_utc_ms','last_action_monotonic_ns')
        if s['bar_count']==0:
            need(all(s[k] is None for k in times) and s['mark_price'] is None and s['clock_session_id'] is None and s['pending'] is None,'STATE_INVALID')
            need(v['cash']==v['equity']==v['peak_equity']==c['initial_cash'] and all(v[k]==0 for k in ('units','cost_basis','cumulative_fees','cumulative_realized_pnl')),'STATE_INVALID')
        else:
            need(all(integer(s[k]) for k in times) and type(s['clock_session_id']) is str and TOKEN.fullmatch(s['clock_session_id']) is not None,'STATE_INVALID')
            mark=amount(s['mark_price'],9,10**12,'STATE_INVALID')
            need(mark>0 and v['equity']==v['cash']+v['units']*mark and s['last_action_monotonic_ns']>=s['last_receipt_monotonic_ns'],'STATE_INVALID')
        need(v['cash']+v['cost_basis']+v['cumulative_fees']==c['initial_cash']+v['cumulative_realized_pnl'],'STATE_INVALID')
        need(v['peak_equity']>=v['equity'] and v['peak_equity']>0,'STATE_INVALID')
        p=s['pending']
        if p is not None:
            need(type(p) is dict and set(p)=={'side','quantity','intent_utc_ms','intent_monotonic_ns'} and p['side'] in ('BUY','SELL'),'STATE_INVALID')
            qty=amount(p['quantity'],8,10**30,'STATE_INVALID');need(qty>0 and qty%c['quantity_step']==0 and (p['side']!='SELL' or qty<=v['units']),'STATE_INVALID')
            need(integer(p['intent_utc_ms']) and integer(p['intent_monotonic_ns']),'STATE_INVALID')
            if s['stopped'] and s['reason_code']=='QUOTE_TIMEOUT':
                need(p['intent_utc_ms']<=s['last_action_utc_ms'] and p['intent_monotonic_ns']<=s['last_action_monotonic_ns'],'STATE_INVALID')
            else:need(p['intent_utc_ms']==s['last_action_utc_ms'] and p['intent_monotonic_ns']==s['last_action_monotonic_ns'],'STATE_INVALID')
        return c,v
    except (ValueError,TypeError,KeyError,ZeroDivisionError):raise ValueError('STATE_INVALID') from None


def initial(config):
    c=config_check(config)
    s=dict(schema_version=METHOD,config=copy.deepcopy(config),cash=text(c['initial_cash']),units='0',cost_basis='0',cumulative_fees='0',cumulative_realized_pnl='0',equity=text(c['initial_cash']),peak_equity=text(c['initial_cash']),mark_price=None,bar_count=0,last_bar_close_ms=None,last_receipt_monotonic_ns=None,last_action_utc_ms=None,last_action_monotonic_ns=None,clock_session_id=None,pending=None,stopped=False,reason_code=None)
    state_check(s);return s


def _ready(s):
    c,v=state_check(s);need(not s['stopped'],'RUN_STOPPED');return copy.deepcopy(s),c,v


def _clock(s,o):
    need(integer(o['received_utc_ms']) and integer(o['received_monotonic_ns']) and type(o['clock_session_id']) is str and TOKEN.fullmatch(o['clock_session_id']) is not None,'INPUT_INVALID')
    if s['clock_session_id'] is not None:
        need(o['clock_session_id']==s['clock_session_id'] and o['received_monotonic_ns']>s['last_receipt_monotonic_ns'],'CLOCK_INVALID')


def _mark(s,c,mark):
    s['mark_price']=text(mark);equity=F(s['cash'])+F(s['units'])*mark
    peak=max(F(s['peak_equity']),equity);s['equity']=text(equity);s['peak_equity']=text(peak)
    if (peak-equity)/peak>=c['max_drawdown']:s.update(stopped=True,reason_code='RISK_STOP')


def _output(s):
    try:state_check(s)
    except ValueError:raise ValueError('NUMERIC_INVALID') from None
    return s


def bar(state,observation,target):
    s,c,v=_ready(state);o=observation
    need(type(o) is dict and set(o)=={'close_ms','close','received_utc_ms','received_monotonic_ns','clock_session_id','decision_utc_ms','decision_monotonic_ns'},'INPUT_INVALID')
    price=amount(o['close']);need(price>0 and all(integer(o[k]) for k in ('close_ms','decision_utc_ms','decision_monotonic_ns')),'INPUT_INVALID');_clock(s,o)
    need(s['pending'] is None,'PENDING_INTENT')
    need(s['last_bar_close_ms'] is None or o['close_ms']>s['last_bar_close_ms'],'SEQUENCE_INVALID')
    delay=s['config']['max_decision_delay_ms']
    need(o['received_utc_ms']>=o['close_ms'] and 0<=o['decision_utc_ms']-o['received_utc_ms']<=delay and 0<=o['decision_monotonic_ns']-o['received_monotonic_ns']<=delay*10**6,'CLOCK_INVALID')
    if s['last_action_monotonic_ns'] is not None:need(o['decision_monotonic_ns']>s['last_action_monotonic_ns'] and o['decision_utc_ms']>=s['last_action_utc_ms'],'CLOCK_INVALID')
    warm=s['bar_count']<s['config']['warmup_bars']
    if warm:need(target is None,'TARGET_INVALID');t=None
    else:
        t=amount(target,reason='TARGET_INVALID');need(0<=t<=c['max_target'],'TARGET_INVALID')
    s.update(bar_count=s['bar_count']+1,last_bar_close_ms=o['close_ms'],last_receipt_monotonic_ns=o['received_monotonic_ns'],last_action_utc_ms=o['decision_utc_ms'],last_action_monotonic_ns=o['decision_monotonic_ns'],clock_session_id=o['clock_session_id'])
    _mark(s,c,price)
    if not warm and not s['stopped']:
        desired=floor(t*F(s['equity'])/price,c['quantity_step']);delta=desired-v['units']
        if delta:s['pending']=dict(side='BUY' if delta>0 else 'SELL',quantity=text(abs(delta)),intent_utc_ms=o['decision_utc_ms'],intent_monotonic_ns=o['decision_monotonic_ns'])
    return _output(s)


def quote(state,observation):
    s,c,v=_ready(state);o=observation
    need(type(o) is dict and set(o)=={'bid','ask','received_utc_ms','received_monotonic_ns','clock_session_id'},'INPUT_INVALID')
    bid=amount(o['bid']);ask=amount(o['ask']);need(0<bid<=ask,'INPUT_INVALID');_clock(s,o)
    p=s['pending'];need(p is not None,'NO_PENDING_INTENT')
    need(o['received_monotonic_ns']>s['last_action_monotonic_ns'] and o['received_utc_ms']>=s['last_action_utc_ms'],'CLOCK_INVALID')
    s.update(last_receipt_monotonic_ns=o['received_monotonic_ns'],last_action_utc_ms=o['received_utc_ms'],last_action_monotonic_ns=o['received_monotonic_ns'])
    delay=s['config']['max_quote_delay_ms']
    if o['received_utc_ms']-p['intent_utc_ms']>delay or o['received_monotonic_ns']-p['intent_monotonic_ns']>delay*10**6:
        s.update(stopped=True,reason_code='QUOTE_TIMEOUT');return _output(s)
    buy=p['side']=='BUY';mid=(bid+ask)/2
    price=ceil(ask*(1+c['slippage_bps']/10000),c['tick_size']) if buy else floor(bid*(1-c['slippage_bps']/10000),c['tick_size'])
    need(price>0,'NUMERIC_INVALID');qty=F(p['quantity']);step=c['quantity_step']
    def cost(q):return q*price+ceil(q*price*c['fee_bps']/10000,MONEY)
    if buy:
        lo=0;hi=int(qty/step)
        while lo<hi:
            m=(lo+hi+1)//2;q=m*step;cash=v['cash']-cost(q);units=v['units']+q
            if cash>=0 and units*mid<=c['max_target']*(cash+units*mid):lo=m
            else:hi=m-1
        qty=lo*step
    fee=ceil(qty*price*c['fee_bps']/10000,MONEY);notional=qty*price
    if buy:
        cash=v['cash']-notional-fee;units=v['units']+qty;basis=v['cost_basis']+notional;pnl=v['cumulative_realized_pnl']
    else:
        need(qty<=v['units'],'STATE_INVALID');allocated=v['cost_basis'] if qty==v['units'] else floor(v['cost_basis']*qty/v['units'],MONEY)
        cash=v['cash']+notional-fee;units=v['units']-qty;basis=v['cost_basis']-allocated;pnl=v['cumulative_realized_pnl']+notional-allocated
    need(cash>=0,'NUMERIC_INVALID')
    s.update(cash=text(cash),units=text(units),cost_basis=text(basis),cumulative_fees=text(v['cumulative_fees']+fee),cumulative_realized_pnl=text(pnl),pending=None)
    _mark(s,c,mid);return _output(s)


def stop(state,reason):
    s,c,v=_ready(state);need(reason=='OPERATOR_STOP','INPUT_INVALID');s.update(stopped=True,reason_code=reason);return _output(s)
