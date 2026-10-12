"""Single-lifetime diagnostic reservation ledger, never runtime admission."""
import copy
import fcntl
import hashlib
import json
import os
import re
import threading
from app.research.phase6_object_store import _root, _bound, _publish, _read, _snapshot as _stat
from app.research.phase6_validation import validate, canonical_hash


def _need(ok, reason):
    if not ok:
        raise ValueError(reason)


def _copy(value, depth=0, budget=None):
    if budget is None: budget=[10000]
    budget[0]-=1
    _need(depth<=6 and budget[0]>=0, 'INPUT_INVALID')
    if value is None or type(value) in (bool,int): return value
    if type(value) is str:
        _need(len(value)<=4096 and value.isascii(), 'INPUT_INVALID');return value
    if type(value) is list:
        _need(len(value)<=1000, 'INPUT_INVALID')
        return [_copy(value[i],depth+1,budget) for i in range(len(value))]
    _need(type(value) is dict and len(value)<=4000, 'INPUT_INVALID')
    result={}
    for key,item in value.items():
        _need(type(key) is str and len(key)<=128 and key.isascii(),'INPUT_INVALID')
        result[key]=_copy(item,depth+1,budget)
    return result


def _canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def _hex(x):return type(x) is str and re.fullmatch('[0-9a-f]{64}',x) is not None
def _int(x):return type(x) is int and 0<=x<2**63
def _token(x):return type(x) is str and re.fullmatch('[A-Za-z0-9_.:-]{1,128}',x) is not None


def _header(h):
    _need(type(h) is dict and set(h)=={'schema_version','owner','registry_identity','max_bytes'},'INPUT_INVALID')
    _need(h['schema_version']=='p6.diagnostic_registry.v1' and _token(h['owner']) and _hex(h['registry_identity']) and _int(h['max_bytes']) and h['max_bytes']>0,'INPUT_INVALID')
    return _canonical(h)


def _inventory(inv,limit):
    _need(type(inv) is dict and len(inv)<=3000,'INPUT_INVALID')
    for name,v in inv.items():
        _need(type(name) is str and (re.fullmatch('[0-9a-f]{64}\\.blob',name) is not None or (re.fullmatch('reservation-[0-9]{8}\\.json',name) is not None and 1<=int(name[12:20])<=1000)),'INPUT_INVALID')
        _need(type(v) is dict and set(v)=={'sha256','size'} and _hex(v['sha256']) and _int(v['size']),'INPUT_INVALID')
        _need(not name.endswith('.blob') or name[:-5]==v['sha256'],'INPUT_INVALID')
    _need(sum(v['size'] for v in inv.values())<=limit,'INPUT_INVALID')


def _verify_files(fd,header,inventory):
    names={'registry.json'}|set(inventory)
    _need(set(os.listdir(fd))==names,'INTEGRITY_FAILURE')
    before={n:_stat(os.stat(n,dir_fd=fd,follow_symlinks=False)) for n in names}
    raw=_header(header);_need(_read(fd,'registry.json',len(raw))==raw,'INTEGRITY_FAILURE')
    for n,v in inventory.items():
        _need(hashlib.sha256(_read(fd,n,v['size'])).hexdigest()==v['sha256'],'INTEGRITY_FAILURE')
    _need(set(os.listdir(fd))==names and before=={n:_stat(os.stat(n,dir_fd=fd,follow_symlinks=False)) for n in names},'INTEGRITY_FAILURE')


