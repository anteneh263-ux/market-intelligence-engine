"""Versioned candidate007 call sites. No AWS operation runs on import.

Run only after separate root/window approval and prepared immutable source pins.
"""
import sys,json,time,hashlib,base64,os,subprocess,signal
from pathlib import Path
HERE=Path(__file__).resolve().parent
import importlib.util
_context_name='mie_attempt007_context'
if _context_name not in sys.modules:
    _spec=importlib.util.spec_from_file_location(_context_name,HERE/'context.py')
    _context=importlib.util.module_from_spec(_spec)
    sys.modules[_context_name]=_context
    try:_spec.loader.exec_module(_context)
    except BaseException:
        del sys.modules[_context_name]
        raise
m=sys.modules[_context_name]
if Path(m.__file__).resolve()!=HERE/'context.py':raise RuntimeError('CONTEXT_MODULE_COLLISION')

def log(name,value):m.write(name,value)
def read(name):return m.read(name)
def call(n,s,o,p,**kw):return m.call(n,s,o,p,**kw)
def fresh_protection(prefix,min_seconds,stage,expected_template):
    m.pins();m.health(min_seconds)
    assert call(prefix+'-identity','sts','get-caller-identity',{})['Arn']=='arn:aws:iam::989879505232:root'
    s=call(prefix+'-stack','cloudformation','describe-stacks',dict(StackName=m.STACK))['Stacks'][0]
    assert s['StackId']==m.STACK and s['StackStatus']=='UPDATE_COMPLETE'
    assert {p['ParameterKey']:p['ParameterValue'] for p in s['Parameters']}=={'LaunchCompute':'yes','StopAtUtc':m.stop_expression()}
    r=call(prefix+'-resources','cloudformation','list-stack-resources',dict(StackName=m.STACK))
    assert m.ids(r)==read('preflight-summary.json')['resources']
    assert all(x['ResourceStatus'] in ('CREATE_COMPLETE','UPDATE_COMPLETE') for x in r['StackResourceSummaries'])
    t=call(prefix+'-template','cloudformation','get-template',dict(StackName=m.STACK,TemplateStage='Original'))
    assert m.body(t)==json.loads(expected_template.read_bytes())
    sch=call(prefix+'-schedule','scheduler','get-schedule',dict(Name='mie-p6-qualification-001-stop'))
    m.schedule_ok(sch,m.stop_expression())
    policy=call(prefix+'-policy','iam','get-role-policy',dict(RoleName='mie-p6-qualification-001-StopRole-NmFR4Lj25Xw8',PolicyName='stop-only-qualification-pair'))
    assert policy['PolicyDocument']==read('preflight2-policy-stdout.json')['PolicyDocument']
    pair=m.pair(prefix,stage)
    m.health(min_seconds)
    return pair
def verify_policy():
    m.pins();assert read('preflight-summary.json')['status']=='READ_ONLY_PREFLIGHT_PASS'
    prior=call('policy-get-before','cloudformation','get-stack-policy',dict(StackName=m.STACK))
    checked=m.v2.validate_policy(prior)
    log('policy-installed-verified.json',dict(**checked,set_attempts=0,persistent=True))
    print('EXISTING_EXACT_STACK_POLICY_VERIFIED_NO_WRITE',flush=True)

def reuse_kit():
    m.pins();assert read('policy-installed-verified.json')['status']=='EXACT_STACK_POLICY_READBACK'
    assert call('kit-identity','sts','get-caller-identity',{})['Arn']=='arn:aws:iam::989879505232:root'
    version='E.Z2EIEwdq7Q7JkjviL1SQSfCBmUQA6E';key='code/'+m.SHA+'.tar.gz'
    out=m.ROOT/'kit-exact-version-readback.tar.gz';assert not out.exists()
    payload=dict(Bucket=m.BUCKET,Key=key,VersionId=version,ExpectedBucketOwner=m.ACCOUNT,ChecksumMode='ENABLED')
    argv=['/opt/homebrew/bin/aws','s3api','get-object','--bucket',m.BUCKET,'--key',key,'--version-id',version,'--expected-bucket-owner',m.ACCOUNT,'--checksum-mode','ENABLED','--profile','mie-phase6','--region',m.REGION,'--endpoint-url','https://s3.eu-north-1.amazonaws.com','--no-cli-pager','--no-cli-auto-prompt','--cli-connect-timeout','3','--cli-read-timeout','15','--output','json',str(out)]
    log('kit-get-request.json',dict(argv=argv,service='s3api',operation='get-object',payload=payload,read_only=True,total_attempts=1,timeout_seconds=60))
    try:r=m.live.bounded_cli(argv,m.ENV,timeout=60)
    except BaseException as exc:
        log('kit-get-unknown.json',dict(exception_type=type(exc).__name__,retry_allowed=False));raise
    log('kit-get-stdout.json',r.stdout);log('kit-get-stderr.txt',r.stderr)
    assert r.returncode==0
    result=json.loads(r.stdout)
    raw=m.KIT.read_bytes();checksum=base64.b64encode(bytes.fromhex(m.SHA)).decode()
    assert result['VersionId']==version and result['ContentLength']==len(raw) and result['ServerSideEncryption']=='AES256' and result['ChecksumSHA256']==checksum
    assert out.read_bytes()==raw
    log('kit-version-verification.json',dict(status='EXACT_VERSION_BYTES_VERIFIED',version_id=version,bucket=m.BUCKET,key=key,sha256=m.SHA,bytes=len(raw),put_attempts=0,reused_existing=True))
    print('EXISTING_KIT_EXACT_VERSION_REUSED_NO_UPLOAD',flush=True)
