"""Pinned full-dataset membership for the observed simulated session profile."""
from .validation_plan import require,snapshot
from .registry_inspection import _hash,_hex
from app.paper.session.profiles import is_trading_time


def validate_session_coverage(value,contract,profile):
    m=snapshot(value);d=contract['dataset_identity'];duration=d['timeframe_ms']
    require(type(m) is dict and set(m)=={'schema_version','timeframe_ms','rows','coverage'} and
        m['schema_version']=='wp5.session_dataset.v1' and _hash(m)==d['dataset_hash'] and
        type(m['timeframe_ms']) is int and m['timeframe_ms']==duration,'DATASET_MANIFEST_INVALID')
    c=m['coverage']
    require(type(c) is dict and set(c)=={'schema_version','coverage_mode','calendar_id','calendar_policy_hash','open_times'} and
        c['schema_version']=='wp5.session_coverage.v1' and c['coverage_mode']==d['coverage_mode'] and
        c['calendar_policy_hash']==_hash(dict(market_hours=profile['market_hours'],timeframe_ms=duration)),
        'COVERAGE_BINDING_INVALID')
    require(type(m['rows']) is list and len(m['rows'])==d['rows'] and m['rows'],'DATASET_MANIFEST_INVALID')
    times=[];index={}
    for r in m['rows']:
        require(type(r) is dict and set(r)=={'open_time','row_hash'} and type(r['open_time']) is int and _hex(r['row_hash']),
            'DATASET_MANIFEST_INVALID')
        t=r['open_time']
        require((not times or t>times[-1]) and (t-d['first_open_ms'])%duration==0 and is_trading_time(profile,t),
            'COVERAGE_MEMBERSHIP_INVALID')
        times.append(t);index[t]=r['row_hash']
    require(type(c['open_times']) is list and all(type(t) is int for t in c['open_times']) and c['open_times']==times and
        times[0]==d['first_open_ms'] and times[-1]==d['last_open_ms'] and times[-1]+duration==d['dataset_end_exclusive_ms'],
        'COVERAGE_MEMBERSHIP_INVALID')
    mode=d['coverage_mode']
    if mode in ('CONTINUOUS_24_7','SESSION_CALENDAR'):
        require((times[-1]-times[0])//duration<=1000000,'COVERAGE_TOO_LARGE')
        expected=[t for t in range(times[0],times[-1]+duration,duration) if mode=='CONTINUOUS_24_7' or is_trading_time(profile,t)]
        require(times==expected,'COVERAGE_MEMBERSHIP_INVALID')
    if mode=='SESSION_CALENDAR':
        require(c['calendar_id']==d['calendar_id'] and _hash(c)==d['calendar_hash'],'COVERAGE_BINDING_INVALID')
    elif mode=='SPARSE_DECLARED':
        require(c['calendar_id'] is None and _hash(c)==d['coverage_manifest_hash'],'COVERAGE_BINDING_INVALID')
    else:require(mode=='CONTINUOUS_24_7' and c['calendar_id'] is None,'COVERAGE_BINDING_INVALID')
    return m,index


def verify_session_rows(value,index,start,end):
    rows=snapshot(value);expected=[t for t in index if start<=t<end]
    require(type(rows) is list and len(rows)==len(expected) and rows,'DATASET_CONTENT_MISMATCH')
    for row,t in zip(rows,expected):
        require(type(row) is dict and set(row)=={'open_time','open','high','low','close','volume'} and
            type(row['open_time']) is int and row['open_time']==t and _hash(row)==index[t], 'DATASET_CONTENT_MISMATCH')
    return rows
