"""Read-only quality checks for public HL hourly candle and funding snapshots.
No MIE app imports or evidence stores. Paths must be supplied explicitly.
"""
import argparse, json, hashlib, math, time
from pathlib import Path
from datetime import datetime, timezone


def iso(ms):
    return datetime.fromtimestamp(ms/1000, timezone.utc).isoformat()


def check(candles_path, funding_path, asof):
    c = json.loads(Path(candles_path).read_text())
    f = json.loads(Path(funding_path).read_text())
    closed = [x for x in c if x['T'] < asof]
    issues = []
    for name, records, key in [('candles',closed,'t'),('funding',f,'time')]:
        ts = [int(x[key]) for x in records]
        if not ts: raise ValueError('Empty '+name)
        if len(ts) != len(set(ts)): issues.append(name+': duplicate timestamps')
        if ts != sorted(ts): issues.append(name+': unsorted timestamps')
    ct = {x['t'] for x in closed}
    gaps = [t for t in range(min(ct),max(ct)+1,3600000) if t not in ct]
    ft = {int(x['time'])//3600000*3600000 for x in f}
    missing_funding = sorted(ct-ft)
    bad = 0
    for x in closed:
        o,h,l,cl,v = [float(x[k]) for k in ('o','h','l','c','v')]
        if (not all(math.isfinite(z) for z in (o,h,l,cl,v)) or
            not 0<l<=min(o,cl)<=max(o,cl)<=h or v<0 or
            x.get('s')!='BTC' or x.get('i')!='1h' or x['t']%3600000): bad += 1
    for x in f:
        if x.get('coin')!='BTC' or not math.isfinite(float(x['fundingRate'])):
            issues.append('Invalid funding record')
    return {'asof_utc':iso(asof),'raw_candles':len(c),'closed_candles':len(closed),
        'excluded_unclosed_candles':len(c)-len(closed),
        'first_closed_utc':iso(min(ct)),'last_closed_open_utc':iso(max(ct)),
        'hourly_gaps':len(gaps),'invalid_candles':bad,'funding_records':len(f),
        'closed_hours_without_funding':len(missing_funding),'issues':issues,
        'sha256':{str(p):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (candles_path,funding_path)},
        'limits':['Funding assigned to UTC hour only; exact settlement alignment not verified.',
                  'Public candle snapshot, not verified validator reference prices.',
                  'No strategy or stop simulation executed.']}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candles',required=True);p.add_argument('--funding',required=True)
    p.add_argument('--asof-ms',type=int,required=True)
    a=p.parse_args();print(json.dumps(check(a.candles,a.funding,a.asof_ms),indent=2))
