"""Single-owner remote receipt verification; unknown outcomes permanently stop."""
import copy
import json
import os
import secrets
import ssl
import threading
from app.research.phase6_anchor_validation import _snapshot,_hash,_hex,_trust_schema,verify_anchors
from app.research.phase6_receipt_request import validate_append_request
from app.research.phase6_receipt_connection import request_once
from app.research.phase6_receipt_wire import _host
from app.research.phase6_receipt_http import _pairs,_reject_number
from app.research.phase6_source_intake import need,encoded
from app.research.phase6_object_store import ObjectWriter


class VerifiedReceiptClient:
    def __init__(self,writer,*,run_id,owner,trust,expected_trust_hash,clock,port,context,hostname,server_cert_sha256):
        t=_snapshot(trust);_trust_schema(t)
        _host(hostname)
        need(type(port) is int and 1<=port<=65535 and _hex(server_cert_sha256))
        need(type(context) is ssl.SSLContext and context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED)
        need(type(writer) is ObjectWriter and writer.header['owner']==owner and writer.header['run_id']==run_id)
        need(_hex(run_id) and _hash(t)==expected_trust_hash and callable(clock))
        self._pid,self._thread=os.getpid(),threading.get_ident()
        self._writer=writer;self._claim=writer.claim();self._clock=clock
        self._trust,self._trust_hash,self._run=t,expected_trust_hash,run_id
        self._connection=dict(port=port,context=context,hostname=hostname,server_cert_sha256=server_cert_sha256)
        self._receipts=[];self._subjects=[];self._last_clock=None
        self.stopped=False;self._busy=False;self._head='0'*64;self._sequence=0

    def _owner(self):need(self._pid==os.getpid() and self._thread==threading.get_ident())

    def _time(self):
        event=_snapshot(self._clock())
        need(type(event) is dict and set(event)=={'utc_ms','monotonic_ns','clock_session_id'})
        utc,mono,sid=event['utc_ms'],event['monotonic_ns'],event['clock_session_id']
        need(type(utc) is int and 0<=utc<2**63 and type(mono) is int and 0<=mono<2**63 and type(sid) is str and bool(sid))
        if self._last_clock:
            need(sid==self._last_clock[2] and utc>=self._last_clock[0] and mono>self._last_clock[1])
        need(self._trust['not_before_utc_ms']<=utc<=self._trust['not_after_utc_ms'])
        self._last_clock=(utc,mono,sid);return utc

    def _record(self,kind,data):
        need(not self.stopped)
        record=dict(schema_version='p6.receipt_client_observation.v1',run_id=self._run,sequence=self._sequence+1,
            previous_observation_hash=self._head,kind=kind,data=data)
        digest=self._writer.put(encoded(record),claim=self._claim)
        need(not self.stopped);self._sequence+=1;self._head=digest
        return digest

    def _send(self,path,payload):
        self._record('request',dict(path=path,payload=payload))
        response=request_once(**self._connection,path=path,body=encoded(payload).decode('ascii'))
        self._record('response',response)
        need(response['status_code']==200 and response['retry_allowed'] is False)
        return _snapshot(json.loads(response['body'],object_pairs_hook=_pairs,parse_float=_reject_number,parse_constant=_reject_number))

    def _verified_head(self,receipts,subjects,challenge=None):
        if challenge is None:challenge=dict(nonce=secrets.token_hex(32),issued_utc_ms=self._time())
        else:challenge=_snapshot(challenge)
        need(type(challenge) is dict and set(challenge)=={'nonce','issued_utc_ms'} and _hex(challenge['nonce']))
        self._record('challenge',challenge)
        response=self._send('/head',dict(run_id=self._run,nonce=challenge['nonce']))
        need(set(response)=={'schema_version','head','runtime_accepted'} and response['schema_version']=='p6.challenged_head_result.v1')
        package=dict(trust=self._trust,expected_trust_hash=self._trust_hash,expected_run_id=self._run,
            expected_subjects=subjects,receipts=receipts,head=response['head'],challenge=challenge,now_utc_ms=self._time())
        validation=verify_anchors(package)
        self._record('verification',dict(package=package,result=validation))
        need(validation['cryptographic_status']=='VALID' and validation['head_status']=='MATCHED')
        return copy.deepcopy(package)

    def append(self,*,subject_kind,subject_hash):
        self._owner();need(not self.stopped)
        if self._busy:
            self.stopped=True;raise ValueError('RECEIPT_CLIENT_REENTRY')
        self._busy=True
        try:
            q=dict(schema_version='p6.anchor_append_request.v1',run_id=self._run,anchor_seq=len(self._receipts)+1,
                subject_kind=subject_kind,subject_hash=subject_hash,previous_anchor_hash=_hash(self._receipts[-1]) if self._receipts else '0'*64,
                service_identity=self._trust['service_identity'])
            need(validate_append_request(q)['status']=='VALID')
            self._record('pre_append_clock',dict(utc_ms=self._time()))
            response=self._send('/append',q);receipt=response['receipt']
            need(response['schema_version']=='p6.receipt_append_result.v1')
            need(all(receipt[k]==v for k,v in q.items() if k!='schema_version'))
            receipts=self._receipts+[receipt];subjects=self._subjects+[dict(subject_kind=subject_kind,subject_hash=subject_hash)]
            package=self._verified_head(receipts,subjects)
            need(not self.stopped)
            self._receipts=copy.deepcopy(receipts);self._subjects=copy.deepcopy(subjects)
            return package
        except BaseException:self.stopped=True;raise
        finally:self._busy=False

    def head(self,*,challenge=None):
        self._owner();need(not self.stopped and self._receipts)
        if self._busy:
            self.stopped=True;raise ValueError('RECEIPT_CLIENT_REENTRY')
        self._busy=True
        try:return self._verified_head(self._receipts,self._subjects,challenge)
        except BaseException:self.stopped=True;raise
        finally:self._busy=False

    def close(self):
        self._owner();self.stopped=True
        if self._busy:raise ValueError('RECEIPT_CLIENT_REENTRY')
        self._writer.close(claim=self._claim)