def watch():
    m.pins();assert read('kit-version-verification.json')['status']=='EXACT_VERSION_BYTES_VERIFIED'
    log('local-preflight-before-window.json',m.local_preflight())
    assert call('watch-identity','sts','get-caller-identity',{})['Arn']=='arn:aws:iam::989879505232:root'
    s=call('watch-stack','cloudformation','describe-stacks',dict(StackName=m.STACK))['Stacks'][0]
    assert s['StackId']==m.STACK and s['StackStatus']=='UPDATE_COMPLETE'
    m.pair('watch','before_resize')
    assert not list((m.ROOT/'operator').iterdir())
    now=int(time.time())*1000
    c=dict(schema_version='p6.qualification_launch.v1',account_id=m.ACCOUNT,region=m.REGION,stack_id=m.STACK,operator_arn='arn:aws:iam::989879505232:root',not_before_utc_ms=now,stop_utc_ms=now+2700000,template_sha256=hashlib.sha256((m.VARIANT/'qualification-v1.json').read_bytes()).hexdigest(),runtime_bundle_sha256=m.SHA)
    log('launch-contract.json',c);h=hashlib.sha256((m.ROOT/'launch-contract.json').read_bytes()).hexdigest()
    sha,observer=m.observer_command();assert sha==h
    log('process-launch.json',dict(initial_pid=os.getpid(),uid=os.getuid(),argv=observer,
        contract_sha256=h,control_version=m.CONTROL_VERSION))
    argv=['/usr/bin/caffeinate','-i','-t','2880',*observer]
    log('launch-command.json',dict(argv=argv,contract_sha256=h,window_seconds=2700,mode='WATCH_ONLY_NO_TEMPLATE_NO_BUNDLE',scope='Observe and protectively stop exact-stack hosts; no update/start',initial_pid=os.getpid()))
    print(json.dumps(dict(status='WATCH_STARTING',stop_utc_ms=c['stop_utc_ms'])),flush=True)
    with (m.ROOT/'operator-stdout.txt').open('xb') as out,(m.ROOT/'operator-stderr.txt').open('xb') as err:os.dup2(out.fileno(),1);os.dup2(err.fileno(),2)
    os.chdir(m.WORK);os.execve(argv[0],argv,m.ENV)
def wait_stack(prefix,min_seconds):
    return m.live.progress.wait_stack(call,m.health,stack_id=m.STACK,prefix=prefix,
                                      min_seconds=min_seconds,sleep=time.sleep)

def schedule():
    e=m.health(2520);assert set(e['data']['instances'].values())=={'stopped'}
    m.pins();m.pair('schedule-before','before_resize')
    s=call('schedule-stack-before','cloudformation','describe-stacks',dict(StackName=m.STACK))['Stacks'][0]
    assert s['StackId']==m.STACK and s['StackStatus']=='UPDATE_COMPLETE'
    assert {p['ParameterKey']:p['ParameterValue'] for p in s['Parameters']}==read('preflight-summary.json')['parameters']
    r=call('schedule-resources-before','cloudformation','list-stack-resources',dict(StackName=m.STACK))
    assert m.ids(r)==read('preflight-summary.json')['resources']
    t=call('schedule-template-before','cloudformation','get-template',dict(StackName=m.STACK,TemplateStage='Original'))
    assert m.body(t)==json.loads((m.VARIANT.parent/'qualification-v1.json').read_bytes())
    # Do not send an update with less time than its immediate wait gate requires.
    m.health(2520)
    r=call('schedule-update-once','cloudformation','update-stack',dict(StackName=m.STACK,UsePreviousTemplate=True,Capabilities=['CAPABILITY_IAM'],Parameters=[dict(ParameterKey='LaunchCompute',UsePreviousValue=True),dict(ParameterKey='StopAtUtc',ParameterValue=m.stop_expression())],ClientRequestToken=hashlib.sha256((m.ROOT/'launch-contract.json').read_bytes()).hexdigest()))
    assert r['StackId']==m.STACK
    log('schedule-update-accepted.json',dict(stack_id=m.STACK,stop_expression=m.stop_expression(),repeat_allowed=False))
    wait_stack('schedule-poll',2510)
    fresh_protection('schedule-check',2500,'before_resize',m.VARIANT.parent/'qualification-v1.json')
    log('schedule-update-verified.json',dict(status='VERIFIED',stop_expression=m.stop_expression(),only_schedule_expression_changed=True))
    print('NEW_STOP_SCHEDULE_VERIFIED',flush=True)
