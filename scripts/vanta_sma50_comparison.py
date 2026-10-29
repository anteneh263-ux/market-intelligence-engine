"""Exploratory predeclared SMA50 vs SMA200 at 10% entry allocation.
Not an independent holdout: all bundled periods were previously inspected.
No tuning, stops, runtime stores, app imports, H-025 changes or network calls.
"""
import argparse
import hashlib
import json
from pathlib import Path
from vanta_sma200_screen import load, date, screen


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv', required=True)
    args = p.parse_args()
    path = Path(args.csv)
    rows = load(path)
    starts = [200] + [i for i,r in enumerate(rows) if i > 200 and date(r['timestamp'])[5:] == '01-01']
    scenarios = {}
    for length in (200, 50):
        for name,bps,carry in [('zero_cost',0,0),('illustrative_cost',8,.1095)]:
            scenarios[f'SMA{length}_{name}'] = [screen(rows,i,bps,carry,length=length,exposure=.1) for i in starts]
    print(json.dumps({'version':'VANTA-SMA50-COMPARE-v1','exposure_at_entry':.1,
         'data_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
         'scenarios':scenarios,'limitations':[
         'All suffixes overlap; no independence or unseen holdout.',
         'Cash/spot fixed units; not Vanta leverage/funding accounting.',
         'Entry exposure can drift; 10 percent is not a hard ongoing cap.',
         'No stop loss. No ranking, challenge completion or income estimate.',
         'Curves continue after breaches, not surviving-account returns.',
         'Daily prices cannot validate intraday stops or validator equivalence.']},indent=2))

if __name__ == '__main__':
    main()
