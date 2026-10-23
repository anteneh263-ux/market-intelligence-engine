"""No cloud execution: fixed administrative fixtures and explicit fake transport."""
import base64
import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
import pytest

HERE=Path('/Users/antenehhailu/MIE_RECEIPT_MEDIUM_WORKTREE/deployment/phase6/receipt-small008')
def module(name):
    s=importlib.util.spec_from_file_location('test_small008_'+name,HERE/(name+'.py'))
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
@pytest.fixture
def g():return module('guard')
@pytest.fixture
def data(g):
    b=g.baseline();d=copy.deepcopy(b['data'])
    d.update({n:b[n] for n in ('network','source_attr','receipt_attr')})
    d['drifts']={'StackResourceDrifts':[b['drift']['StackResourceDrift']]}
    return d

def test_exact_actual_stopped_configuration_is_admitted_without_rewriting_recovery(g,data):
    result=g.snapshot(data)
    assert result['original_recovery001_outcome']=='RECOVERY_UNRESOLVED'
    assert result['cfn_report']=='CACHED_CFN_PUBLIC_IP_DISCREPANCY_UNRESOLVED'
    assert result['phase6_complete'] is False

@pytest.mark.parametrize('name,path,value',[
    ('identity',('Account',),'other'),('identity',('Arn',),'other'),
    ('stack',('Stacks',0,'StackStatus'),'UPDATE_COMPLETE'),
    ('stack_confirm',('Stacks',0,'EnableTerminationProtection'),False),
    ('resources',('StackResourceSummaries',0,'PhysicalResourceId'),'other'),
    ('resources',('NextToken',),'more'),
    ('schedule',('State',),'DISABLED'),('schedule',('ScheduleExpression',),'at(2030-01-01T00:00:00)'),
    ('source_attr',('AssociatePublicIpAddress',),False),('receipt_attr',('AssociatePublicIpAddress',),False),
    ('receipt_attr',('NetworkInterfaceId',),'eni-other'),
    ('network',('NetworkInterfaces',0,'Attachment','DeviceIndex'),1),
    ('network',('NetworkInterfaces',0,'Attachment','AttachmentId'),'other'),
    ('network',('NetworkInterfaces',0,'Attachment','InstanceId'),'other'),
    ('network',('NetworkInterfaces',0,'OwnerId'),'other'),
    ('network',('NetworkInterfaces',0,'Attachment','DeleteOnTermination'),False),
    ('network',('NextToken',),'more'),
    ('instances',('Reservations',0,'Instances',0,'InstanceType'),'t4g.medium'),
    ('instances',('Reservations',0,'Instances',0,'State','Name'),'running'),
    ('instances',('Reservations',0,'Instances',0,'NetworkInterfaces',0,'NetworkInterfaceId'),'other'),
    ('credits',('InstanceCreditSpecifications',0,'CpuCredits'),'unlimited'),
    ('volumes',('Volumes',0,'Encrypted'),False),
    ('stack_policy',('StackPolicyBody',),'{}'),
    ('drifts',('StackResourceDrifts',0,'StackResourceDriftStatus'),'UNKNOWN'),
    ('drifts',('StackResourceDrifts',0,'PhysicalResourceId'),'other'),
    ('drifts',('StackResourceDrifts',0,'ActualProperties'),'{}'),
    ('drifts',('StackResourceDrifts',0,'PropertyDifferences'),[]),
])
def test_changed_scope_rejected(g,data,name,path,value):
    d=data[name]
    for k in path[:-1]:d=d[k]
    d[path[-1]]=value
    with pytest.raises((ValueError,KeyError)):g.snapshot(data)

def test_other_drift_is_never_covered_by_public_ip_exception(g,data):
    d=data['drifts']['StackResourceDrifts'][0]
    d['PropertyDifferences'].append({'PropertyPath':'/InstanceType','ExpectedValue':'t4g.small','ActualValue':'t4g.medium','DifferenceType':'NOT_EQUAL'})
    with pytest.raises(ValueError):g.snapshot(data)

def test_exact_sync_report_is_separate_cached_status(g,data):
    d=data['drifts']['StackResourceDrifts'][0];d.update(StackResourceDriftStatus='IN_SYNC',PropertyDifferences=[],ActualProperties=d['ExpectedProperties'])
    assert g.snapshot(data)['cfn_report']=='CACHED_CFN_IN_SYNC'

