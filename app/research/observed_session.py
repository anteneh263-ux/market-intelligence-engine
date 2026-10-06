"""Durable observations of actual simulated session callbacks, version 1."""
import os
import json
import stat
import re
from pathlib import Path

from .registry_inspection import _hash,_hex,_canonical
from .validation_plan import snapshot,require,finite,read_artifact
from .walkforward import _Store
from app.paper.session.observed_engine_v1 import run_replay
from app.paper.session.manifest import detect_manifest_drift
from app.paper.session.ingest import dataset_hash
from app.paper.session.journal import _verify as verify_journal_events

VERSION='paper-session-observed-v1'


def _summarize(events,terminal):
    available={};features={};decisions={};attempts={};rows=[];rejected=[]
    for e in events:
        kind,t,p=e['kind'],e['timestamp_ms'],e['payload']
        if kind=='BAR_AVAILABLE':
            bar=p['bar_open_ms'];require(bar not in available,'OBSERVATION_DUPLICATE')
            available[bar]=t
        elif kind=='FEATURE_OBSERVED':
            key=p['decision_id'];bar=p['bar_open_ms']
            require(key not in features and bar in available and available[bar]<=t,'OBSERVATION_ORDER_INVALID')
            features[key]=dict(bar=bar,time=t,source=p['source'])
        elif kind=='SIGNAL_DECISION':
            key=p['decision_id'];require(key in features and key not in decisions,'OBSERVATION_ORDER_INVALID')
            f=features[key];require(f['bar']==p['bar_open_ms'] and f['source']==p['source'] and f['time']<=t,'OBSERVATION_ORDER_INVALID')
            decisions[key]=dict(decision_id=key,source=p['source'],bar_open_ms=f['bar'],
                bar_available_ms=available[f['bar']],feature_observed_ms=f['time'],signal_decision_ms=t,
                target=p['target'],status='UNEXECUTED',attempts=0)
        elif kind=='SIGNAL_REJECTED':
            require(p['decision_id'] in features and p['decision_id'] not in decisions,'OBSERVATION_ORDER_INVALID')
            rejected.append(p['decision_id'])
        elif kind=='ORDER_SUBMITTED':
            key=p['decision_id'];aid=p['attempt_id']
            require(key in decisions and aid not in attempts,'OBSERVATION_ORDER_INVALID')
            d=decisions[key]
            require(d['status'] in ('UNEXECUTED','BROKER_ERROR') and d['signal_decision_ms']<=t and p['target']==d['target'],
                'OBSERVATION_ORDER_INVALID')
            attempts[aid]=dict(key=key,time=t,terminal=False);d['attempts']+=1;d['status']='SUBMITTED'
        elif kind in ('ORDER_RESULT','BROKER_ERROR'):
            aid=p['attempt_id'];require(aid in attempts,'OBSERVATION_ORDER_INVALID');a=attempts[aid]
            require(not a['terminal'] and a['key']==p['decision_id'] and a['time']<=t,'OBSERVATION_ORDER_INVALID')
            a['terminal']=True;d=decisions[a['key']]
            if kind=='BROKER_ERROR':d['status']='BROKER_ERROR';continue
            order=p['order'];status=order['status']
            require(status in ('filled','partial','rejected','no_trade') and order['exec_open_time']==t and
                finite(order['filled_units']),'OBSERVATION_RESULT_INVALID')
            d['status']=status.upper()
            if status in ('filled','partial'):
                require(abs(order['filled_units'])>0,'OBSERVATION_RESULT_INVALID')
                row={k:d[k] for k in ('bar_open_ms','bar_available_ms','feature_observed_ms','signal_decision_ms')}
                row.update(order_submitted_ms=a['time'],fill_ms=t);rows.append(row)
            else:require(order['filled_units']==0,'OBSERVATION_RESULT_INVALID')
        elif kind=='SESSION_TERMINAL':
            require(e is events[-1] and p==terminal,'OBSERVATION_ORDER_INVALID')
        else:raise ValueError('OBSERVATION_KIND_INVALID')
    require(events and events[-1]['kind']=='SESSION_TERMINAL' and all(a['terminal'] for a in attempts.values()),'OBSERVATION_INCOMPLETE')
    outstanding=[key for key,d in decisions.items() if d['status'] in ('UNEXECUTED','BROKER_ERROR')]
    pending=terminal['pending_decision_id']
    require(outstanding==([pending] if pending is not None else []),'OBSERVATION_TERMINAL_INVALID')
    for d in decisions.values():
        if d['status'] in ('UNEXECUTED','BROKER_ERROR') and terminal['lifecycle_status']!='COMPLETED':
            d['status']='CANCELLED_'+d['status']
    require(len({r['bar_open_ms'] for r in rows})==len(rows),'OBSERVATION_DUPLICATE')
    rows.sort(key=lambda r:r['bar_open_ms'])
    counts={status:sum(d['status']==status for d in decisions.values()) for status in sorted({d['status'] for d in decisions.values()})}
    return dict(timing_rows=rows,decisions=list(decisions.values()),decision_status_counts=counts,
        accepted_decisions=len(decisions),rejected_signals=rejected,submitted_attempts=len(attempts),
        completed_timing_rows=len(rows),available_bars=len(available))


