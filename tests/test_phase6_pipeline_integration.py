"""Actual local admission/execution/receipt calls; synthetic feed, native FFI and AWS.

The test owns explicit temporary roots. It is not a prospective pilot or cloud
retention qualification, and it deliberately makes no complete-run assertion.
"""
import copy
import itertools
import json
import socket
import threading
import pytest
from test_phase6_receipt_mtls import tls
from test_phase6_receipt_service import service
from test_phase6_reservation_ledger import setup,inputs
from test_phase6_binance_source import message,raw
from test_phase6_intake_binding import read
from app.research import phase6_clockbound as clock_module
from app.research.phase6_object_store import ObjectWriter,inspect_objects
from app.research.phase6_clockbound_intake import ClockBoundSourceIntake
from app.research.phase6_source_inventory import SourceInventory
from app.research.phase6_receipt_host import ClockBoundReceiptHost
from app.research.phase6_receipt_client import VerifiedReceiptClient
from app.research.phase6_start_attempt import StartBindingAttempt
from app.research.phase6_genesis_admission import open_genesis_session
from app.research.phase6_checkpoint_anchor_validation import verify_checkpoint_anchors
from app.research.phase6_validation import canonical_hash as H
from app.research.phase6_capacity import V2
from app.research.phase6_pipeline_artifacts import checkpoint_bundle,prior_receipt_proof
from app.paper.session import diagnostic_strategy_v1 as strategy,shadow_reducer_v1 as reducer


def anchor(package):
    return {k:copy.deepcopy(package[k]) for k in ('trust','receipts','head','challenge','now_utc_ms')}


@pytest.mark.parametrize('controlled,fault,requests',[
    (False,None,11),(True,None,11),(True,'disconnect',6),(True,'gap',5),
    (True,'clock',5),(True,'capacity',9),(True,'unknown_receipt',7),
    (True,'bad_signature',7),(True,'tamper_prior',9),(True,'successor_write',9),
    (True,'no_quote',6),(True,'late_quote',8),(True,'message_limit',6),
    (True,'slow_decision',6),(True,'pending_duplicate',6),(True,'constructor_failure',11),(True,'load',35)])