def test_running_small_hardware_checked_but_real_state_preserved(g,data):
    for res in data['instances']['Reservations']:
        for i in res['Instances']:
            if i['InstanceId']==g.RECEIPT:i['State']['Name']='running'
    original=copy.deepcopy(data['instances'])
    assert g.pair(data['instances'],data['volumes'],data['credits'],('running',))[g.RECEIPT]=='running'
    assert data['instances']==original

def test_original_recovery_still_refuses_the_observed_drift(g):
    d=g.baseline()['drift'];t=g.old.epoch(d['StackResourceDrift']['Timestamp'])
    with pytest.raises(ValueError,match='DRIFT_STATUS'):g.old.drift(d,t-1,t+1)

@pytest.mark.parametrize('wall,elapsed',[(100,0),(0,100),(3,0),(2700,2700),(-1,-1)])
def test_local_clock_or_deadline_refuses(wall,elapsed):
    m=module('run');w=[10000.0];e=[500.0];win=m.Window(lambda:w[0],lambda:e[0]);w[0]+=wall;e[0]+=elapsed
    with pytest.raises(ValueError):win.check()

def log_fixture():
    raw=b'{"host_preparation":"synthetic_fixture_only"}\n';sha=lambda b:hashlib.sha256(b).hexdigest()
    files={'result.json':base64.b64encode(raw).decode()};packed=gzip.compress(json.dumps(files,sort_keys=True,separators=(',',':')).encode(),mtime=0)
    inv={'status':'OPERATIONS_INVENTORY','files':[{'name':'result.json','bytes':len(raw),'sha256':sha(raw)}],
        'format':'gzip-base64-json-filemap','compressed_bytes':len(packed),'compressed_sha256':sha(packed),'chunk_count':1,'chunk_bytes':12000,'read_only':True}
    chunks=[{'part':0,'bytes':len(packed),'sha256':sha(packed),'combined_sha256':sha(packed),'payload':base64.b64encode(packed).decode(),'read_only':True}]
    return files,inv,chunks

@pytest.mark.parametrize('failure',['part','file_hash','path','inflated_size','truncated'])
def test_corrupt_log_export_is_rejected(failure):
    m=module('guest');files,inv,chunks=log_fixture()
    if failure=='part':chunks[0]['part']=1
    elif failure=='file_hash':inv['files'][0]['sha256']='0'*64
    elif failure=='path':inv['files'][0]['name']='../escape'
    elif failure=='inflated_size':inv['files'][0]['bytes']=17*1024*1024
    else:chunks=[]
    with pytest.raises(ValueError):m.decode(inv,chunks)

def test_log_export_verifies_exact_bytes():
    m=module('guest');files,inv,chunks=log_fixture();assert m.decode(inv,chunks)==files

class FakeWindow:
    def __init__(self,m):
        self.start_wall=time.time();self.start_elapsed=time.monotonic();self.stop_ms=(int(self.start_wall)+2700)*1000;self.m=m
    def check(self,minimum=0):return {'utc_seconds':time.time(),'elapsed_seconds':0,'remaining_seconds':2699}
    def elapsed(self):return time.monotonic()
    def expression(self):return self.m.datetime.fromtimestamp(self.stop_ms/1000,self.m.timezone.utc).strftime('at(%Y-%m-%dT%H:%M:%S)')