class ReservationLedger:
    def __init__(self,root,*,owner,registry_identity,max_bytes):
        self._header=dict(schema_version='p6.diagnostic_registry.v1',owner=owner,registry_identity=registry_identity,max_bytes=max_bytes)
        encoded=_header(self._header)
        self._root=root;self._fd=None;self._pid=os.getpid();self._thread=threading.get_ident()
        self._lifecycle='INCOMPLETE';self._busy=False;self._inventory={};self._records=[];self._plans=set()
        try:
            self._fd=_root(root);fcntl.flock(self._fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            _bound(root,self._fd);_need(not os.listdir(self._fd),'STORAGE_REFUSED')
            _publish(self._fd,'registry.json',encoded);_bound(root,self._fd)
            self._lifecycle='OPEN'
        except BaseException as exc:
            self._fail()
            if isinstance(exc,(OSError,ValueError)):raise ValueError('STORAGE_REFUSED') from None
            raise

    def _owner(self):
        _need(self._pid==os.getpid() and self._thread==threading.get_ident(),'REGISTRY_STOPPED')

    def _release(self):
        fd,self._fd=self._fd,None
        if fd is not None:os.close(fd)

    def _fail(self):
        self._lifecycle='INCOMPLETE'
        try:self._release()
        except BaseException:pass

    def _save(self,name,document):
        _need(self._lifecycle=='OPEN','REGISTRY_STOPPED')
        raw=_canonical(document);entry=dict(sha256=hashlib.sha256(raw).hexdigest(),size=len(raw))
        if name in self._inventory:
            _need(self._inventory[name]==entry and _read(self._fd,name,len(raw))==raw,'INTEGRITY_FAILURE')
        else:
            _need(sum(v['size'] for v in self._inventory.values())+len(raw)<=self._header['max_bytes'],'STORAGE_REFUSED')
            _publish(self._fd,name,raw)
            _need(self._lifecycle=='OPEN','REGISTRY_STOPPED')
            self._inventory[name]=entry

    def _authorization(self,p,a,time):
        _need(type(a) is dict and set(a)=={'schema_version','plan_hash','owner','evidence_root','registry_root','anchor_endpoint_id','approved_utc_ms','approval_reference'},'AUTHORIZATION_INVALID')
        _need(a['schema_version']=='p6.authorization.v1' and a['owner']==p['owner']==self._header['owner'] and a['plan_hash']==canonical_hash(p) and a['registry_root']==self._root and _token(a['anchor_endpoint_id']) and _int(a['approved_utc_ms']) and type(a['approval_reference']) is str and 0<len(a['approval_reference'])<=512,'AUTHORIZATION_INVALID')
        _need(_int(time) and a['approved_utc_ms']<=time<p['start_ms'] and (not self._records or time>=self._records[-1]['reserved_utc_ms']),'CLOCK_INVALID')
        try:
            evidence=a['evidence_root'];fd=_root(evidence)
            try:
                _bound(evidence,fd)
                _need(os.path.commonpath([self._root,evidence]) not in (self._root,evidence),'AUTHORIZATION_INVALID')
                x,y=os.fstat(fd),os.fstat(self._fd)
                _need((x.st_dev,x.st_ino)!=(y.st_dev,y.st_ino),'AUTHORIZATION_INVALID')
            finally:os.close(fd)
        except (OSError,ValueError,TypeError):raise ValueError('AUTHORIZATION_INVALID') from None

    def reserve(self,plan,authorization,*,reserved_utc_ms):
        self._owner()
        if self._busy:
            self._fail();raise ValueError('REGISTRY_STOPPED')
        _need(self._lifecycle=='OPEN','REGISTRY_STOPPED');self._busy=True
        try:
            p,a=_copy(plan),_copy(authorization)
            q=dict(document=p,plan=None,reservation=None,prior_receipts=[],prior_segment=None,payloads={},clock_probes={})
            _need(validate(q)['structural_status']=='VALID','PLAN_INVALID')
            self._authorization(p,a,reserved_utc_ms)
            ph=canonical_hash(p);ah=canonical_hash(a)
            _need(ph not in self._plans,'DUPLICATE_PLAN');_need(len(self._records)<1000,'STORAGE_REFUSED')
            _bound(self._root,self._fd);_verify_files(self._fd,self._header,self._inventory)
            r=dict(schema_version='p6.reservation.v1',plan_hash=ph,authorization_hash=ah,registry_identity=self._header['registry_identity'],sequence=len(self._records)+1,reserved_utc_ms=reserved_utc_ms,previous_reservation_hash=canonical_hash(self._records[-1]) if self._records else '0'*64)
            self._save(ph+'.blob',p);self._save(ah+'.blob',a);self._save(f"reservation-{r['sequence']:08d}.json",r)
            _need(self._lifecycle=='OPEN','REGISTRY_STOPPED');_bound(self._root,self._fd)
            _verify_files(self._fd,self._header,self._inventory)
            _need(self._lifecycle=='OPEN','REGISTRY_STOPPED');_bound(self._root,self._fd)
            self._records.append(r);self._plans.add(ph)
            return dict(schema_version='p6.reservation_result.v1',reservation=copy.deepcopy(r),run_id=canonical_hash(r),status='RESERVED_UNANCHORED',runtime_accepted=False,complete_family_N=None,external_authorization_verification='UPSTREAM_REQUIRED')
        except BaseException:
            self._fail();raise
        finally:self._busy=False

    def snapshot(self):
        self._owner()
        return copy.deepcopy(dict(header=self._header,acknowledged_reservations=self._records,inventory=self._inventory,lifecycle=self._lifecycle,technical_verification='LOCAL_ONLY',runtime_accepted=False))

    def close(self):
        self._owner()
        if self._busy:
            self._fail();raise ValueError('REGISTRY_STOPPED')
        try:self._release()
        except BaseException:
            self._lifecycle='INCOMPLETE';raise
        if self._lifecycle=='OPEN':self._lifecycle='CLOSED'


def inspect_reservations(root,*,header,inventory):
    h,inv=_copy(header),_copy(inventory);_header(h);_inventory(inv,h['max_bytes']);fd=None
    try:
        fd=_root(root);fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB);_bound(root,fd)
    except (OSError,ValueError):
        if fd is not None:os.close(fd)
        raise ValueError('STORAGE_REFUSED') from None
    try:
        _verify_files(fd,h,inv);_bound(root,fd)
        return dict(schema_version='p6.registry_inspection.v1',structural_status='VALID',technical_verification='LOCAL_ONLY',file_count=len(inv),total_bytes=sum(v['size'] for v in inv.values()),runtime_accepted=False)
    except OSError:raise ValueError('INTEGRITY_FAILURE') from None
    finally:os.close(fd)
