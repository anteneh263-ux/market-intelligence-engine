"""VANTA-SMA200-SCREEN-v1: standalone historical compatibility diagnostic.
No app imports, stores, credentials, network calls, H-025 artifacts or promotions.
Prints JSON to stdout only. Not an authoritative Vanta simulator or MIE verdict.
"""
import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def load(path):
    rows = []
    for row in csv.DictReader(Path(path).open()):
        r = {k: float(row[k]) for k in ('timestamp', 'open', 'high', 'low', 'close')}
        if not all(math.isfinite(v) for v in r.values()):
            raise ValueError('Non-finite OHLC/timestamp')
        if not 0 < r['low'] <= min(r['open'], r['close']) <= max(r['open'], r['close']) <= r['high']:
            raise ValueError('Invalid OHLC')
        if rows and r['timestamp'] - rows[-1]['timestamp'] != 86400:
            raise ValueError('Requires contiguous UTC daily bars')
        if r['timestamp'] % 86400:
            raise ValueError('Requires midnight UTC timestamps')
        rows.append(r)
    return rows


def date(ts):
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()


def signals(rows, length=200):
    # Decision at prior close; execution at next open. Current close never enters its own order.
    return [False if i < length else rows[i-1]['close'] >
            sum(r['close'] for r in rows[i-length:i]) / length for i in range(len(rows))]


def screen(rows, start, side_bps=0, carry_annual=0, length=200, exposure=1.):
    if start < length or start >= len(rows):
        raise ValueError('Insufficient warm-up/evaluation data')
    if not 0 < exposure <= 1:
        raise ValueError('Exposure must be in (0, 1]')
    target = signals(rows, length)
    cash, qty, peak = 1., 0., 1.
    last_order = rows[start]['timestamp']
    first = {}
    max_dd, orders = 0., 0
    for i in range(start, len(rows)):
        r = rows[i]
        open_equity = cash + qty * r['open']
        if target[i] != (qty > 0):
            if qty:
                cash += qty * r['open'] * (1 - side_bps / 10000)
                qty = 0.
            else:
                allocation = cash * exposure
                qty = allocation / (r['open'] * (1 + side_bps / 10000))
                cash -= allocation
            last_order = r['timestamp']
            orders += 1
        # Explicit fixed-unit spot proxy; daily carry approximation, not validator accounting.
        carry = qty * r['open'] * carry_annual / 365
        cash -= carry
        low_equity = cash + qty * r['low']
        close_equity = cash + qty * r['close']
        peak = max(peak, close_equity)
        dd = 1 - close_equity / peak
        max_dd = max(max_dd, dd)
        checks = {'intraday_low_proxy_gt_5pct': low_equity < .95 * open_equity,
                  'challenge_eod_gt_5pct': dd > .05,
                  'main_eod_gt_8pct': dd > .08,
                  'inactivity_60_days': r['timestamp'] - last_order >= 60*86400}
        for k, hit in checks.items():
            if hit and k not in first:
                first[k] = date(r['timestamp'])
    return {'start': date(rows[start]['timestamp']), 'end': date(rows[-1]['timestamp']),
            'orders': orders, 'first_breaches': first,
            'unrestricted_return_pct': round((close_equity-1)*100, 4),
            'unrestricted_close_max_drawdown_pct': round(max_dd*100, 4)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv', required=True)
    args = p.parse_args()
    rows = load(args.csv)
    scenarios = {}
    for name, bps, carry in [('zero_cost_lower_bound', 0, 0),
                             ('illustrative_8bps_side_flat_carry', 8, .1095)]:
        starts = [200] + [i for i,r in enumerate(rows) if i > 200 and date(r['timestamp'])[5:] == '01-01']
        scenarios[name] = [screen(rows, i, bps, carry) for i in starts]
    print(json.dumps({'diagnostic_version':'VANTA-SMA200-SCREEN-v1',
          'data_sha256':hashlib.sha256(Path(args.csv).read_bytes()).hexdigest(),
          'bars':len(rows), 'scenarios':scenarios,
          'limitations':['Historical exploratory screening only; no H-025 evidence.',
          'Fixed-unit spot proxy differs from Vanta leveraged position accounting.',
          'Daily lows do not establish intraday path, funding timing or gap execution.',
          'Flat carry is illustrative; live Hyperliquid funding not supplied.',
          'Curves continue after breaches for diagnostics, not surviving-account returns.',
          'No ranking, payouts, collateral losses, challenge pass or profit estimates.']}, indent=2))

if __name__ == '__main__':
    main()