class FakeAPI:
    def __init__(self,m,data,root,mode):
        self.m=m;self.data=data;self.root=root;self.mode=mode;self.calls=[];self.records={};self.identity_ok=False
        self.protection_armed=False;self.stop_used=False;self.audit_errors=[];self.commands={};self.started=False;self.invocation_pending=False
        (root/'kit-exact-version.tar.gz').write_bytes(b'fake')
    def record(self,n,v):
        if n in self.records:raise FileExistsError(n)
        if self.mode=='write_failure' and n=='receipt-start-intent.json':raise OSError('synthetic full disk')
        self.records[n]=copy.deepcopy(v)
    def arm_protection(self):self.protection_armed=True
    def protective_stop(self,reason):
        assert not self.stop_used;self.stop_used=True;self.calls.append(('ec2','stop-instances',{'InstanceIds':self.m.g.PAIR}))
        for key in ('instances','instances_confirm'):
            for r in self.data[key]['Reservations']:
                for i in r['Instances']:i['State']['Name']='stopped'
    def emergency_call(self,s,o,p):return self.call(s,o,p)
    def call(self,s,o,p,kind=None):
        self.calls.append((s,o,p))
        if o=='get-object':return {}
        if o=='update-stack':
            if self.mode=='unknown_update':raise self.m.recovery.legacy.CallUnknown('synthetic')
            for key in ('stack','stack_confirm'):
                stack=self.data[key]['Stacks'][0];stack['StackStatus']='UPDATE_COMPLETE'
                for parameter in stack['Parameters']:
                    if parameter['ParameterKey']=='StopAtUtc':parameter['ParameterValue']=p['Parameters'][1]['ParameterValue']
            self.data['schedule']['ScheduleExpression']=p['Parameters'][1]['ParameterValue']
            self.data['events']['StackEvents'].insert(0,dict(StackId=self.m.g.STACK,ClientRequestToken=p['ClientRequestToken'],
                LogicalResourceId='mie-p6-qualification-001',ResourceStatus='UPDATE_COMPLETE',Timestamp=self.m.datetime.now(self.m.timezone.utc).isoformat()))
            return {'StackId':self.m.g.STACK}
        if o=='start-instances':
            if self.mode=='unknown_start':raise self.m.recovery.legacy.CallUnknown('synthetic')
            self.started=True
            for key in ('instances','instances_confirm'):
                for r in self.data[key]['Reservations']:
                    for i in r['Instances']:
                        if i['InstanceId']==self.m.g.RECEIPT:i['State']['Name']='running'
            return {'StartingInstances':[{'InstanceId':self.m.g.RECEIPT,'PreviousState':{'Name':'stopped'},'CurrentState':{'Name':'pending'}}]}
        if o=='describe-instance-information':return {'InstanceInformationList':[{'InstanceId':self.m.g.RECEIPT,'PingStatus':'Online'}]}
        if o=='send-command':
            cid=f'00000000-0000-0000-0000-{len(self.commands):012d}';self.commands[cid]=kind
            return {'Command':{'CommandId':cid,'InstanceIds':[self.m.g.RECEIPT],'DocumentName':'AWS-RunShellScript'}}
        if o=='get-command-invocation':
            kind=self.commands[p['CommandId']]
            if self.mode=='invocation_pending' and not self.invocation_pending:
                self.invocation_pending=True;raise self.m.InvocationPending()
            out={'status':'FRESH_TARGET_ADMITTED'}
            if kind=='timer':out={'status':'GUEST_STOP_VERIFIED','instance_id':self.m.g.RECEIPT,'stop_utc_ms':self.window.stop_ms,
                'timer':{'active':'active','substate':'waiting','next_us':9999999999},'remaining_us':2500000000}
            if kind=='logs-inventory':out=log_fixture()[1]
            if kind=='logs-chunk-0':out=log_fixture()[2][0]
            if self.mode=='bad_logs' and kind=='logs-chunk-0':out['sha256']='0'*64
            failed=self.mode=='preparation_failure' and kind=='preparation'
            return dict(CommandId=p['CommandId'],InstanceId=self.m.g.RECEIPT,DocumentName='AWS-RunShellScript',
                Status='Failed' if failed else 'Success',ResponseCode=1 if failed else 0,StandardOutputContent=json.dumps(out))
        key=next(n for n,sv,op,pl in self.m.g.requests() if (sv,op,pl)==(s,o,p))
        result=copy.deepcopy(self.data[key])
        if o=='get-caller-identity':self.identity_ok=True
        return result

@pytest.mark.parametrize('mode,success,starts,sends',[
    ('success',True,1,5),('invocation_pending',True,1,5),('unknown_update',False,0,0),
    ('unknown_start',False,1,0),('preparation_failure',False,1,3),('write_failure',False,0,0),('bad_logs',False,1,5)])
def test_full_procedure_injected_no_aws(data,tmp_path,monkeypatch,mode,success,starts,sends):
    m=module('run');api=FakeAPI(m,data,tmp_path,mode)
    monkeypatch.setattr(m.guest,'verify_kit',lambda *args:None)
    result=m.operate(api,FakeWindow(m),sleep=lambda _:None)
    assert result['status']==('HOST_PREPARED_PENDING_INDEPENDENT_REVIEW' if success else 'SMALL008_UNRESOLVED'),result
    ops=[o for _,o,_ in api.calls]
    assert ops.count('update-stack')==1 and ops.count('start-instances')==starts and ops.count('send-command')==sends
    assert ops.count('stop-instances')==1
    assert not set(ops)&{'continue-update-rollback','create-change-set','execute-change-set','modify-network-interface-attribute','put-object'}
    assert result['final_observed_states']=={i:'stopped' for i in m.g.PAIR}
    assert result['phase6_complete'] is False and result['receipt_runtime_qualified'] is False

