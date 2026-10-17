"""Accelerated synthetic load; no wall-clock, provider, admission or AWS acceptance."""
import copy,json,time
from test_phase6_validation import plan
from test_phase6_event_validation import case
from test_phase6_bar_call import clock
from app.research.phase6_validation import canonical_hash as H
from app.research.phase6_capacity import V2
from app.research.phase6_event_journal import EventJournal
from app.research.phase6_object_store import ObjectWriter
from app.paper.session import diagnostic_strategy_v1 as strategy,shadow_reducer_v1 as reducer


def test_thirty_segment_synthetic_load(tmp_path):
    pa=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=2,targets=['0.25','0.75'])
    p=plan();p.update(end_ms=1860000,warmup_bars=2,max_target='0.75',params_hash=H(pa),intake_limit_bytes=100*1024*1024,max_decision_delay_ms=5000,max_quote_delay_ms=5000)
    r=dict(schema_version='p6.reservation.v1',plan_hash=H(p),authorization_hash='a'*64,registry_identity='b'*64,sequence=1,reserved_utc_ms=1,previous_reservation_hash='0'*64)
    run=H(r);q=dict(plan=p,reservation=r,receipts=[],segments=[],payloads={},clock_probes={},prefix=dict(schema_version='p6.accepted_prefix.v1',run_id=run,segment_hashes=[]))
    s=reducer.initial({k:p[k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT});ss=strategy.initial(pa)
    prior=dict(schema_version='p6.genesis.v1',run_id=run,plan_hash=H(p),strategy_state_hash=H(ss),**{k:s[k] for k in ('cash','units','cost_basis','cumulative_fees','cumulative_realized_pnl','equity','peak_equity')})
    previous_proof=None;last_quote=None;latencies=[];started=time.perf_counter()
    def receive(payload,kind,t):
        probe=dict(schema_version='p6.clock_probe.v1',clock_session_id='test',measured_utc_ms=t,measured_monotonic_ns=t*1000000,valid_until_monotonic_ns=t*1000000,offset_bound_ms=1,source_identity='synthetic')
        e=dict(schema_version='p6.receipt.v1',run_id=run,receipt_seq=len(q['receipts'])+1,kind=kind,provider_event_ms=t,received_utc_ms=t,received_monotonic_ns=t*1000000,clock_session_id='test',clock_probe_hash=H(probe),raw_payload_hash=H(dict(synthetic_payload=payload,t=t)),normalized_payload_hash=H(payload),previous_receipt_hash=H(q['receipts'][-1]) if q['receipts'] else '0'*64)
        q['receipts'].append(e);q['payloads'][H(payload)]=payload;q['clock_probes'][H(probe)]=probe
        return e
    def quote(t):
        nonlocal last_quote
        if last_quote==t:return
        receive(dict(schema_version='p6.quote.v1',provider=p['provider'],instrument=p['instrument'],bid='100',ask='100'),'QUOTE',t);last_quote=t
    for seq in range(1,31):
        opening=60000+(seq-1)*60000;close=opening+60000
        for sec in range(60):quote(opening+sec*1000+20)
        bar=dict(schema_version='p6.bar.v1',provider=p['provider'],instrument=p['instrument'],open_ms=opening,close_ms=close,open='100',high='100',low='100',close='100',volume='1')
        e=receive(bar,'BAR_FINAL',close+10)
        segment=dict(schema_version='p6.segment.v1',run_id=run,segment_seq=seq,start_ms=opening,end_ms=close,bar_hash=H(bar),accepted_receipt_hash=H(e),previous_segment_hash=H(q['segments'][-1]) if q['segments'] else '0'*64)
        q['segments'].append(segment);q['prefix']['segment_hashes'].append(H(segment))
        m=case()['manifest'];m.update(run_id=run,plan_hash=H(p),params_hash=H(pa),strategy_code_hash=p['strategy_code_hash'],segment_id=H(segment),prior_checkpoint_hash=H(prior if seq==1 else prior['checkpoint']),persisted_utc_ms=close+11)
        root=tmp_path/str(seq);root.mkdir();w=ObjectWriter(str(root),owner='test',run_id=run,max_bytes=64*1024*1024)
        def read(h):return json.loads((root/(h+'.blob')).read_bytes())
        tick=time.perf_counter()
        j=EventJournal(w,m,utc_ms=close+11,monotonic_ns=(close+11)*1000000,capacity=V2)
        j.append('BAR_ACCEPTED',dict(schema_version='p6.payload.bar_accepted.v1',segment_hash=H(segment),receipt_hash=H(e),bar_hash=H(bar),accepted_prefix_hash=H(q['prefix'])),utc_ms=close+12,monotonic_ns=(close+12)*1000000)
        ib=j.bind_intake(q);j.bind_prior_state(prior,params=pa,state=s,strategy_state=ss)
        if previous_proof:j.bind_prior_receipts(previous_proof)
        d=j.run_diagnostic(p,pa,ss,segment_seq=seq,clock=clock(close+13))
        out=j.run_bar(s,dict(close_ms=close,close='100',received_utc_ms=close+10,received_monotonic_ns=(close+10)*1000000,clock_session_id='test',decision_utc_ms=close+14,decision_monotonic_ns=(close+14)*1000000),clock=clock(close+16))
        qb=None
        if out['state']['pending'] is not None:
            quote(close+20);qb=j.bind_quote_receipts(q)
            out=j.run_quote(dict(bid='100',ask='100',received_utc_ms=close+20,received_monotonic_ns=(close+20)*1000000,clock_session_id='test'),quote_receipt_hash=H(q['receipts'][-1]),clock=clock(close+21))
        previous=j.snapshot()['last_event'];cp=j.prepare_checkpoint(last_receipt_seq=len(q['receipts']),clock=clock(close+25))
        c=read(cp['commit_hash']);event=read(cp['event_hash'])
        prior=dict(manifest=m,checkpoint=read(cp['checkpoint_hash']),checkpoint_commit=c,event=event,payload=read(event['payload_hash']),previous_event=previous,state=read(c['state_hash']),strategy_state=read(c['strategy_state_hash']),execution_commit=read(c['execution_commit_hash']),diagnostic_commit=read(c['diagnostic_commit_hash']))
        invocation=read(prior['diagnostic_commit']['invocation_hash']);binding=read(ib['commit_hash'])
        quote_binding=read(qb['commit_hash']) if qb else None
        previous_proof=dict(diagnostic_invocation=invocation,intake_binding=binding,intake_bundle=read(ib['bundle_hash']),intake_validation=read(binding['validation_hash']),execution_invocation=read(prior['execution_commit']['invocation_hash']),quote_binding=quote_binding,quote_bundle=read(qb['bundle_hash']) if qb else None,quote_validation=read(quote_binding['validation_hash']) if qb else None)
        s,ss=prior['state'],prior['strategy_state'];j.close();latencies.append(time.perf_counter()-tick)
        assert not s['stopped'] and prior['checkpoint']['segment_seq']==seq
    report=dict(schema_version='p6.synthetic_capacity_measurement.v1',bars=30,receipts=len(q['receipts']),elapsed_seconds=time.perf_counter()-started,max_segment_seconds=max(latencies),disk_bytes=sum(f.stat().st_size for f in tmp_path.rglob('*.blob')),runtime_accepted=False,prospective=False,aws_tested=False)
    print(json.dumps(report,sort_keys=True))
    assert len(q['receipts'])>=1830