def run_observed_session(*,manifest,profile,risk_limits,strategy,params,frame,evidence_directory,trial_binding):
    """Fresh-root simulated execution; exceptions preserve incomplete evidence."""
    native=snapshot(manifest);prof=snapshot(profile);risk=snapshot(risk_limits);parameters=snapshot(params)
    binding=snapshot(trial_binding);state=snapshot(vars(strategy))
    require(type(binding) is dict and set(binding)=={'research_program_id','experiment_id','trial_id','contract_hash'} and
        all(_hex(v) for v in binding.values()),'TRIAL_BINDING_INVALID')
    require(native.get('software_version')==VERSION and not detect_manifest_drift(native)[0], 'SESSION_MANIFEST_INVALID')
    require('H025' not in re.sub('[^A-Z0-9]','',str(native['strategy']['strategy_id']).upper()),'H025_EXCLUDED')
    data=frame.copy(deep=True);data_hash=dataset_hash(data)
    require(native.get('dataset_hash')==data_hash,'DATASET_CONTENT_MISMATCH')
    frozen=dict(schema_version='wp5.observed_session_manifest.v1',native_manifest=native,trial_binding=binding,
        strategy_state=state,clock='SIMULATED_MARKET_TIME',observation_version=VERSION)
    store=_Store(evidence_directory);folder=Path(evidence_directory);events=[]
    try:
        raw=store.put('MANIFEST.json',frozen);root_hash=_hash(frozen)
        (folder/'journal').mkdir();os.fsync(store.fd)
        def observe(kind,t,payload):
            store.check('MANIFEST.json',raw)
            require(snapshot(vars(strategy))==state and _hash(frozen)==root_hash and
                parameters==snapshot(params) and prof==snapshot(profile) and risk==snapshot(risk_limits),'FROZEN_RUNTIME_MUTATED')
            require(type(t) is int and (not events or t>=events[-1]['timestamp_ms']),'OBSERVATION_ORDER_INVALID')
            event=dict(schema_version='wp5.session_event.v1',seq=len(events),kind=kind,timestamp_ms=t,payload=snapshot(payload),
                parent_hash=events[-1]['event_hash'] if events else root_hash)
            event['event_hash']=_hash(event);store.put(f"event-{len(events):08}.json",event);events.append(event)
        result=run_replay(manifest=native,profile=prof,risk_limits=risk,strategy=strategy,params=parameters,
            frame=data,journal_dir=str(folder/'journal'),_phase5_observer=observe)
        require(events and events[-1]['kind']=='SESSION_TERMINAL','OBSERVATION_INCOMPLETE')
        store.check('MANIFEST.json',raw)
        require(dataset_hash(data)==data_hash,'FROZEN_RUNTIME_MUTATED')
        for event in events:store.check(f"event-{event['seq']:08}.json",_canonical(event))
        path=folder/'journal'/'journal.jsonl'
        with path.open('rb') as f:os.fsync(f.fileno())
        journal=_read_journal(folder,native['manifest_hash'])
        require(journal and journal[-1]['event_hash']==result['journal_summary']['final_event_hash']==
            events[-1]['payload']['journal_final_hash'],'SESSION_JOURNAL_MISMATCH')
        orders=[e['payload'] for e in journal if e['event_type']=='order']
        observed=[e['payload']['order'] for e in events if e['kind']=='ORDER_RESULT']
        require(orders==observed,'SESSION_JOURNAL_MISMATCH')
        audit=_summarize(events,events[-1]['payload'])
        require(audit['submitted_attempts']==result['counts']['orders']+result['counts']['execution_errors'] and
            audit['completed_timing_rows']==result['counts']['fills']+result['counts']['partials'],'SESSION_JOURNAL_MISMATCH')
        points=[dict(timestamp_ms=e['timestamp'],equity=e['payload']['equity'])
            for e in journal if e['event_type']=='portfolio_snapshot']
        require(points,'SESSION_EQUITY_INVALID')
        terminal_time=journal[-1]['timestamp']
        require(terminal_time>=points[-1]['timestamp_ms'],'SESSION_EQUITY_INVALID')
        if terminal_time==points[-1]['timestamp_ms']:
            points[-1]['equity']=result['final_equity']
        else:points.append(dict(timestamp_ms=terminal_time,equity=result['final_equity']))
        require(all(finite(p['equity']) and p['equity']>0 for p in points),'SESSION_EQUITY_INVALID')
        require(all(a['timestamp_ms']<b['timestamp_ms'] for a,b in zip(points,points[1:])),'SESSION_EQUITY_INVALID')
        # First close is the baseline; never count an artificial initial flat return.
        returns=[b['equity']/a['equity']-1 for a,b in zip(points,points[1:])]
        report=dict(schema_version='wp5.observed_session_report.v1',observation_status='VERIFIED',
            evidence_kind='SIMULATED_REPLAY',prospective_verified=False,manifest_hash=root_hash,
            observation_head=events[-1]['event_hash'],event_count=len(events),journal_head=journal[-1]['event_hash'],
            trial_binding=binding,native_result={k:v for k,v in result.items() if k!='journal_path'},
            audit=audit,net_returns=returns,equity_points=points,return_baseline='FIRST_CLOSE')
        report['report_hash']=_hash(report);store.put('RESULT.json',report);return report
    finally:store.close()