def create_preview():
    assert read('schedule-update-verified.json')['status']=='VERIFIED'
    fresh_protection('preview-before',2500,'before_resize',m.VARIANT.parent/'qualification-v1.json')
    p=dict(StackName=m.STACK,ChangeSetName='mie-p6-receipt-medium-v2-007',ChangeSetType='UPDATE',TemplateBody=(m.VARIANT/'qualification-v1.json').read_text(),Capabilities=['CAPABILITY_IAM'],Parameters=[dict(ParameterKey='LaunchCompute',ParameterValue='yes'),dict(ParameterKey='StopAtUtc',ParameterValue=m.stop_expression())],Description='Approved attempt007: existing receipt small to medium only; source unchanged; bounded host preparation',ClientToken=hashlib.sha256((m.ROOT/'launch-contract.json').read_bytes()+b'create-medium').hexdigest())
    m.health(2480)
    r=call('medium-change-set-create-once','cloudformation','create-change-set',p)
    assert r['StackId']==m.STACK
    cid=r['Id'];log('medium-change-set-id.json',dict(change_set_id=cid))
    for n in range(24):
        m.health(2440)
        value=call('medium-preview-'+str(n),'cloudformation','describe-change-set',dict(StackName=m.STACK,ChangeSetName=cid,IncludePropertyValues=True))
        if value['Status']=='CREATE_COMPLETE':break
        assert value['Status'] in ('CREATE_PENDING','CREATE_IN_PROGRESS'),value['Status']
        time.sleep(5)
    else:raise RuntimeError('PREVIEW_NOT_READY')
    tr=dict(StackName=m.STACK,ChangeSetName=cid,TemplateStage='Original')
    tp=call('medium-preview-template','cloudformation','get-template',tr)
    pr=dict(StackName=m.STACK);policy=call('medium-preview-stack-policy','cloudformation','get-stack-policy',pr)
    checked=m.v2.execution_request(value,expected_id=cid,stop_expression=m.stop_expression(),template_request=tr,template_response=tp,policy_request=pr,policy_response=policy)
    log('medium-preview-verified.json',dict(**checked,describe_response_file='medium-preview-'+str(n)+'-stdout.json',template_response_file='medium-preview-template-stdout.json'))
    print('EXACT_RECEIPT_SMALL_TO_MEDIUM_PREVIEW_VERIFIED',flush=True)
def execute():
    m.pins();m.health(2420)
    checked=read('medium-preview-verified.json');cid=read('medium-change-set-id.json')['change_set_id']
    assert checked['ChangeSetName']==cid
    contract_hash=hashlib.sha256((m.ROOT/'launch-contract.json').read_bytes()).hexdigest()
    pid=m.operator_pid()
    argv=[m.PYTHON,str(m.PROCESS_DIR/'execute-live.py'),'--evidence-root',str(m.ROOT),'--contract-sha256',contract_hash,'--operator-pid',str(pid),'--change-set-id',cid,'--stop-expression',m.stop_expression(),'--execute-approved']
    log('medium-live-entry-request.json',dict(argv=argv,operator_pid=pid,entry_sha256=hashlib.sha256(Path(argv[1]).read_bytes()).hexdigest(),attempts=1,retry_allowed=False))
    # Replace this wrapper; the reviewed adapter owns each bounded AWS child.
    # No outer subprocess timeout can kill the adapter and orphan its child.
    with (m.ROOT/'medium-live-entry-stdout.txt').open('xb') as out,(m.ROOT/'medium-live-entry-stderr.txt').open('xb') as err:
        os.dup2(out.fileno(),1);os.dup2(err.fileno(),2)
    os.chdir(m.WORK);os.execve(argv[0],argv,m.ENV)
