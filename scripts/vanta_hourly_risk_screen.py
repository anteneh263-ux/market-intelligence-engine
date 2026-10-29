"""VANTA-HOURLY-RISK-v1: isolated, non-authoritative historical diagnostic.
Read supplied snapshots only, print JSON only. No MIE runtime/evidence imports.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
from datetime import datetime, timezone

HOUR = 3600000
DAY = 24 * HOUR


def iso(t):
    return datetime.fromtimestamp(t/1000, timezone.utc).isoformat()


def daily_targets(bars, length=50):
    days = {}
    for b in bars:
        days.setdefault(b['t']//DAY, []).append(b)
    complete = {d: x[-1]['c'] for d,x in days.items()
                if len(x)==24 and x[0]['t']==d*DAY and x[-1]['t']==d*DAY+23*HOUR}
    targets = {}
    for d in sorted(days):
        history = [complete.get(k) for k in range(d-length,d)]
        if all(x is not None for x in history):
            targets[d] = history[-1] > sum(history)/length
    return targets


def validate(raw, rates, asof):
    bars = []
    for b in raw:
        if b['T'] >= asof: continue
        x = {'t':int(b['t']), **{k:float(b[k]) for k in ('o','h','l','c')}}
        if not all(math.isfinite(x[k]) for k in ('o','h','l','c')):
            raise ValueError('Non-finite price')
        if not 0 < x['l'] <= min(x['o'],x['c']) <= max(x['o'],x['c']) <= x['h']:
            raise ValueError('Invalid OHLC')
        if x['t']%HOUR: raise ValueError('Not aligned to UTC hour')
        if bars and x['t']-bars[-1]['t'] != HOUR: raise ValueError('Duplicate, unsorted or missing candle')
        bars.append(x)
    funding = {}
    for r in rates:
        t = int(r['time'])//HOUR*HOUR
        if t in funding: raise ValueError('Duplicate funding hour')
        funding[t] = float(r['fundingRate'])
        if not math.isfinite(funding[t]): raise ValueError('Non-finite funding')
    if not bars or any(b['t'] not in funding for b in bars):
        raise ValueError('Empty bars or missing funding hour')
    return bars, funding


def simulate(bars, funding, length=50, exposure=.1, stop=.02, side_bps=8, use_funding=True):
    targets = daily_targets(bars,length)
    start = next((i for i,b in enumerate(bars) if b['t']%DAY==0 and b['t']//DAY in targets),None)
    if start is None: raise ValueError('No warmed-up full day')
    cash, qty, peak, eod_peak = 1., 0., 1., 1.
    day_open = 1.
    pending_stop, locked, seen_flat = False, False, False
    last_order = bars[start]['t']
    orders, stops, triggers = [], [], []
    first = {}
    max_dd, max_eod_dd, max_intraday = 0., 0., 0.
    fees_total, funding_total = 0., 0.
    fee_rate = side_bps/10000

    def trade(target_qty, price, t, reason):
        nonlocal cash, qty, fees_total, last_order
        delta = target_qty-qty
        if abs(delta)*price < 1e-12: return
        fee=abs(delta)*price*fee_rate
        cash -= delta*price+fee
        qty=target_qty
        fees_total+=fee
        last_order=t
        orders.append({'time':iso(t),'reason':reason,'quantity_change':delta})

    for b in bars[start:]:
        t=b['t']; day=t//DAY
        # Funding at bucket start, on quantity carried into the hour; open-price notional.
        # Approximation: exact exchange/validator settlement and marks are not replicated.
        charge = qty*b['o']*funding[t] if use_funding else 0.
        cash-=charge; funding_total+=charge
        if t%DAY==0:
            day_open=cash+qty*b['o']
        if pending_stop:
            trade(0.,b['o'],t,'stop_next_open')
            stops.append(iso(t));pending_stop=False;locked=True;seen_flat=False
        if t%DAY==0 and day in targets:
            target=targets[day]
            if locked:
                if not target: seen_flat=True
                elif seen_flat: locked=False
            desired=exposure if target and not locked else 0.
            equity=cash+qty*b['o']
            # Buy/sell equation targets post-fee equity fraction; only at UTC midnight.
            if desired==0: new_qty=0.
            else:
                before=qty*b['o'];desired_value=desired*equity
                if desired_value >= before:
                    value=desired*(equity+fee_rate*before)/(1+desired*fee_rate)
                else:
                    value=desired*(equity-fee_rate*before)/(1-desired*fee_rate)
                new_qty=value/b['o']
            trade(new_qty,b['o'],t,'daily_target')
        equity=cash+qty*b['c']
        low_equity=cash+qty*b['l']
        if equity<=0: raise ValueError('Nonpositive equity')
        peak=max(peak,equity)
        dd=1-equity/peak;max_dd=max(max_dd,dd)
        intraday=max(0.,1-low_equity/day_open);max_intraday=max(max_intraday,intraday)
        if intraday>.05: first.setdefault('intraday_low_proxy_gt_5pct',iso(t))
        if t%DAY==23*HOUR:
            eod_peak=max(eod_peak,equity)
            eod_dd=1-equity/eod_peak;max_eod_dd=max(max_eod_dd,eod_dd)
            if eod_dd>.05:first.setdefault('challenge_eod_gt_5pct',iso(t))
            if eod_dd>.08:first.setdefault('main_eod_gt_8pct',iso(t))
        if t-last_order>=60*DAY:first.setdefault('inactivity_60_days',iso(t))
        # Stop based solely on hourly closing equity, global closing high-water incl initial capital.
        # No fictitious execution at the trigger level or at the already observed candle low.
        if stop is not None and qty>0 and dd>=stop and not pending_stop:
            pending_stop=True;triggers.append(iso(t))
    return {'start_utc':iso(bars[start]['t']),'last_hour_open_utc':iso(bars[-1]['t']),
        'hours':len(bars)-start,'return_pct':round((equity-1)*100,6),
        'max_hour_close_drawdown_pct':round(max_dd*100,6),
        'max_eod_drawdown_pct':round(max_eod_dd*100,6),
        'max_intraday_low_proxy_pct':round(max_intraday*100,6),
        'fees_pct_initial_equity':round(fees_total*100,6),
        'net_funding_debit_pct_initial_equity':round(funding_total*100,6),
        'first_breaches':first,'orders_count':len(orders),'stop_exits':stops,
        'stop_trigger_hours':triggers,'unexecuted_stop_at_end':pending_stop,
        'ending_open_position':qty>0,'orders':orders}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candles',required=True);p.add_argument('--funding',required=True)
    p.add_argument('--asof-ms',required=True,type=int)
    a=p.parse_args()
    bars,funding=validate(json.loads(Path(a.candles).read_text()),json.loads(Path(a.funding).read_text()),a.asof_ms)
    results={}
    for name,stop,bps,uf in [('stop_zero_cost',.02,0,False),('stop_cost_funding',.02,8,True),('no_stop_cost_funding',None,8,True)]:
        results[name]=simulate(bars,funding,stop=stop,side_bps=bps,use_funding=uf)
    print(json.dumps({'version':'VANTA-HOURLY-RISK-v1','asof_ms':a.asof_ms,
        'hashes':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (a.candles,a.funding)},
        'results':results,'limitations':['Historical exploratory diagnostic, not H-025 evidence or Vanta qualification.',
        'Spot cash/units proxy; validator accounting not replicated.',
        '10% post-fee target only at daily rebalance; exposure drifts intraday.',
        '2% global close-equity stop, detected hourly and executed next open; gaps can exceed target.',
        'Reentry requires a daily flat state followed by long after stop; high-water is never reset.',
        'Funding bucket start at open-price notional is approximate.',
        '3bps spread plus assumed 5bps slippage per traded notional; not actual fills.',
        'Continue after platform breaches for diagnostics; no surviving-account profit claim.',
        'No ranking, challenge deadline/completion, payout, collateral or prospective verification.']},indent=2))

if __name__=='__main__':main()