def test_two_segments_raw_frames_to_fills_and_verified_checkpoints(tmp_path,monkeypatch,tls,controlled,fault,requests):
    load_mode=fault=='load';segments=10 if load_mode else 2
    now=[1000];ticks=itertools.count(1)
    class Native:
        def __init__(self,*a):self.closed=False
        def interval(self):return ((now[0]-1)*1000000,(now[0]+1)*1000000,1)
        def close(self):self.closed=True
    monkeypatch.setattr(clock_module,'_Native',Native)
    monkeypatch.setattr(clock_module.time,'CLOCK_BOOTTIME',7,raising=False)
    tick=lambda:now[0]*1000000+next(ticks)*100
    monkeypatch.setattr(clock_module.time,'clock_gettime_ns',lambda _:tick())
    monkeypatch.setattr(clock_module.time,'monotonic_ns',tick)
    digests,tls_factory=tls
    config,db,index,s3=service(builder=lambda **kw:kw)
    for k in ('clock','domain_name','principal_arns'):config.pop(k)
    trust=config['trust'];trust.update(not_after_utc_ms=1000000,max_clock_skew_ms=10)
    config['expected_trust_hash']=H(trust)
    objects={};put=s3.put_object;get=s3.get_object;retention=s3.get_object_retention
    def put_many(**kw):
        result=put(**kw);objects[kw['Key']]=copy.deepcopy(kw);return result
    def get_many(**kw):s3.saved=objects[kw['Key']];return get(**kw)
    def retained_many(**kw):s3.saved=objects[kw['Key']];return retention(**kw)
    s3.put_object=put_many;s3.get_object=get_many;s3.get_object_retention=retained_many
    ledger,registry,evidence=setup(tmp_path)
    p,a=inputs(registry,evidence)
    params=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=2 if load_mode else 0,targets=['0.25','0.75'])
    p.update(provider='binance-public-spot',instrument='BTC:USDT',end_ms=60000+segments*60000,warmup_bars=params['warmup_bars'],intake_limit_bytes=100000000 if load_mode else 10000,max_target='0.75',params_hash=H(params),anchor_trust_hash=H(trust))
    a['plan_hash']=H(p)
    r=ledger.reserve(p,a,reserved_utc_ms=2)['reservation'];ledger.close();run=H(r)
    def writer(name,owner='test'):
        root=tmp_path/name;root.mkdir()
        return ObjectWriter(str(root),owner=owner,run_id=run,max_bytes=64000000 if load_mode else 4000000)
    source_writer=writer('source')
    from app.research import phase6_binance_transport as transport_module
    Source=transport_module.BinanceClockBoundTransport if controlled else ClockBoundSourceIntake
    source=Source(source_writer,plan=p,reservation=r,
        library_path='/synthetic/libclockbound.so',library_sha256='a'*64)
    inventory=SourceInventory(plan=p,reservation=r)
    s=reducer.initial({k:p[k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT});ss=strategy.initial(params)
    genesis=dict(schema_version='p6.genesis.v1',run_id=run,plan_hash=H(p),strategy_state_hash=H(ss),
        **{k:s[k] for k in ('cash','units','cost_basis','cumulative_fees','cumulative_realized_pnl','equity','peak_equity')})
    start_writer=ObjectWriter(str(evidence),owner='test',run_id=run,max_bytes=1000000)
    context=dict(plan=p,authorization=a,reservation=r,genesis=genesis,registry_identity=r['registry_identity'],strategy_state_hash=H(ss),trust=trust)
    attempt=StartBindingAttempt(start_writer,context,clock=lambda:source.event()['utc_ms'])
    errors=[];out=[];ready=threading.Event()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen(1);port=listener.getsockname()[1]
        def worker():
            try:
                host=ClockBoundReceiptHost(writer('service-clock',config['owner']),library_path='/synthetic/libclockbound.so',
                    library_sha256='a'*64,client_runs={digests['client']:[run]},**config)
                ready.set()
                out.append(host.serve(listener,context=tls_factory.server_context,hostname='localhost',max_requests=requests,idle_timeout_seconds=2))
            except BaseException as exc:errors.append(exc);ready.set()
        thread=threading.Thread(target=worker);thread.start();assert ready.wait(5)
        client=VerifiedReceiptClient(writer('client'),run_id=run,owner='test',trust=trust,expected_trust_hash=H(trust),clock=source.event,
            port=port,context=tls_factory.client_context(),hostname='localhost',server_cert_sha256=digests['server'])
        journal=None
        try:
            client.append(subject_kind='RESERVATION',subject_hash=run)
            client.append(subject_kind='GENESIS',subject_hash=H(genesis))
            pre=client.head(challenge=attempt.snapshot()['challenge'])
            assert attempt.finish(pre['receipts'],pre['head'])['binding_status']=='VALID'
            start=attempt.export_request()
            if controlled:
                from app.research.phase6_diagnostic_pipeline import DiagnosticPipeline
                segment_writers=[writer('segment-'+str(i)) for i in range(1,segments+1)];result_writer=writer('result')
                def construct():return DiagnosticPipeline(source=source,client=client,start_attempt=attempt,params=params,engine_code_hash='1'*64,
                    segment_writers=segment_writers,result_writer=result_writer)
                if fault=='constructor_failure':
                    with monkeypatch.context() as patch:
                        def invalid(*a,**kw):raise ValueError('invalid reducer configuration')
                        patch.setattr(reducer,'initial',invalid)
                        with pytest.raises(ValueError,match='invalid reducer'):construct()
                    assert result_writer._claim is None and all(w._claim is None for w in segment_writers)
                    fault=None
                pipeline=construct()
                first=message();second=message();second['E']=180000;second['k'].update(t=120000,T=179999)
                quote1=message('quote');quote2=message('quote');quote2['E']=180020
                nonfinal=message();nonfinal['k']['x']=False
                frames_list=[(120010,first),(120030,quote1),(120035,first),(150000,quote1),(150001,nonfinal),(180010,second),(180030,quote2)]
                if fault=='gap':frames_list[0]=(180010,second)
                if fault=='late_quote':frames_list[1]=(120200,quote1)
                if fault=='pending_duplicate':frames_list.insert(1,(120015,first))
                if load_mode:
                    frames_list=[]
                    for i in range(segments):
                        start_ms=60000+i*60000;close_ms=start_ms+60000
                        for second_index in range(1,60):
                            q=message('quote');q['E']=start_ms+second_index*1000
                            frames_list.append((q['E']+10,q))
                        bar=message();bar['E']=close_ms;bar['k'].update(t=start_ms,T=close_ms-1)
                        q=message('quote');q['E']=close_ms+20
                        frames_list.extend([(close_ms+10,bar),(close_ms+30,q)])
                    fault=None
                frames=iter(frames_list)
                if fault=='capacity':pipeline._writers[1]._limit=1
                if fault=='successor_write':
                    def failed_put(*a,**kw):raise OSError('unknown successor publication')
                    monkeypatch.setattr(pipeline._writers[1],'put',failed_put)
                if fault=='slow_decision':
                    advance=strategy.advance
                    def delayed(*a,**kw):
                        result=advance(*a,**kw);now[0]+=101;return result
                    monkeypatch.setattr(strategy,'advance',delayed)
                if fault in ('unknown_receipt','bad_signature'):
                    from app.research import phase6_receipt_client as client_module
                    request=client_module.request_once
                    def corrupt(**kw):
                        answer=request(**kw)
                        if kw['path']=='/append' and json.loads(kw['body'])['subject_kind']=='CHECKPOINT':
                            if fault=='unknown_receipt':raise TimeoutError('reply lost after actual commit')
                            body=json.loads(answer['body']);body['receipt']['signature']='0'*128;answer['body']=json.dumps(body)
                        return answer
                    monkeypatch.setattr(client_module,'request_once',corrupt)
                class Socket:
                    ack=False;received=0
                    def send(self,value):assert value==transport_module.SUBSCRIBE
                    def recv(self,**kw):
                        if not self.ack:self.ack=True;return b'{"result":null,"id":1}'
                        self.received+=1
                        if fault in ('disconnect','no_quote') and self.received==2:
                            if fault=='disconnect':raise ConnectionError('feed disconnected')
                            raise TimeoutError('no quote')
                        if fault=='clock' and self.received==1:
                            monkeypatch.setattr(source._intake._clock.native,'interval',lambda:(0,0,2))
                        if fault=='tamper_prior' and self.received==6:
                            from pathlib import Path
                            header=Path(pipeline._writers[0].root)/'writer.json';header.chmod(0o600);header.write_text('{}')
                        instant,value=next(frames);now[0]=instant;return raw(value)
                    def close(self):pass
                monkeypatch.setattr(transport_module,'_connect',Socket)
                durations=[]
                if load_mode:
                    import time
                    accept=pipeline.accept
                    def measured(value):
                        before=time.perf_counter()
                        try:return accept(value)
                        finally:durations.append(time.perf_counter()-before)
                    monkeypatch.setattr(pipeline,'accept',measured)
                max_messages=1000 if load_mode else 20
                if fault not in (None,'message_limit','late_quote'):
                    with pytest.raises((ValueError,OSError)):pipeline.run(max_messages=max_messages,duration_seconds=900 if load_mode else 180)
                    from pathlib import Path
                    saved=list(Path(pipeline._result_writer.root).glob('*.blob'));assert len(saved)==1
                    result=json.loads(saved[0].read_text());assert result['status']=='INCOMPLETE'
                    assert source.stopped and client.stopped
                else:
                    result=pipeline.run(max_messages=1 if fault=='message_limit' else max_messages,duration_seconds=900 if load_mode else 180)
                    assert result['status']==('INCOMPLETE' if fault=='message_limit' else 'STOPPED_LOCAL' if fault=='late_quote' else 'COMPLETE_LOCAL')
                assert not result['runtime_accepted'] and not result['independent_retention_verified']
                checkpoints=pipeline._checkpoints;remote=dict(receipts=copy.deepcopy(client._receipts))
                if fault is None:
                    assert result['source_packets']==(610 if load_mode else 7) and result['anchored_segments']==segments
                    assert len(result['local_store_audits'])==2
                    assert checkpoints[-1]['checkpoint']['last_receipt_seq']==(610 if load_mode else 6)
                    if load_mode:
                        from pathlib import Path
                        print('CAPACITY_MEASUREMENT '+json.dumps(dict(segments=segments,frames=len(durations),callback_total_seconds=sum(durations),
                            callback_max_seconds=max(durations),blob_bytes=sum(f.stat().st_size for f in tmp_path.rglob('*.blob')),synthetic_clock=True)))
                elif fault=='late_quote':assert checkpoints[-1]['state']['reason_code']=='QUOTE_TIMEOUT'
                else:assert result['anchored_segments']<2
                with pytest.raises(ValueError):pipeline.run(max_messages=max_messages,duration_seconds=900 if load_mode else 180)
            else:
                checkpoints=[];prior_proof=None;prior=genesis;old_journal=None
                for seq in (1,2):
                    close=60000+seq*60000;now[0]=close+10
                    # Retain a between-checkpoint quote in the complete next prefix.
                    if seq==2:
                        now[0]=150000
                        between=source.receive(raw(message('quote')));inventory.accept(between)
                        now[0]=close+10
                    m=message();m['E']=close;m['k'].update(t=close-60000,T=close-1)
                    frame=source.receive(raw(m));inventory.accept(frame);bundle=inventory.bundle();segment=bundle['segments'][-1]
                    fresh=client.head();event=source.event()
                    manifest=dict(schema_version='p6.session_manifest.v1',run_id=run,segment_id=H(segment),plan_hash=H(p),
                        engine_version='p6.shadow.reducer.v1',engine_code_hash='1'*64,strategy_code_hash=p['strategy_code_hash'],params_hash=H(params),
                        prior_checkpoint_hash=H(genesis if seq==1 else prior['checkpoint']),prior_anchor_hash=H(fresh['receipts'][-1]),persisted_utc_ms=event['utc_ms'])
                    w=writer('segment-'+str(seq))
                    if seq==1:
                        journal=open_genesis_session(w,manifest,dict(start=start,anchor=anchor(fresh)),
                            utc_ms=event['utc_ms'],monotonic_ns=event['monotonic_ns'],capacity=V2)
                    else:
                        old_journal=journal
                        journal=old_journal.open_successor(w,manifest,dict(start=start,checkpoints=checkpoints,anchor=anchor(fresh)),clock=source.event)
                        assert old_journal.snapshot()['lifecycle']=='CLOSED_SUCCESSOR_OPENED_LOCAL'
                    event=source.event()
                    journal.append('BAR_ACCEPTED',dict(schema_version='p6.payload.bar_accepted.v1',segment_hash=H(segment),
                        receipt_hash=H(frame['receipt']),bar_hash=segment['bar_hash'],accepted_prefix_hash=H(bundle['prefix'])),
                        utc_ms=event['utc_ms'],monotonic_ns=event['monotonic_ns'])
                    journal.bind_intake(bundle);journal.bind_prior_state(prior,params=params,state=s,strategy_state=ss)
                    if seq>1:journal.bind_prior_receipts(prior_proof)
                    d=journal.run_diagnostic(p,params,ss,segment_seq=seq,clock=source.event)
                    decision=journal.read_object(journal.read_object(d['event_hash'])['payload_hash']);receipt=frame['receipt']
                    observation=dict(close_ms=frame['payload']['close_ms'],close=frame['payload']['close'],
                        **{k:receipt[k] for k in ('received_utc_ms','received_monotonic_ns','clock_session_id')},
                        decision_utc_ms=decision['callback_finished_utc_ms'],decision_monotonic_ns=decision['callback_finished_monotonic_ns'])
                    bar=journal.run_bar(s,observation,clock=source.event)
                    assert bar['state']['pending'] is not None
                    now[0]=close+30
                    qm=message('quote');qm['E']=close+20
                    quote=source.receive(raw(qm));inventory.accept(quote);bundle=inventory.bundle()
                    journal.bind_quote_receipts(bundle);qr=quote['receipt'];qp=quote['payload']
                    fill=journal.run_quote(dict(bid=qp['bid'],ask=qp['ask'],**{k:qr[k] for k in ('received_utc_ms','received_monotonic_ns','clock_session_id')}),
                        quote_receipt_hash=H(qr),clock=source.event)
                    assert fill['state']['pending'] is None and fill['state']['units']!='0'
                    now[0]=close+40
                    cp=journal.prepare_checkpoint(last_receipt_seq=len(bundle['receipts']),clock=source.event)
                    checkpoint=checkpoint_bundle(journal,manifest,cp)
                    prior_proof=prior_receipt_proof(journal,checkpoint)
                    checkpoints.append(checkpoint)
                    remote=client.append(subject_kind='CHECKPOINT',subject_hash=cp['checkpoint_hash'])
                    proof=dict(start=start,checkpoints=checkpoints,anchor=anchor(remote))
                    assert verify_checkpoint_anchors(proof,capacity=V2)['binding_status']=='VALID'
                    binding=journal.bind_checkpoint_anchor(proof,clock=source.event)
                    assert journal.read_object(binding['validation_hash'])['binding_status']=='VALID'
                    status=journal.snapshot();assert status['lifecycle']=='CHECKPOINT_ANCHOR_BOUND_LOCAL' and not status['runtime_accepted']
                    prior=checkpoint;s=checkpoint['state'];ss=checkpoint['strategy_state']
                assert s['bar_count']==2 and ss['bar_count']==2 and checkpoints[-1]['checkpoint']['last_receipt_seq']==5
                assert checkpoints[-1]['checkpoint']['previous_checkpoint_hash']==H(checkpoints[0]['checkpoint'])
                journal.close();assert inspect_objects(w.root,header=w.header,objects=status['objects'])['structural_status']=='VALID'
        finally:
            if journal is not None:journal.close()
            client.close();source.close();attempt.close();thread.join(timeout=15)
    assert not thread.is_alive() and not errors and len(out)==1
    expected_objects=segments+2 if fault is None else 3 if requests>=7 else 2
    assert index.puts==expected_objects and len(objects)==expected_objects
    if fault is not None:return
    # Every receipt subject is an acknowledged local object, not a fixture signature.
    assert [x['subject_hash'] for x in remote['receipts']]==[run,H(genesis)]+[H(x['checkpoint']) for x in checkpoints]


def test_result_publication_failure_is_not_masked_by_cleanup(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app.research.phase6_diagnostic_pipeline import DiagnosticPipeline
    # Isolate the terminal publication boundary with real, closed audited stores.
    stores=[]
    for name in ('source','client','result'):
        root=tmp_path/name;root.mkdir();stores.append(ObjectWriter(str(root),owner='test',run_id='a'*64,max_bytes=1000000))
    stores[0].close();stores[1].close()
    pipeline=object.__new__(DiagnosticPipeline)
    pipeline._pid=__import__('os').getpid();pipeline._thread=threading.get_ident()
    pipeline._finalized=False;pipeline._busy=False;pipeline._failed=False;pipeline._done=False
    pipeline._source_head=None;pipeline._packets=0;pipeline._journal=None;pipeline._writers=()
    pipeline._source=SimpleNamespace(close=lambda:None)
    pipeline._client=SimpleNamespace(close=lambda:None,_writer=stores[1]);pipeline._intake=SimpleNamespace(writer=stores[0])
    pipeline._state={};pipeline._start={'reservation':{}};pipeline._checkpoints=[]
    pipeline._result_writer=stores[2];pipeline._claim=stores[2].claim()
    put=stores[2].put;close=stores[2].close
    def failed_put(*a,**kw):put(*a,**kw);raise OSError('primary unknown publication')
    def failed_close(**kw):close(**kw);raise RuntimeError('secondary close failure')
    monkeypatch.setattr(stores[2],'put',failed_put);monkeypatch.setattr(stores[2],'close',failed_close)
    with pytest.raises(OSError,match='primary unknown publication'):pipeline.finish()
    assert pipeline._finalized
