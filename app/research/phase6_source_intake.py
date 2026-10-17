"""Single-owner durable raw source intake; supplied clocks are not time authority."""
import copy
import json
import os
import threading
from app.research.phase6_object_store import ObjectWriter, _bound
from app.research.phase6_start_validation import _snapshot
from app.research.phase6_validation import validate, canonical_hash as H, _shape
from app.research.phase6_binance_source import normalize_message, PROVIDER, INSTRUMENT


def need(ok):
    if not ok:raise ValueError('INTAKE_REFUSED')


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')


class SourceIntake:
    def __init__(self,writer,*,plan,reservation):
        q=_snapshot(dict(plan=plan,reservation=reservation))
        p,r=q['plan'],q['reservation']
        v=validate(dict(document=p,plan=None,reservation=None,prior_receipts=[],prior_segment=None,payloads={},clock_probes={}))
        need(v['structural_status']=='VALID' and _shape(r,'p6.reservation.v1') and r['plan_hash']==H(p))
        need(r['sequence']>=1 and (r['previous_reservation_hash']=='0'*64)==(r['sequence']==1)
             and r['reserved_utc_ms']<p['start_ms'])
        need(p['provider']==PROVIDER and p['instrument']==INSTRUMENT)
        need(type(writer) is ObjectWriter and writer.header['run_id']==H(r) and writer.header['owner']==p['owner'])
        self.writer,self.plan,self.reservation=writer,p,r
        self.pid,self.thread=os.getpid(),threading.get_ident()
        self.claim=writer.claim();self.stopped=False;self.busy=False
        self.total=0;self.packet_count=0;self.receipt_count=0
        self.head='0'*64;self.previous_receipt=None;self.last_clock=None

    def receive(self,raw,*,clock):
        need(not self.stopped and self.pid==os.getpid() and self.thread==threading.get_ident())
        if self.busy:
            self.stopped=True
            raise ValueError('INTAKE_REENTRY')
        self.busy=True
        try:
            # Capture before decoding or storage. Live transport must deliver the
            # received frame immediately; this callable alone does not prove UTC.
            sample=_snapshot(clock())
            need(type(sample) is dict and set(sample)=={'utc_ms','monotonic_ns','clock_session_id','probe'})
            probe=sample['probe'];need(_shape(probe,'p6.clock_probe.v1'))
            utc,mono,session=(sample[k] for k in ('utc_ms','monotonic_ns','clock_session_id'))
            need(type(utc) is int and 0<=utc<2**63 and type(mono) is int and 0<=mono<2**63)
            need(session==probe['clock_session_id'] and probe['measured_utc_ms']<=utc
                 and probe['measured_monotonic_ns']<=mono<=probe['valid_until_monotonic_ns']
                 and probe['offset_bound_ms']<=self.plan['max_clock_skew_ms'])
            if self.last_clock is not None:
                need(session==self.last_clock[2] and utc>=self.last_clock[0] and mono>self.last_clock[1])
            need(type(raw) is bytes and 0<len(raw)<=65536)
            # Count each received frame, including byte-identical duplicates and
            # non-final kline frames. Blob deduplication never refunds intake.
            need(self.total+len(raw)<=self.plan['intake_limit_bytes'])
            result=normalize_message(raw)
            need(result['status'] in ('ACCEPTED','IGNORED'))
            receipt=None
            if result['status']=='ACCEPTED':
                receipt=dict(schema_version='p6.receipt.v1',run_id=H(self.reservation),receipt_seq=self.receipt_count+1,
                    **{k:result[k] for k in ('kind','provider_event_ms','raw_payload_hash','normalized_payload_hash')},
                    received_utc_ms=utc,received_monotonic_ns=mono,clock_session_id=session,clock_probe_hash=H(probe),
                    previous_receipt_hash=H(self.previous_receipt) if self.previous_receipt else '0'*64)
                need(self.plan['start_ms']<=utc and result['provider_event_ms']<=utc+self.plan['max_clock_skew_ms'])
                payload=result['payload']
                if result['kind']=='BAR_FINAL':
                    v=validate(dict(document=payload,plan=self.plan,reservation=None,prior_receipts=[],prior_segment=None,payloads={},clock_probes={}))
                    need(v['structural_status']=='VALID' and payload['close_ms']<=utc<=payload['close_ms']+self.plan['max_lateness_ms'])
            def put(raw):
                need(not self.stopped)
                return self.writer.put(raw,claim=self.claim)
            raw_hash=put(raw);probe_hash=put(encoded(probe));normalization_hash=put(encoded(result))
            receipt_hash=put(encoded(receipt)) if receipt is not None else None
            payload_hash=put(encoded(result['payload'])) if receipt is not None else None
            need(raw_hash==result['raw_payload_hash'] and probe_hash==H(probe))
            commit=dict(schema_version='p6.source_intake_commit.v1',run_id=H(self.reservation),plan_hash=H(self.plan),
                packet_seq=self.packet_count+1,raw_bytes=len(raw),cumulative_raw_bytes=self.total+len(raw),
                raw_payload_hash=raw_hash,clock_probe_hash=probe_hash,normalization_hash=normalization_hash,
                receipt_hash=receipt_hash,normalized_payload_hash=payload_hash,received_utc_ms=utc,
                received_monotonic_ns=mono,clock_session_id=session,previous_commit_hash=self.head)
            head=put(encoded(commit));_bound(self.writer.root,self.writer._fd);need(not self.stopped)
            self.head=head;self.total+=len(raw);self.packet_count+=1;self.last_clock=(utc,mono,session)
            if receipt is not None:self.receipt_count+=1;self.previous_receipt=copy.deepcopy(receipt)
            return dict(commit_hash=head,commit=commit,receipt=receipt,payload=result['payload'],probe=probe,
                runtime_accepted=False,source_authenticated=False,clock_authenticated=False)
        except BaseException:
            self.stopped=True
            raise
        finally:self.busy=False

    def close(self):
        need(self.pid==os.getpid() and self.thread==threading.get_ident())
        self.stopped=True;self.writer.close(claim=self.claim)
