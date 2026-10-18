"""Local supervisor state-machine tests. No AWS resources or calls."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from app.research import phase6_qualification_operator as op
from app.research.phase6_anchor_validation import _hash
from app.research.phase6_object_store import ObjectWriter


class Clock:
    def __init__(self,fault=None):self.now=0;self.base=1792320000000;self.fault=fault
    def utc(self):return self.base+self.now-(5000 if self.fault=='regress' and self.now>=10000 else 0)
    def continuous(self):
        if self.fault=='unavailable' and self.now>=10000:raise OSError('clock unavailable')
        return self.now*1000000
    def sleep(self,seconds):self.now+=int(seconds*1000)


def contract(clock):
    return dict(schema_version='p6.qualification_launch.v1',account_id=op.ACCOUNT,region=op.REGION,
        stack_id='arn:aws:cloudformation:'+op.REGION+':'+op.ACCOUNT+':stack/'+op.PREFIX+'/12345678-1234-1234-1234-123456789abc',
        operator_arn='arn:aws:iam::'+op.ACCOUNT+':root',not_before_utc_ms=clock.base,
        stop_utc_ms=clock.base+30000,template_sha256=hashlib.sha256(b'{}').hexdigest(),runtime_bundle_sha256=hashlib.sha256(b'bundle').hexdigest())


class Aws:
    def __init__(self,c,clock,fault=None):
        self.c=c;self.clock=clock;self.fault=fault;self.calls=[];self.updated=False;self.states={}
    def call(self,service,operation,payload):
        self.calls.append((service,operation,copy.deepcopy(payload)))
        if operation=='get-caller-identity':return dict(Account=op.ACCOUNT,Arn=self.c['operator_arn'])
        if operation=='describe-stacks':
            state='CREATE_COMPLETE'
            if self.updated:
                state='UPDATE_IN_PROGRESS' if self.clock.now<5000 else 'UPDATE_COMPLETE'
                if self.fault=='partial' and self.clock.now>=5000:state='UPDATE_ROLLBACK_FAILED'
            return {'Stacks':[{'StackId':self.c['stack_id'],'StackStatus':state}]}
        if operation=='update-stack':
            self.updated=True
            if self.fault=='update_unknown':raise TimeoutError('unknown update')
            return {'StackId':self.c['stack_id']}
        if operation=='describe-instances':
            if self.updated and self.clock.now>=5000 and not self.states:
                self.states={'i-'+'1'*17:'running'}
                if self.fault!='partial':self.states['i-'+'2'*17]='running'
            if self.fault=='observation_error' and self.clock.now>=10000 and 'Filters' in payload:
                raise TimeoutError('observer failed')
            ids=payload.get('InstanceIds',sorted(self.states))
            found=[]
            for instance_id in ids:
                if self.states.get(instance_id)=='stopping' and self.fault!='never_stops':self.states[instance_id]='stopped'
                found.append(dict(InstanceId=instance_id,State={'Name':self.states[instance_id]},Tags=[
                    {'Key':'aws:cloudformation:stack-id','Value':self.c['stack_id']},
                    {'Key':'aws:cloudformation:logical-id','Value':'ReceiptInstance' if instance_id.endswith('1') else 'SourceInstance'},
                    {'Key':'Purpose','Value':'wrong' if self.fault=='wrong_owner_before_stop' and 'InstanceIds' in payload else 'phase6-qualification-only'}]))
            result={'Reservations':[{'OwnerId':op.ACCOUNT,'Instances':found}]} if found else {'Reservations':[]}
            if self.fault=='paginated':result['NextToken']='more'
            return result
        if operation=='stop-instances':
            assert len(payload['InstanceIds'])==1 and not any(k in payload for k in ('Force','SkipOsShutdown','Hibernate'))
            instance_id=payload['InstanceIds'][0]
            self.states[instance_id]='stopping'
            if self.fault=='stop_unknown':raise TimeoutError('unknown stop')
            return {'StoppingInstances':[{'InstanceId':instance_id,'CurrentState':{'Name':'stopping'}}]}
        raise AssertionError(operation)


def setup(tmp_path,clock,fault=None):
    c=contract(clock);root=tmp_path/'operator';root.mkdir()
    return c,Aws(c,clock,fault),ObjectWriter(str(root),owner=op.ACCOUNT,run_id=_hash(c),max_bytes=1000000)


@pytest.mark.parametrize('fault',[None,'partial','update_unknown','stop_unknown','never_stops',
                                  'wrong_owner_before_stop','observation_error','clock_regress','clock_unavailable','trace'])
def test_exact_stack_lifecycle_and_unknown_outcomes(tmp_path,monkeypatch,fault):
    clock=Clock('regress' if fault=='clock_regress' else 'unavailable' if fault=='clock_unavailable' else None)
    c,api,writer=setup(tmp_path,clock,fault)
    if fault=='trace':
        original=writer.put
        def put(raw,**kw):
            if json.loads(raw)['kind']=='observation':raise OSError('unknown evidence write')
            return original(raw,**kw)
        monkeypatch.setattr(writer,'put',put)
    ready=[]
    result=op.supervise(c,api=api,writer=writer,template=b'{}',bundle=b'bundle',utc_ms=clock.utc,
                       elapsed_ns=clock.continuous,sleep=clock.sleep,armed=ready.append)
    assert len(ready)==1 and api.calls[0][1]=='get-caller-identity'
    assert sum(x[1]=='update-stack' for x in api.calls)==1
    stops=[x for x in api.calls if x[1]=='stop-instances']
    assert len(stops)==len({x[2]['InstanceIds'][0] for x in stops})
    if fault=='wrong_owner_before_stop':assert not stops and result['status']=='UNRESOLVED'
    elif fault in ('never_stops','observation_error'):assert stops and result['status']=='UNRESOLVED'
    elif fault=='trace':assert stops and result['status']=='UNRESOLVED' and not result['evidence_complete']
    else:assert stops and result['status']=='STOP_CONFIRMED'
    assert not result['runtime_accepted']
    if fault!='trace':
        events=[json.loads(p.read_text()) for p in Path(writer.root).glob('*.blob')]
        assert not any(e['kind']=='stop_response_received' and e['data']['stopped_confirmed'] for e in events)
        if fault=='clock_unavailable':assert any(e['data'].get('clock_available') is False for e in events)
    writer.close()


@pytest.mark.parametrize('field,value',[('stop_utc_ms',1792320000000),('stop_utc_ms',1792323601000),
    ('not_before_utc_ms',1792320010000),('stop_utc_ms',True),('region','us-east-1'),('account_id','123456789012'),
    ('stack_id','arn:aws:cloudformation:eu-north-1:989879505232:stack/H-025/12345678-1234-1234-1234-123456789abc')])
def test_launch_contract_refuses_stale_excessive_or_wrong_target(field,value):
    clock=Clock();c=contract(clock);c[field]=value
    with pytest.raises(Exception):op.contract(c,clock.utc())


def test_hash_mismatch_or_partial_inventory_never_launches(tmp_path):
    clock=Clock();c,api,writer=setup(tmp_path,clock)
    with pytest.raises(ValueError):op.supervise(c,api=api,writer=writer,template=b'changed',utc_ms=clock.utc,elapsed_ns=clock.continuous,sleep=clock.sleep)
    assert not api.updated
    writer.close()


def test_unknown_prelaunch_record_prevents_update(tmp_path,monkeypatch):
    clock=Clock();c,api,writer=setup(tmp_path,clock)
    monkeypatch.setattr(writer,'put',lambda *a,**kw:(_ for _ in ()).throw(OSError('unknown armed write')))
    with pytest.raises(ValueError):op.supervise(c,api=api,writer=writer,template=b'{}',bundle=b'bundle',utc_ms=clock.utc,elapsed_ns=clock.continuous,sleep=clock.sleep)
    assert not api.updated
    writer.close()


def test_cli_requires_explicit_execution_flag():
    with pytest.raises(SystemExit) as exc:op.main(['--contract','/tmp/c','--sha256','a'*64,'--evidence-root','/tmp/o'])
    assert exc.value.code==2


def test_host_continuous_clock_is_available():
    assert op.continuous_ns()>0


def test_frozen_clocks_trigger_protective_stop(tmp_path):
    clock=Clock();c,api,writer=setup(tmp_path,clock)
    result=op.supervise(c,api=api,writer=writer,template=b'{}',bundle=b'bundle',
        utc_ms=lambda:clock.base,elapsed_ns=lambda:0,sleep=clock.sleep)
    assert result['status']=='STOP_CONFIRMED'
    assert any(x[1]=='stop-instances' for x in api.calls)
    events=[json.loads(p.read_text()) for p in Path(writer.root).glob('*.blob')]
    assert any(e['kind']=='finished' and e['data']['reason']=='CLOCK_STALLED' for e in events)


@pytest.mark.parametrize('bundle',[None,b'wrong',b''])
def test_missing_or_wrong_bundle_never_launches(tmp_path,bundle):
    clock=Clock();c,api,writer=setup(tmp_path,clock)
    with pytest.raises(ValueError,match='QUALIFICATION_REFUSED'):
        op.supervise(c,api=api,writer=writer,template=b'{}',bundle=bundle,
            utc_ms=clock.utc,elapsed_ns=clock.continuous,sleep=clock.sleep)
    assert not api.updated
    writer.close()
