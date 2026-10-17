"""One-run, one-thread receipt host with native clock evidence; no deployment."""
import os
import sys
import threading
from app.research.phase6_clockbound import ClockBoundClock
from app.research.phase6_object_store import ObjectWriter
from app.research.phase6_source_intake import encoded,need
from app.research.phase6_anchor_validation import _snapshot,_hash
from app.research.phase6_receipt_service import build_receipt_operations
from app.research.phase6_receipt_mtls import ReceiptMtlsBoundary
from app.research.phase6_receipt_connection import accept_once


class ClockBoundReceiptHost:
    def __init__(self,writer,*,library_path,library_sha256,client_runs,**config):
        keys={'dynamodb','s3','table_arn','index_table_arn','bucket','owner','trust','expected_trust_hash','signing_key'}
        need(set(config)==keys and type(writer) is ObjectWriter)
        trust=_snapshot(config['trust']);run=writer.header['run_id']
        need(writer.header['owner']==config['owner'] and _hash(trust)==config['expected_trust_hash'])
        need(type(trust['max_clock_skew_ms']) is int and 1<=trust['max_clock_skew_ms']<=100)
        need(type(client_runs) is dict and client_runs and all(type(v) in (list,tuple) and set(v)=={run} for v in client_runs.values()))
        self._pid,self._thread=os.getpid(),threading.get_ident()
        self._writer=writer;self._claim=None;self._clock=None;self._busy=False
        self.stopped=False;self._head='0'*64;self._seq=0
        try:
            self._clock=ClockBoundClock(library_path=library_path,library_sha256=library_sha256,max_offset_ms=trust['max_clock_skew_ms'])
            operations=build_receipt_operations(**dict(config,trust=trust),clock=self._utc)
            self._boundary=ReceiptMtlsBoundary(client_runs=client_runs,**operations)
            self._claim=writer.claim()
        except BaseException:
            if self._clock:
                try:self._clock.close()
                except BaseException:pass
            raise

    def _owner(self):need(os.getpid()==self._pid and threading.get_ident()==self._thread)

    def _utc(self):
        self._owner();need(not self.stopped)
        try:
            sample=self._clock.sample()
            observation=dict(schema_version='p6.service_clock_observation.v1',run_id=self._writer.header['run_id'],
                sequence=self._seq+1,previous_observation_hash=self._head,sample=sample)
            digest=self._writer.put(encoded(observation),claim=self._claim)
            need(not self.stopped);self._head=digest;self._seq+=1
            return sample['utc_ms']
        except BaseException:
            self.stopped=True;self._clock.stopped=True;raise

    def serve(self,listener,*,context,hostname,max_requests,idle_timeout_seconds=120):
        self._owner();need(not self.stopped)
        if self._busy:
            self.stopped=True;raise ValueError('RECEIPT_HOST_REENTRY')
        need(type(max_requests) is int and 1<=max_requests<=10000)
        need(type(idle_timeout_seconds) is int and 1<=idle_timeout_seconds<=120)
        self._busy=True
        try:
            for _ in range(max_requests):
                need(not self.stopped)
                accept_once(listener,context=context,boundary=self._boundary,hostname=hostname,idle_timeout_seconds=idle_timeout_seconds)
                need(not self.stopped)
            return dict(clock_observations=self._seq,clock_head=self._head,runtime_accepted=False)
        except BaseException:
            self.stopped=True;raise
        finally:
            self._busy=False
            failed=sys.exc_info()[0] is not None
            try:self.close()
            except BaseException:
                if not failed:raise

    def close(self):
        self._owner();self.stopped=True
        if self._busy:raise ValueError('RECEIPT_HOST_REENTRY')
        error=None
        for resource,kwargs in ((self._clock,{}),(self._writer,dict(claim=self._claim))):
            try:resource.close(**kwargs)
            except BaseException as exc:
                if error is None:error=exc
        if error is not None:raise error