def test_protective_stop_survives_storage_failure(tmp_path,monkeypatch):
    m=module('run');tmp_path.chmod(0o700);calls=[]
    def runner(argv,env,timeout):calls.append(argv);return SimpleNamespace(returncode=0,stdout=b'{}',stderr=b'')
    api=m.API(tmp_path,runner);api.identity_ok=True;api.arm_protection()
    monkeypatch.setattr(api,'record',lambda *args:(_ for _ in ()).throw(OSError('synthetic full disk')))
    api.protective_stop('test');assert len(calls)==1 and 'stop-instances' in calls[0]
    with pytest.raises(ValueError):api.protective_stop('retry forbidden')
    assert len(calls)==1 and api.audit_errors

def test_api_refuses_unapproved_scope_before_transport(tmp_path):
    m=module('run');tmp_path.chmod(0o700)
    def no_network(*args,**kwargs):pytest.fail('unexpected network')
    api=m.API(tmp_path,no_network)
    for s,o,p in [('ec2','start-instances',{'InstanceIds':[m.g.SOURCE]}),('ec2','modify-network-interface-attribute',{}),
        ('cloudformation','continue-update-rollback',{}),('ssm','get-command-invocation',{'CommandId':'other','InstanceId':m.g.RECEIPT})]:
        with pytest.raises((ValueError,FileNotFoundError)):api.call(s,o,p)

def test_s3_custom_get_object_uses_exact_direct_flags_not_cli_input_json(tmp_path):
    m=module('run');tmp_path.chmod(0o700);captured=[]
    def runner(argv,env,timeout):
        captured.append(argv);return SimpleNamespace(returncode=0,stdout=b'{}',stderr=b'')
    api=m.API(tmp_path,runner);api.call('s3api','get-object',m.guest.kit_request())
    argv=captured[0]
    assert '--cli-input-json' not in argv and argv[-1]==str(tmp_path/'kit-exact-version.tar.gz')
    assert argv[argv.index('--bucket')+1]==m.g.BUCKET
    assert argv[argv.index('--key')+1]=='code/'+m.g.KIT_SHA+'.tar.gz'
    assert argv[argv.index('--version-id')+1]==m.g.KIT_VERSION
    assert argv[argv.index('--expected-bucket-owner')+1]==m.g.ACCOUNT

def test_help_does_not_create_runtime_root_or_call_aws():
    m=module('run');assert not m.ROOT.exists()
    result=subprocess.run([m.recovery.legacy.PYTHON,str(HERE/'run.py'),'--help'],capture_output=True,timeout=10)
    assert result.returncode==0 and not m.ROOT.exists()

def test_user_pause_blocks_even_explicit_run_or_create_flags(monkeypatch):
    m=module('run')
    for flag in ('--run','--create-root'):
        monkeypatch.setattr(m.sys,'argv',['run.py',flag,'--approved','--approval-text','test'])
        with pytest.raises(ValueError,match='PAUSED_BY_OWNER'):m.main()
    assert not m.ROOT.exists()

def test_small_kit_and_guest_payload_preserve_exact_version_and_paths():
    m=module('run');raw=m.KIT.read_bytes()
    m.guest.verify_kit(dict(VersionId=m.g.KIT_VERSION,ContentLength=len(raw),ServerSideEncryption='AES256',
        ChecksumSHA256=base64.b64encode(hashlib.sha256(raw).digest()).decode()),raw)
    p=m.guest.generator.request('receipt',m.g.RECEIPT,m.g.KIT_VERSION,attempt='receipt-002',stop_utc_ms=1800000000000,expected_timer_us=99)
    script=p['Parameters']['commands'][0]
    assert m.g.KIT_SHA in script and m.g.KIT_VERSION in script and 'delivery-v2' in script and 'medium' not in script
    assert script.count('MIE_TIMER_PY')==4
    subprocess.run(['/bin/bash','-n'],input=script,text=True,check=True)