# Earliest admission needs2230s. Reserve70s for bounded readiness reads after
# the2300s pre-start gate. Slow calls may consume that margin: refuse, never extend.
START_MIN_SECONDS=2300
READY_MIN_SECONDS=2230
def readiness():
    import copy
    for n in range(12):
        m.health(READY_MIN_SECONDS)
        prefix='receipt-ready-'+str(n)
        values={name:call(prefix+'-'+name,service,op,payload) for name,service,op,payload in [
            ('instances','ec2','describe-instances',dict(InstanceIds=[m.SOURCE,m.RECEIPT])),
            ('volumes','ec2','describe-volumes',dict(VolumeIds=list(m.guard.VOLUMES.values()))),
            ('credits','ec2','describe-instance-credit-specifications',dict(InstanceIds=[m.SOURCE,m.RECEIPT]))]}
        states=m.states(values['instances'])
        assert states=={m.SOURCE:'stopped',m.RECEIPT:states.get(m.RECEIPT)}
        assert states[m.RECEIPT] in ('pending','running')
        # Reuse unchanged hardware/ownership validator with only its state predicate
        # substituted in memory. Raw pending response stays intact in the journal;
        # this does NOT claim delivery readiness until actual running + SSM Online.
        hardware=copy.deepcopy(values['instances'])
        for reservation in hardware['Reservations']:
            for instance in reservation['Instances']:
                if instance['InstanceId']==m.RECEIPT:instance['State']['Name']='running'
        m.guard.validate_pair(hardware,values['volumes'],values['credits'],stage='delivery')
        ss=call(prefix+'-ssm','ssm','describe-instance-information',dict(Filters=[dict(Key='InstanceIds',Values=[m.RECEIPT])]))
        rows=ss['InstanceInformationList'];assert not ss.get('NextToken') and len(rows)<=1
        if rows:assert rows[0]['InstanceId']==m.RECEIPT and rows[0]['PingStatus'] in ('Online','ConnectionLost','Inactive')
        m.health(READY_MIN_SECONDS)  # Recheck after every potentially slow read batch.
        if states[m.RECEIPT]=='running' and rows and rows[0]['PingStatus']=='Online':
            log('receipt-readiness-verified.json',dict(status='RECEIPT_RUNNING_SSM_ONLINE',read_batches=n+1,
                package_version=m.PACKAGE_VERSION,deadline_extended=False,source_stopped=True))
            return
        if n<11:time.sleep(2)
    raise RuntimeError('RECEIPT_READINESS_NOT_REACHED_NO_RESTART')

def after_resize():
    wait_stack('resize-poll',START_MIN_SECONDS)
    probe=call('resize-state','ec2','describe-instances',dict(InstanceIds=[m.SOURCE,m.RECEIPT]))
    states=m.states(probe);assert states[m.SOURCE]=='stopped' and states[m.RECEIPT] in ('stopped','running')
    stage='after_resize' if states[m.RECEIPT]=='stopped' else 'delivery'
    fresh_protection('resize-check',START_MIN_SECONDS,stage,m.VARIANT/'qualification-v1.json')
    log('medium-resize-verified.json',dict(status='RESIZE_VERIFIED',receipt_type='t4g.medium',states=states))
    if stage=='after_resize':
        m.health(START_MIN_SECONDS)
        r=call('receipt-start-once','ec2','start-instances',dict(InstanceIds=[m.RECEIPT]))
        assert len(r['StartingInstances'])==1
        i=r['StartingInstances'][0];assert i['InstanceId']==m.RECEIPT and i['PreviousState']['Name']=='stopped' and i['CurrentState']['Name']=='pending'
        log('receipt-start-accepted.json',dict(instance_id=m.RECEIPT,start_attempts=1,source_started=False))
    else:log('receipt-start-not-needed.json',dict(reason='CloudFormation left receipt running',explicit_start_attempts=0))
    readiness()
    print('RESIZE_AND_READINESS_VERIFIED_PREPARATION_REQUIRES_FRESH_ADMISSION',flush=True)
def interrupt():
    m.require_open()
    sha,_=m.observer_command()
    result=m.process.interrupt(m.ROOT,sha)
    print(json.dumps(result,sort_keys=True),flush=True)
def main(argv=None):
    args=sys.argv[1:] if argv is None else argv
    operations={f.__name__:f for f in (verify_policy,reuse_kit,watch,schedule,create_preview,execute,after_resize,interrupt)}
    m.live.control.require(__debug__ and len(args)==1 and args[0] in operations)
    # Protective interruption must remain usable after a preparation-pin failure.
    if args[0]!='interrupt':m.pins()
    return operations[args[0]]()

if __name__=='__main__':
    main()
