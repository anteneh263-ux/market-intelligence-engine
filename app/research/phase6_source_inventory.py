"""Bounded inventory of acknowledged source commits for actual journal inputs."""
import copy
import os
import threading
from app.research.phase6_validation import canonical_hash as H,validate,_shape,HEX,TOKEN
from app.research.phase6_start_validation import _snapshot
from app.research.phase6_intake_validation import verify_intake_prefix
from app.research.phase6_source_intake import need
from app.research.phase6_capacity import V2


class SourceInventory:
    def __init__(self,*,plan,reservation):
        q=_snapshot(dict(plan=plan,reservation=reservation))
        self._plan,self._reservation=q['plan'],q['reservation']
        need(validate(dict(document=self._plan,plan=None,reservation=None,prior_receipts=[],prior_segment=None,payloads={},clock_probes={}))['structural_status']=='VALID')
        r=self._reservation
        need(_shape(r,'p6.reservation.v1') and r['plan_hash']==H(self._plan))
        need(r['sequence']>=1 and (r['previous_reservation_hash']=='0'*64)==(r['sequence']==1)
             and r['reserved_utc_ms']<self._plan['start_ms'])
        self._run=H(self._reservation);self._pid,self._thread=os.getpid(),threading.get_ident()
        self._receipts=[];self._segments=[];self._payloads={};self._probes={};self._bars={}
        self._packets=0;self._total=0;self._head='0'*64;self._last_clock=None;self.stopped=False

    def accept(self,result):
        need(self._pid==os.getpid() and self._thread==threading.get_ident() and not self.stopped)
        try:
            q=_snapshot(result)
            need(type(q) is dict and set(q)=={'commit_hash','commit','receipt','payload','probe',
                'runtime_accepted','source_authenticated','clock_authenticated'})
            need(all(q[k] is False for k in ('runtime_accepted','source_authenticated','clock_authenticated')))
            c=q['commit'];r=q['receipt'];payload=q['payload'];probe=q['probe']
            hashes=('run_id','plan_hash','raw_payload_hash','clock_probe_hash','normalization_hash','previous_commit_hash')
            integers=('packet_seq','raw_bytes','cumulative_raw_bytes','received_utc_ms','received_monotonic_ns')
            need(type(c) is dict and set(c)==set(hashes)|set(integers)|{'schema_version','receipt_hash','normalized_payload_hash','clock_session_id'})
            need(all(type(c[k]) is str and HEX.fullmatch(c[k]) for k in hashes))
            need(all(type(c[k]) is int and 0<=c[k]<2**63 for k in integers))
            need(type(c['clock_session_id']) is str and TOKEN.fullmatch(c['clock_session_id']))
            need(_shape(probe,'p6.clock_probe.v1'))
            utc,mono,session=(c[k] for k in ('received_utc_ms','received_monotonic_ns','clock_session_id'))
            need(session==probe['clock_session_id'] and probe['measured_utc_ms']<=utc
                 and probe['measured_monotonic_ns']<=mono<=probe['valid_until_monotonic_ns']
                 and probe['offset_bound_ms']<=self._plan['max_clock_skew_ms'])
            if self._last_clock is not None:
                need(session==self._last_clock[2] and utc>=self._last_clock[0] and mono>self._last_clock[1])
            need(q['commit_hash']==H(c) and c['run_id']==self._run and c['plan_hash']==H(self._plan))
            need(c['schema_version']=='p6.source_intake_commit.v1' and c['packet_seq']==self._packets+1 and self._packets<10000)
            need(c['previous_commit_hash']==self._head and type(c['raw_bytes']) is int and 0<c['raw_bytes']<=65536)
            need(c['cumulative_raw_bytes']==self._total+c['raw_bytes']<=self._plan['intake_limit_bytes'])
            need(c['clock_probe_hash']==H(probe))
            new_segment=False
            if r is None:
                need(c['receipt_hash'] is None and c['normalized_payload_hash'] is None and payload is None)
            else:
                need(_shape(r,'p6.receipt.v1'))
                need(len(self._receipts)<4096 and c['receipt_hash']==H(r) and c['normalized_payload_hash']==H(payload))
                need(r['receipt_seq']==len(self._receipts)+1 and r['run_id']==self._run)
                need(r['previous_receipt_hash']==(H(self._receipts[-1]) if self._receipts else '0'*64))
                need(r['normalized_payload_hash']==H(payload) and r['clock_probe_hash']==H(probe)
                     and r['raw_payload_hash']==c['raw_payload_hash'])
                need(all(r[k]==c[k] for k in ('received_utc_ms','received_monotonic_ns','clock_session_id')))
                self._receipts.append(r);self._payloads[H(payload)]=payload;self._probes[H(probe)]=probe
                if r['kind']=='BAR_FINAL':
                    start=payload['open_ms'];bar_hash=H(payload)
                    if start in self._bars:need(self._bars[start]==bar_hash)
                    else:
                        need(start==self._plan['start_ms']+len(self._segments)*self._plan['timeframe_ms'])
                        segment=dict(schema_version='p6.segment.v1',run_id=self._run,segment_seq=len(self._segments)+1,
                            start_ms=start,end_ms=payload['close_ms'],bar_hash=bar_hash,accepted_receipt_hash=H(r),
                            previous_segment_hash=H(self._segments[-1]) if self._segments else '0'*64)
                        self._segments.append(segment);self._bars[start]=bar_hash;new_segment=True
                if not self._segments:
                    need(validate(dict(document=r,plan=self._plan,reservation=self._reservation,prior_receipts=self._receipts[:-1],
                        prior_segment=None,payloads=self._payloads,clock_probes=self._probes))['structural_status']=='VALID')
                if self._segments:
                    need(verify_intake_prefix(self._bundle(),capacity=V2)['binding_status']=='VALID')
            self._packets+=1;self._total=c['cumulative_raw_bytes'];self._head=q['commit_hash'];self._last_clock=(utc,mono,session)
            return dict(new_segment=new_segment,receipt_count=len(self._receipts),segment_count=len(self._segments),
                        packet_count=self._packets,source_commit_hash=self._head,runtime_accepted=False)
        except BaseException:self.stopped=True;raise

    def _bundle(self):
        return dict(plan=self._plan,reservation=self._reservation,receipts=self._receipts,segments=self._segments,
            payloads=self._payloads,clock_probes=self._probes,prefix=dict(schema_version='p6.accepted_prefix.v1',
            run_id=self._run,segment_hashes=[H(s) for s in self._segments]))

    def bundle(self):
        need(self._pid==os.getpid() and self._thread==threading.get_ident() and not self.stopped and self._segments)
        return copy.deepcopy(self._bundle())
