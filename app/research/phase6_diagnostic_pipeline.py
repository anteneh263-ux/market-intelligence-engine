"""Sequential diagnostic assembly; deployment/approval and clock qualification upstream."""
import os
import sys
import threading
from app.paper.session import diagnostic_strategy_v1 as strategy,shadow_reducer_v1 as reducer
from app.research.phase6_start_attempt import StartBindingAttempt
from app.research.phase6_receipt_client import VerifiedReceiptClient
from app.research.phase6_binance_transport import BinanceClockBoundTransport
from app.research.phase6_clockbound_intake import ClockBoundSourceIntake
from app.research.phase6_source_inventory import SourceInventory
from app.research.phase6_object_store import ObjectWriter,read_verified_object,inspect_objects
from app.research.phase6_pipeline_artifacts import checkpoint_bundle,prior_receipt_proof
from app.research.phase6_genesis_admission import open_genesis_session
from app.research.phase6_source_intake import need,encoded
from app.research.phase6_start_validation import _snapshot
from app.research.phase6_validation import canonical_hash as H,HEX
from app.research.phase6_capacity import V2


def _anchor(package):
    return {k:package[k] for k in ('trust','receipts','head','challenge','now_utc_ms')}


class DiagnosticPipeline:
    """One owned source, receipt client, pre-bound start and finite writer set.

    Does not allocate directories, connect before run(), authorize a pilot, retry
    failures, or turn a closed/incomplete stream into a completed experiment.
    """
    def __init__(self,*,source,client,start_attempt,params,engine_code_hash,segment_writers,result_writer):
        need(type(source) in (BinanceClockBoundTransport,ClockBoundSourceIntake)
             and type(client) is VerifiedReceiptClient and type(start_attempt) is StartBindingAttempt)
        start=start_attempt.export_request();p=start['plan'];run=H(start['reservation'])
        pa=_snapshot(params);ss=strategy.initial(pa)
        need(H(pa)==p['params_hash'] and H(ss)==start['strategy_state_hash']
             and pa['warmup_bars']==p['warmup_bars'])
        need(type(engine_code_hash) is str and HEX.fullmatch(engine_code_hash))
        intake=source._intake._intake if type(source) is BinanceClockBoundTransport else source._intake
        need(H(intake.plan)==H(p) and H(intake.reservation)==run and intake.packet_count==0)
        need(client._run==run and client._clock==source.event and client._trust==start['anchor']['trust'])
        count=(p['end_ms']-p['start_ms'])//p['timeframe_ms']
        need(2<=count<=64 and type(segment_writers) in (list,tuple) and len(segment_writers)==count)
        writers=tuple(segment_writers)
        for w in (*writers,result_writer):
            need(type(w) is ObjectWriter and w.header['owner']==p['owner'] and w.header['run_id']==run
                 and w._claim is None and not w._objects and not w._stopped)
        roots=[w.root for w in (*writers,result_writer)]
        need(len(set(roots))==len(roots))
        inventory=SourceInventory(plan=p,reservation=start['reservation'])
        state=reducer.initial({k:p[k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT})
        self._pid,self._thread=os.getpid(),threading.get_ident()
        self._source,self._client,self._intake=source,client,intake
        self._start,self._plan,self._params,self._engine=start,p,pa,engine_code_hash
        self._writers,self._result_writer=writers,result_writer
        self._inventory=inventory;self._state=state
        self._claim=result_writer.claim()
        self._strategy=ss;self._journal=None;self._manifest=None
        self._checkpoints=[];self._prior=start['genesis'];self._prior_proof=None
        self._source_head=None;self._packets=0;self._done=False;self._failed=False
        self._busy=False;self._used=False;self._finalized=False

    def _owner(self):need(os.getpid()==self._pid and threading.get_ident()==self._thread)

    def accept(self,result):
        self._owner();need(not self._finalized and not self._failed and not self._done)
        if self._busy:
            self._failed=True;raise ValueError('PIPELINE_REENTRY')
        self._busy=True
        try:
            # Bind the callback to the actual source owner's just-acknowledged head.
            need(result['commit_hash']==self._intake.head and result['commit']['packet_seq']==self._intake.packet_count)
            progress=self._inventory.accept(result)
            self._source_head=progress['source_commit_hash'];self._packets=progress['packet_count']
            if progress['new_segment']:
                need(self._state['pending'] is None and not self._state['stopped'])
                bundle=self._inventory.bundle();segment=bundle['segments'][-1];seq=segment['segment_seq']
                need(seq==len(self._checkpoints)+1)
                fresh=self._client.head();event=self._source.event()
                m=dict(schema_version='p6.session_manifest.v1',run_id=H(self._start['reservation']),segment_id=H(segment),plan_hash=H(self._plan),
                    engine_version='p6.shadow.reducer.v1',engine_code_hash=self._engine,strategy_code_hash=self._plan['strategy_code_hash'],params_hash=H(self._params),
                    prior_checkpoint_hash=H(self._prior if seq==1 else self._prior['checkpoint']),
                    prior_anchor_hash=H(fresh['receipts'][-1]),persisted_utc_ms=event['utc_ms'])
                proof=dict(start=self._start,anchor=_anchor(fresh))
                if self._journal is None:
                    self._journal=open_genesis_session(self._writers[seq-1],m,proof,utc_ms=event['utc_ms'],monotonic_ns=event['monotonic_ns'],capacity=V2)
                else:
                    proof['checkpoints']=self._checkpoints
                    self._journal=self._journal.open_successor(self._writers[seq-1],m,proof,clock=self._source.event)
                self._manifest=m;j=self._journal;event=self._source.event()
                j.append('BAR_ACCEPTED',dict(schema_version='p6.payload.bar_accepted.v1',segment_hash=H(segment),receipt_hash=segment['accepted_receipt_hash'],
                    bar_hash=segment['bar_hash'],accepted_prefix_hash=H(bundle['prefix'])),utc_ms=event['utc_ms'],monotonic_ns=event['monotonic_ns'])
                j.bind_intake(bundle);j.bind_prior_state(self._prior,params=self._params,state=self._state,strategy_state=self._strategy)
                if seq>1:j.bind_prior_receipts(self._prior_proof)
                d=j.run_diagnostic(self._plan,self._params,self._strategy,segment_seq=seq,clock=self._source.event)
                decision=j.read_object(j.read_object(d['event_hash'])['payload_hash']);r=result['receipt'];bar=result['payload']
                o=dict(close_ms=bar['close_ms'],close=bar['close'],**{k:r[k] for k in ('received_utc_ms','received_monotonic_ns','clock_session_id')},
                    decision_utc_ms=decision['callback_finished_utc_ms'],decision_monotonic_ns=decision['callback_finished_monotonic_ns'])
                self._state=j.run_bar(self._state,o,clock=self._source.event)['state'];self._strategy=d['state']
                if self._state['pending'] is None:self._seal(len(bundle['receipts']))
            elif result['receipt'] is not None and result['receipt']['kind']=='QUOTE' and self._state['pending'] is not None:
                bundle=self._inventory.bundle();self._journal.bind_quote_receipts(bundle)
                r=result['receipt'];q=result['payload']
                o=dict(bid=q['bid'],ask=q['ask'],**{k:r[k] for k in ('received_utc_ms','received_monotonic_ns','clock_session_id')})
                self._state=self._journal.run_quote(o,quote_receipt_hash=H(r),clock=self._source.event)['state']
                self._seal(len(bundle['receipts']))
            need(not self._failed)
            return self._done
        except BaseException:
            self._failed=True;raise
        finally:self._busy=False

    def _seal(self,receipt_count):
        j=self._journal
        cp=j.prepare_checkpoint(last_receipt_seq=receipt_count,clock=self._source.event)
        bundle=checkpoint_bundle(j,self._manifest,cp)
        remote=self._client.append(subject_kind='CHECKPOINT',subject_hash=cp['checkpoint_hash'])
        checkpoints=self._checkpoints+[bundle]
        j.bind_checkpoint_anchor(dict(start=self._start,checkpoints=checkpoints,anchor=_anchor(remote)),clock=self._source.event)
        self._prior_proof=prior_receipt_proof(j,bundle)
        self._checkpoints=checkpoints;self._prior=bundle
        self._state,self._strategy=bundle['state'],bundle['strategy_state']
        self._done=self._state['stopped'] or len(checkpoints)==len(self._writers)

    def finish(self,transport_result=None,*,error_type=None):
        """Close owned resources and record a bounded local outcome exactly once."""
        self._owner();need(not self._finalized)
        if self._busy:
            self._failed=True;raise ValueError('PIPELINE_REENTRY')
        self._finalized=True
        if error_type is not None:self._failed=True
        if (transport_result is None or transport_result.get('last_commit_hash')!=self._source_head
                or transport_result.get('messages')!=self._packets):self._failed=True
        cleanup_error=None
        resources=[self._journal,self._client,self._source]+[w for w in self._writers if w._claim is None]
        for resource in resources:
            if resource is None:continue
            try:resource.close()
            except BaseException as exc:
                self._failed=True
                if cleanup_error is None:cleanup_error=exc
        audits=[]
        for name,w in (('source',self._intake.writer),('receipt_client',self._client._writer)):
            try:
                inventory=dict(w._objects)
                inspection=inspect_objects(w.root,header=w.header,objects=inventory)
                audits.append(dict(store=name,root=w.root,header_hash=H(w.header),inventory_hash=H(inventory),inspection=inspection))
            except BaseException as exc:
                self._failed=True
                if cleanup_error is None:cleanup_error=exc
        status='INCOMPLETE'
        if not self._failed and self._done:
            status='STOPPED_LOCAL' if self._state['stopped'] else 'COMPLETE_LOCAL'
        result=dict(schema_version='p6.pipeline_result.v1',run_id=H(self._start['reservation']),status=status,
            expected_segments=len(self._writers),anchored_segments=len(self._checkpoints),source_packets=self._packets,
            source_commit_hash=self._source_head,last_checkpoint_hash=H(self._checkpoints[-1]['checkpoint']) if self._checkpoints else None,
            error_type=error_type or (type(cleanup_error).__name__ if cleanup_error is not None else None),
            runtime_accepted=False,independent_retention_verified=False,technical_verification='LOCAL_ONLY',local_store_audits=audits)
        try:
            raw=encoded(result);digest=self._result_writer.put(raw,claim=self._claim)
        finally:
            failed=sys.exc_info()[0] is not None
            try:self._result_writer.close(claim=self._claim)
            except BaseException:
                if not failed:raise
        need(read_verified_object(self._result_writer.root,header=self._result_writer.header,objects={digest:len(raw)},digest=digest)==raw)
        if cleanup_error is not None:raise cleanup_error
        return result

    def run(self,*,max_messages,duration_seconds):
        self._owner();need(type(self._source) is BinanceClockBoundTransport and not self._used and not self._finalized)
        self._used=True
        try:
            transport=self._source.run(max_messages=max_messages,duration_seconds=duration_seconds,on_commit=self.accept)
        except BaseException as exc:
            try:self.finish(error_type=type(exc).__name__)
            except BaseException:pass
            raise
        return self.finish(transport)
