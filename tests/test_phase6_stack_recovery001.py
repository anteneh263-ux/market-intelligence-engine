"""Separate recovery contract: historical fixtures, fake API, zero AWS calls."""
import copy
import importlib.util
import json
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path('/Users/antenehhailu/MIE_RECEIPT_MEDIUM_WORKTREE')
HERE = REPO/'deployment/phase6/receipt-stack-recovery001'


def module(name):
    spec = importlib.util.spec_from_file_location('test_recovery001_'+name, HERE/(name+'.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def g():
    return module('guard')


@pytest.fixture
def data():
    return json.loads((HERE/'baseline007.json').read_bytes())['data']


def test_known_failed_rollback_is_eligible(g, data):
    assert g.snapshot(data, phase='before')['status'] == 'ELIGIBLE_STOPPED_FAILED_STACK'


@pytest.mark.parametrize('key,path,value', [
    ('identity', ('Account',), '000000000000'),
    ('identity', ('Arn',), 'arn:aws:iam::989879505232:user/other'),
    ('stack', ('Stacks', 0, 'StackStatus'), 'UPDATE_COMPLETE'),
    ('stack_confirm', ('Stacks', 0, 'StackStatus'), 'UPDATE_ROLLBACK_COMPLETE'),
    ('stack', ('Stacks', 0, 'RoleARN'), 'arn:aws:iam::989879505232:role/other'),
    ('stack', ('Stacks', 0, 'EnableTerminationProtection'), False),
    ('stack', ('Stacks', 0, 'Parameters', 0, 'ParameterValue'), 'at(2030-01-01T00:00:00)'),
    ('resources', ('StackResourceSummaries', 7, 'PhysicalResourceId'), 'i-other'),
    ('resources', ('StackResourceSummaries', 12, 'ResourceStatus'), 'UPDATE_FAILED'),
    ('resources', ('NextToken',), 'more'),
    ('instances', ('Reservations', 0, 'Instances', 0, 'InstanceType'), 't4g.medium'),
    ('instances', ('Reservations', 0, 'Instances', 0, 'State', 'Name'), 'running'),
    ('instances_confirm', ('Reservations', 0, 'Instances', 0, 'State', 'Name'), 'pending'),
    ('volumes', ('Volumes', 0, 'Encrypted'), False),
    ('credits', ('InstanceCreditSpecifications', 0, 'CpuCredits'), 'unlimited'),
    ('schedule', ('State',), 'DISABLED'),
    ('policy', ('PolicyDocument',), {'Version':'2012-10-17','Statement':[]}),
    ('stack_policy', ('StackPolicyBody',), '{}'),
    ('security', ('SecurityGroups', 0, 'IpPermissions'), [{'IpProtocol':'-1'}]),
])
def test_changed_scope_rejected(g, data, key, path, value):
    cursor = data[key]
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value
    with pytest.raises((ValueError, KeyError)):
        g.snapshot(data, phase='before')


@pytest.mark.parametrize('change', ['forward_only', 'other_token', 'later_event', 'other_failure'])
def test_only_exact_failed_rollback_can_be_skipped(g, data, change):
    events = data['events']['StackEvents']
    if change == 'forward_only':
        events[:] = [e for e in events if e['Timestamp'] < '2026-10-22T20:01:29']
    elif change == 'other_token':
        events[1]['ClientRequestToken'] = 'other'
    elif change == 'later_event':
        e = copy.deepcopy(events[0]);e['EventId']='new';e['Timestamp']='2026-10-23T04:00:00+00:00'
        events.insert(0,e)
    else:
        events[1]['LogicalResourceId']='SourceInstance'
    with pytest.raises(ValueError):
        g.eligibility(data['events'])


@pytest.mark.parametrize('extra', [ {'ForceRollback':True}, {'RoleARN':'other'},
    {'ResourcesToSkip':['ReceiptInstance','SourceInstance']}, {'StackName':'other'},
    {'ClientRequestToken':''} ])
def test_request_scope_is_exact(g, extra):
    token='a'*64
    request=g.recovery_request(token);request.update(extra)
    with pytest.raises(ValueError):
        g.validate_request(request, token)


def test_drift_requires_fresh_exact_individual_result(g):
    drift = dict(StackId=g.STACK, LogicalResourceId='ReceiptInstance', PhysicalResourceId=g.RECEIPT,
        ResourceType='AWS::EC2::Instance', StackResourceDriftStatus='IN_SYNC',
        ExpectedProperties=json.dumps({'InstanceType':'t4g.small','ImageId':g.AMI}),
        ActualProperties=json.dumps({'InstanceType':'t4g.small','ImageId':g.AMI}),
        PropertyDifferences=[], Timestamp='2026-10-23T04:00:05+00:00')
    start=g.epoch('2026-10-23T04:00:00+00:00');end=start+10
    assert g.drift({'StackResourceDrift':drift}, start, end)['status']=='INDIVIDUAL_DRIFT_IN_SYNC'
    for key, value in [('StackResourceDriftStatus','NOT_CHECKED'),('StackResourceDriftStatus','MODIFIED'),
        ('StackResourceDriftStatus','DELETED'),('PhysicalResourceId','i-other'),
        ('Timestamp','2026-10-22T04:00:00+00:00'),('Timestamp','2026-10-24T04:00:00+00:00'),
        ('ActualProperties','{}'),('PropertyDifferences',[{'PropertyPath':'/InstanceType'}])]:
        changed=copy.deepcopy(drift);changed[key]=value
        with pytest.raises(ValueError):g.drift({'StackResourceDrift':changed},start,end)


def test_post_recovery_needs_more_than_stack_complete(g, data):
    for key in ('stack','stack_confirm'):
        data[key]['Stacks'][0]['StackStatus']='UPDATE_ROLLBACK_COMPLETE'
    with pytest.raises(ValueError):g.snapshot(data,phase='after')
    data['resources']['StackResourceSummaries'][7]['ResourceStatus']='UPDATE_COMPLETE'
    assert g.snapshot(data,phase='after')['status']=='STOPPED_STACK_CONFIGURATION_CHECKED'


class FakeWindow:
    def __init__(self):self.start_wall=time.time()
    def check(self,minimum=0):return {'utc_seconds':time.time(),'elapsed_seconds':1,'remaining_seconds':899}


class FakeAPI:
    def __init__(self,m,data,*,mode='success'):
        self.m=m;self.data=data;self.mode=mode;self.identity_ok=False;self.records={};self.calls=[];self.sent=False
        self.protection_armed=False;self.audit_errors=[]
    def arm_protection(self):self.protection_armed=True
    def protective_stop(self,reason):return self.call('ec2','stop-instances',{'InstanceIds':self.m.g.PAIR})
    def emergency_call(self,s,o,p):return self.call(s,o,p)
    def record(self,name,value):
        if name in self.records:raise FileExistsError(name)
        self.records[name]=copy.deepcopy(value)
    def call(self,s,o,p):
        self.calls.append((s,o,copy.deepcopy(p)))
        if o=='continue-update-rollback':
            assert self.records['continue-intent.json']['request']==p
            self.sent=True;self.token=p['ClientRequestToken']
            if self.mode=='unknown':raise self.m.legacy.CallUnknown('TIMEOUT')
            return {}
        if o=='stop-instances':return {'StoppingInstances':[]}
        if o=='detect-stack-resource-drift':
            d=dict(StackId=self.m.g.STACK,LogicalResourceId='ReceiptInstance',PhysicalResourceId=self.m.g.RECEIPT,
                ResourceType='AWS::EC2::Instance',StackResourceDriftStatus='IN_SYNC',PropertyDifferences=[],
                Timestamp=self.m.g.datetime.fromtimestamp(time.time(),tz=__import__('datetime').timezone.utc).isoformat(),
                ExpectedProperties=json.dumps({'InstanceType':'t4g.small','ImageId':self.m.g.AMI}),
                ActualProperties=json.dumps({'InstanceType':'t4g.small','ImageId':self.m.g.AMI}))
            if self.mode=='drift':d['StackResourceDriftStatus']='MODIFIED'
            return {'StackResourceDrift':d}
        name=next(n for n,sv,op,pl in self.m.g.requests() if (sv,op,pl)==(s,o,p))
        d=copy.deepcopy(self.data[name])
        if o=='get-caller-identity':
            if self.mode=='wrong-account':d['Account']='wrong'
            self.identity_ok=d==self.m.g.baseline()['identity']
            self.m.g.require(self.identity_ok,'PRINCIPAL')
        if self.sent:
            if name=='stack':d['Stacks'][0]['StackStatus']='UPDATE_ROLLBACK_COMPLETE'
            if name=='instances' and self.mode=='running':d['Reservations'][0]['Instances'][0]['State']['Name']='running'
            if name=='resources':d['StackResourceSummaries'][7]['ResourceStatus']='UPDATE_COMPLETE'
            if name=='events':
                now=self.m.g.datetime.fromtimestamp(time.time(),tz=__import__('datetime').timezone.utc).isoformat()
                base=dict(StackId=self.m.g.STACK,ClientRequestToken=self.token,Timestamp=now)
                d['StackEvents']=[dict(base,EventId='new-stack',LogicalResourceId='mie-p6-qualification-001',
                    ResourceType='AWS::CloudFormation::Stack',ResourceStatus='UPDATE_ROLLBACK_COMPLETE'),
                    dict(base,EventId='new-receipt',LogicalResourceId='ReceiptInstance',ResourceType='AWS::EC2::Instance',
                    ResourceStatus='UPDATE_COMPLETE',ResourceStatusReason='Resource skipped during UpdateRollback')]+d['StackEvents']
        return d


@pytest.mark.parametrize('mode,expected',[
    ('success','RECOVERY_VERIFIED_PENDING_INDEPENDENT_REVIEW'),
    ('unknown','RECOVERY_VERIFIED_PENDING_INDEPENDENT_REVIEW'),
    ('drift','RECOVERY_UNRESOLVED'),('running','RECOVERY_UNRESOLVED'),('wrong-account','RECOVERY_UNRESOLVED')])
def test_procedure_with_fake_aws(data,mode,expected):
    m=module('recover');api=FakeAPI(m,data,mode=mode)
    result=m.operate(api,FakeWindow(),sleep=lambda _:None)
    assert result['status']==expected
    calls=[o for _,o,_ in api.calls]
    assert calls.count('continue-update-rollback')==(0 if mode=='wrong-account' else 1)
    assert calls.count('stop-instances')==(1 if mode in ('drift','running') else 0)
    assert calls.count('detect-stack-resource-drift')==(0 if mode in ('running','wrong-account') else 1)
    assert not set(calls)&{'start-instances','update-stack','execute-change-set','send-command','set-stack-policy'}
    assert result['phase6_complete'] is False and result['runtime_qualified'] is False


def test_no_resubmit_after_unknown_and_still_failed(data):
    m=module('recover');api=FakeAPI(m,data,mode='unknown')
    old=api.call
    def call(s,o,p):
        if api.sent and o=='describe-stacks':
            api.calls.append((s,o,p));return data['stack']
        return old(s,o,p)
    api.call=call
    result=m.operate(api,FakeWindow(),sleep=lambda _:None)
    assert result['status']=='RECOVERY_UNRESOLVED'
    assert sum(o=='continue-update-rollback' for _,o,_ in api.calls)==1
    assert sum(o=='stop-instances' for _,o,_ in api.calls)==1


@pytest.mark.parametrize('wall,elapsed',[(1004,1),(999,-1),(1100,100),(2001,901)])
def test_clock_gap_jump_and_deadline_refuse(wall,elapsed):
    m=module('recover');w=[1000];e=[0]
    c=m.Window(wall=lambda:w[0],elapsed=lambda:e[0]);w[0]=wall;e[0]=elapsed
    with pytest.raises(ValueError):c.check()


def test_api_exact_once_and_timeout_preservation(tmp_path):
    m=module('recover');tmp_path.chmod(0o700);calls=[]
    def runner(argv,env,**kwargs):
        calls.append(argv);assert env['AWS_MAX_ATTEMPTS']=='1'
        raise m.legacy.CallUnknown('TIMEOUT',b'partial',b'error')
    api=m.API(tmp_path,runner=runner);api.identity_ok=True;api.arm_protection()
    request=m.g.recovery_request('a'*64)
    m.save(tmp_path,'continue-intent.json',{'request':request})
    with pytest.raises(m.legacy.CallUnknown):api.call('cloudformation','continue-update-rollback',request)
    assert (tmp_path/'continue-update-rollback-stdout.json').read_bytes()==b'partial'
    assert m.read(tmp_path/'continue-update-rollback-unknown.json')['retry_allowed'] is False
    with pytest.raises(FileExistsError):api.call('cloudformation','continue-update-rollback',request)
    assert len(calls)==1


@pytest.mark.parametrize('s,o,p',[
    ('ec2','start-instances',{'InstanceIds':['i-0c5a2a75ca70db228']}),
    ('cloudformation','continue-update-rollback',{'StackName':'other','ResourcesToSkip':['ReceiptInstance']}),
    ('ec2','stop-instances',{'InstanceIds':['i-other']}),
    ('cloudformation','detect-stack-resource-drift',{'StackName':'other','LogicalResourceId':'ReceiptInstance'}),
    ('iam','put-role-policy',{}),('scheduler','update-schedule',{})])
def test_api_refuses_broader_mutations(tmp_path,s,o,p):
    m=module('recover');tmp_path.chmod(0o700);calls=[]
    api=m.API(tmp_path,runner=lambda *args,**kwargs:calls.append(args))
    with pytest.raises((ValueError,FileNotFoundError,KeyError)):api.call(s,o,p)
    assert not calls


def test_safe_files_and_closed_roots(tmp_path):
    m=module('recover');tmp_path.chmod(0o700)
    target=tmp_path/'outside';target.write_text('preserve')
    (tmp_path/'link').symlink_to(target)
    with pytest.raises(FileExistsError):m.save(tmp_path,'link',{'bad':True})
    with pytest.raises(ValueError):m.save(tmp_path,'../escape',{})
    m.save(tmp_path,'operation-closure.json',{'closed':True})
    with pytest.raises(ValueError):m.save(tmp_path,'later',{})
    assert target.read_text()=='preserve'


def test_invalid_authorization_does_not_touch_cloud(tmp_path,monkeypatch):
    m=module('recover');tmp_path.chmod(0o700);monkeypatch.setattr(m,'ROOT',tmp_path)
    m.save(tmp_path,'authorization.json',{'approved':False})
    with pytest.raises(ValueError):m.authorized(tmp_path)
    assert not (tmp_path/'execution-started.json').exists()


def test_duplicate_json_rejected(g):
    with pytest.raises(ValueError):g.parse('{"a":1,"a":2}')


def test_unchanged_transport_functions():
    import ast
    def functions(path):
        return [ast.dump(n,include_attributes=False) for n in ast.parse(path.read_text()).body
            if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in ('CallUnknown','bounded_cli')]
    assert functions(HERE/'transport.py')==functions(HERE.parent/'receipt-medium-control-v2/execute-live.py')


def test_transport_bounds_output_and_time():
    m=module('recover')
    for program,opts,reason in [('import time;time.sleep(5)',{'timeout':.05},'TIMEOUT'),
        ('print("x"*20000)',{'limit':1000},'OUTPUT_LIMIT')]:
        with pytest.raises(m.legacy.CallUnknown) as error:
            m.legacy.bounded_cli([m.legacy.PYTHON,'-c',program],{},**opts)
        assert error.value.reason==reason


def test_sources_require_complete_pins(monkeypatch):
    m=module('recover')
    with pytest.raises(ValueError):m.sources(dict(package_version=m.g.VERSION,owner=m.OWNER,evidence_root=str(m.ROOT),files={}))


def test_import_and_help_have_no_runtime_side_effect():
    m=module('recover')
    assert not m.ROOT.exists()
    result=subprocess.run([m.legacy.PYTHON,str(HERE/'recover.py'),'--help'],capture_output=True,text=True,timeout=5)
    assert result.returncode==0 and '--approved' in result.stdout
    assert not m.ROOT.exists()


def test_storage_failure_after_submission_still_stops_and_reads(tmp_path,data):
    import errno
    m=module('recover');tmp_path.chmod(0o700)
    calls=[];failed=[False]
    def runner(argv,env,**kwargs):
        s,o=argv[1:3];p=m.g.parse(argv[argv.index('--cli-input-json')+1]);calls.append(o)
        if o=='continue-update-rollback':failed[0]=True;value={}
        elif o=='stop-instances':value={}
        else:
            name=next(n for n,sv,op,pl in m.g.requests() if (sv,op,pl)==(s,o,p))
            value=data[name]
        return subprocess.CompletedProcess(argv,0,json.dumps(value).encode(),b'')
    api=m.API(tmp_path,runner=runner);ordinary=api.record
    def record(n,v):
        if failed[0]:raise OSError(errno.ENOSPC,'No space left on device')
        ordinary(n,v)
    api.record=record
    result=m.operate(api,FakeWindow(),sleep=lambda _:None)
    assert result['status']=='RECOVERY_UNRESOLVED' and result['audit_persistence_failed']
    assert result['error_type']=='OSError' and 'No space' in result['error']
    assert result['final_observed_states']=={i:'stopped' for i in m.g.PAIR}
    assert result['final_observed_stack_status']=='UPDATE_ROLLBACK_FAILED'
    assert calls.count('continue-update-rollback')==1 and calls.count('stop-instances')==1
    assert calls[-3:]==['stop-instances','describe-instances','describe-stacks']
    for fn in [lambda:api.protective_stop('again'),lambda:api.emergency_call('ec2','stop-instances',{'InstanceIds':m.g.PAIR})]:
        with pytest.raises(ValueError):fn()
    assert calls.count('stop-instances')==1


def test_emergency_stop_timeout_is_never_retried(tmp_path):
    m=module('recover');tmp_path.chmod(0o700);calls=[]
    def runner(*a,**kw):calls.append(a);raise m.legacy.CallUnknown('TIMEOUT')
    api=m.API(tmp_path,runner=runner);api.identity_ok=True;api.arm_protection()
    with pytest.raises(m.legacy.CallUnknown):api.protective_stop('failure')
    with pytest.raises(ValueError):api.protective_stop('retry')
    assert len(calls)==1