def _read_journal(folder,manifest_hash):
    root=Path(folder)
    require(root.is_absolute() and root.resolve()==root,'EVIDENCE_PATH_INVALID')
    rootfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        childfd=os.open('journal',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=rootfd)
        try:
            fd=os.open('journal.jsonl',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=childfd)
            with os.fdopen(fd,'rb') as f:
                require(stat.S_ISREG(os.fstat(f.fileno()).st_mode),'EVIDENCE_PATH_INVALID')
                events=[json.loads(line) for line in f.read().splitlines()]
        finally:os.close(childfd)
    finally:os.close(rootfd)
    ok,_,_=verify_journal_events(events,_hash(dict(anchor='manifest',manifest_hash=manifest_hash)))
    require(ok,'SESSION_JOURNAL_MISMATCH')
    return events


def verify_observed_session(directory,*,expected_report_hash):
    """Read-only evidence replay; never opens source bars or invokes a strategy."""
    require(_hex(expected_report_hash),'OBSERVATION_HASH_MISMATCH')
    folder=Path(directory)
    require('H025' not in re.sub('[^A-Z0-9]','',str(folder).upper()),'H025_EXCLUDED')
    report=read_artifact(folder,'RESULT.json');manifest=read_artifact(folder,'MANIFEST.json')
    require(_hash({k:v for k,v in report.items() if k!='report_hash'})==report['report_hash']==expected_report_hash and
        _hash(manifest)==report['manifest_hash'] and manifest['trial_binding']==report['trial_binding'], 'OBSERVATION_HASH_MISMATCH')
    count=report['event_count'];require(type(count) is int and 0<count<=1000000,'OBSERVATION_INCOMPLETE')
    require(set(os.listdir(folder))=={'MANIFEST.json','RESULT.json','journal'}|{f'event-{i:08}.json' for i in range(count)},
        'OBSERVATION_INCOMPLETE')
    events=[];parent=report['manifest_hash']
    for i in range(count):
        e=read_artifact(folder,f'event-{i:08}.json')
        require(type(e['seq']) is int and e['seq']==i and e['parent_hash']==parent and
            e['event_hash']==_hash({k:v for k,v in e.items() if k!='event_hash'}) and
            type(e['timestamp_ms']) is int and (not events or e['timestamp_ms']>=events[-1]['timestamp_ms']),
            'OBSERVATION_HASH_MISMATCH')
        events.append(e);parent=e['event_hash']
    require(parent==report['observation_head'],'OBSERVATION_HASH_MISMATCH')
    require(_summarize(events,events[-1]['payload'])==report['audit'],'OBSERVATION_RESULT_INVALID')
    native=manifest['native_manifest'];journal=_read_journal(folder,native['manifest_hash'])
    result=report['native_result']
    require(journal and journal[-1]['event_type']=='session_final' and
        journal[-1]['event_hash']==report['journal_head']==result['journal_summary']['final_event_hash']==
            events[-1]['payload']['journal_final_hash'] and
        journal[-1]['payload']['counts']==result['counts']==events[-1]['payload']['counts'] and
        journal[-1]['payload']['final_equity']==result['final_equity'] and
        journal[-1]['payload']['lifecycle_status']==result['lifecycle_status']==events[-1]['payload']['lifecycle_status'],
        'SESSION_JOURNAL_MISMATCH')
    require([e['payload'] for e in journal if e['event_type']=='order']==
        [e['payload']['order'] for e in events if e['kind']=='ORDER_RESULT'],'SESSION_JOURNAL_MISMATCH')
    points=[dict(timestamp_ms=e['timestamp'],equity=e['payload']['equity']) for e in journal if e['event_type']=='portfolio_snapshot']
    require(points and report['audit']['available_bars']==len(points),'OBSERVATION_INCOMPLETE')
    last=journal[-1];require(last['timestamp']>=points[-1]['timestamp_ms'],'SESSION_EQUITY_INVALID')
    if last['timestamp']==points[-1]['timestamp_ms']:points[-1]['equity']=last['payload']['final_equity']
    else:points.append(dict(timestamp_ms=last['timestamp'],equity=last['payload']['final_equity']))
    require(all(finite(p['equity']) and p['equity']>0 for p in points),'SESSION_EQUITY_INVALID')
    returns=[b['equity']/a['equity']-1 for a,b in zip(points,points[1:])]
    require(points==report['equity_points'] and returns==report['net_returns'] and report['return_baseline']=='FIRST_CLOSE',
        'OBSERVATION_RESULT_INVALID')
    return report
