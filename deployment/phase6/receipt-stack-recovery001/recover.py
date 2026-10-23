"""Opt-in recovery001 procedure; imports/help never create evidence or call AWS.

One local supervising process, no separate PID signaling or compute-start path.
An expired cloud schedule is NOT a backstop. Review the runbook limitations.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import time

HERE=Path(__file__).resolve().parent
def module(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
g=module('stack_recovery001_guard',HERE/'guard.py')
binding=module('stack_recovery001_binding',HERE.parent/'receipt-process-identity-v4/binding.py')
legacy=module('stack_recovery001_bounded_cli',HERE/'transport.py')
PLAN=Path('/Users/antenehhailu/.codex/visualizations/2026/10/01/01a0f6c9-d66b-7d51-85fa-da98aabc6ae7/mie-planning')
ROOT=PLAN/'phase6-stack-recovery-run-001'
PREP=PLAN/'phase6-receipt-recovery-20261021'
REPO=HERE.parents[2]
OWNER='Anteneh Hailu'
DEPENDENCIES_SHA='eaa704665765f17c3f2f28606f341c768e3a3be9f42ad927889770e04adea5e6'
REQUIRED=['guard.py','transport.py','recover.py','baseline007.json','dependencies007.json','RUNBOOK.md']


def sha(raw):return hashlib.sha256(raw).hexdigest()


def save(root,name,value):
    binding.root_check(root)
    g.require(not any((root/n).exists() or (root/n).is_symlink() for n in
        ('operation-closure.json','closed-files-inventory.json')),'CLOSED_ROOT')
    g.require(Path(name).name==name,'FILENAME')
    raw=value if isinstance(value,bytes) else (g.canonical(value)+'\n').encode()
    fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    fd=os.open(root,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def read(path):return g.parse(binding.raw_file(path))


def sources(pins):
    g.require(pins['package_version']==g.VERSION and pins['owner']==OWNER and pins['evidence_root']==str(ROOT),'SOURCE_SCOPE')
    raw=binding.raw_file(HERE/'dependencies007.json')
    g.require(sha(raw)==DEPENDENCIES_SHA,'DEPENDENCY_MANIFEST')
    old=g.parse(raw)['files']
    g.require(all(pins['files'].get(k)==v for k,v in old.items()),'PRESERVED_DEPENDENCIES')
    prefix=HERE.relative_to(REPO).as_posix()+'/'
    g.require(all(prefix+n in pins['files'] for n in REQUIRED) and 'tests/test_phase6_stack_recovery001.py' in pins['files'],'PIN_MEMBERSHIP')
    binding.source_pins(pins)
    binding.identity.validate_pin(read(HERE.parent/'receipt-process-identity-v4/macos-python-pin.json'))
    g.require(sys.executable==legacy.PYTHON,'INTERPRETER')


def authorized(root):
    g.require(root==ROOT,'ROOT_IDENTITY');binding.root_check(root)
    a=read(root/'authorization.json')
    g.require(a['approved'] is True and a['owner']==OWNER and a['evidence_root']==str(root)
        and a['package_version']==g.VERSION and a['free_plan_only'] is True
        and a['protective_stop_exact_pair'] is True and a['approval_text'].strip(),'AUTHORIZATION')
    for name,field in [('EXECUTION-REVIEW.md','review_sha256'),('source-pins.json','source_pins_sha256')]:
        g.require(sha(binding.raw_file(root/name))==a[field],'AUTHORIZATION_HASH')
    sources(read(root/'source-pins.json'))
    g.require(not any((root/n).exists() or (root/n).is_symlink() for n in
        ('execution-started.json','continue-intent.json','operation-closure.json','closed-files-inventory.json')),'USED_OR_CLOSED')
    return a


def create_root(approval_text):
    g.require(type(approval_text) is str and approval_text.strip(),'EXPLICIT_APPROVAL_TEXT')
    g.require(not ROOT.exists() and not ROOT.is_symlink() and ROOT.parent.resolve()==ROOT.parent,'NEW_ROOT')
    pins_raw=binding.raw_file(PREP/'recovery001-source-pins-candidate.json');pins=g.parse(pins_raw)
    review=binding.raw_file(PLAN/'PHASE6-STACK-RECOVERY001-REVIEW.md')
    sources(pins)
    g.require(sha(review)==pins['review_sha256'],'REVIEW_PIN')
    ROOT.mkdir(mode=0o700)
    save(ROOT,'source-pins.json',pins_raw);save(ROOT,'EXECUTION-REVIEW.md',review)
    save(ROOT,'authorization.json',dict(approved=True,approval_text=approval_text,
        owner=OWNER,evidence_root=str(ROOT),package_version=g.VERSION,free_plan_only=True,
        protective_stop_exact_pair=True,review_sha256=sha(review),source_pins_sha256=sha(pins_raw),
        recorded_utc_seconds=time.time(),phase6_complete=False))


class Window:
    """Local bounds, not market timing authority. Wall/elapsed disagreement stops work."""
    def __init__(self,wall=time.time,elapsed=time.monotonic):
        self.wall=wall;self.elapsed=elapsed
        self.start_wall=wall();self.start_elapsed=elapsed();self.last=self.start_elapsed
    def check(self,minimum=0):
        w=self.wall();m=self.elapsed();e=m-self.start_elapsed
        g.require(e>=0 and 0<=m-self.last<=90 and abs((w-self.start_wall)-e)<=2,'CLOCK_OR_OBSERVATION_GAP')
        g.require(e+minimum<900 and w+minimum<self.start_wall+900,'RECOVERY_DEADLINE')
        self.last=m
        return {'utc_seconds':w,'elapsed_seconds':e,'remaining_seconds':900-e}


class API:
    def __init__(self,root,runner=legacy.bounded_cli):
        self.root=binding.root_check(root);self.runner=runner;self.sequence=0;self.identity_ok=False
        self.protection_armed=False;self.stop_used=False;self.emergency_stop_dispatched=False;self.audit_errors=[]
    def record(self,name,value):save(self.root,name,value)
    def best_record(self,name,value):
        try:self.record(name,value)
        except Exception as exc:self.audit_errors.append({'file':name,'error_type':type(exc).__name__})
    def arm_protection(self):
        g.require(self.identity_ok,'PROTECTIVE_PRINCIPAL')
        if not self.protection_armed:
            self.record('protective-stop-authorization.json',dict(request={'InstanceIds':g.PAIR},
                owner=OWNER,one_attempt=True,allow_even_if_readback_or_storage_unavailable=True))
            self.protection_armed=True
    def protective_stop(self,reason):
        g.require(self.identity_ok and self.protection_armed and not self.stop_used,'PROTECTION_NOT_ARMED_OR_USED')
        self.stop_used=True  # In-memory one-shot gate survives storage failure.
        payload={'InstanceIds':g.PAIR}
        self.best_record('protective-stop-intent.json',dict(request=payload,reason=reason,retry_allowed=False))
        return self.emergency_call('ec2','stop-instances',payload)
    def emergency_call(self,service,operation,payload):
        # Only prearmed protection or bounded final readbacks. No recovery retry.
        allowed={('ec2','describe-instances',g.canonical({'InstanceIds':g.PAIR})),
                 ('cloudformation','describe-stacks',g.canonical({'StackName':g.STACK}))}
        if operation=='stop-instances':
            g.require(service=='ec2' and payload=={'InstanceIds':g.PAIR} and self.identity_ok
                and self.protection_armed and self.stop_used and not self.emergency_stop_dispatched,'EXACT_EMERGENCY_STOP')
            self.emergency_stop_dispatched=True
        else:g.require((service,operation,g.canonical(payload)) in allowed,'EMERGENCY_READ_SCOPE')
        name='emergency-'+operation
        argv=self.argv(service,operation,payload)
        self.best_record(name+'-request.json',dict(service=service,operation=operation,payload=payload,
            utc_seconds=time.time(),aws_max_attempts=1,timeout_seconds=30,prearmed=self.protection_armed))
        try:r=self.runner(argv,dict(os.environ,AWS_MAX_ATTEMPTS='1',AWS_RETRY_MODE='standard'),timeout=30)
        except BaseException as exc:
            if isinstance(exc,legacy.CallUnknown):
                self.best_record(name+'-stdout.json',exc.stdout);self.best_record(name+'-stderr.txt',exc.stderr)
            self.best_record(name+'-unknown.json',dict(status='OUTCOME_UNKNOWN',retry_allowed=False,exception_type=type(exc).__name__))
            raise
        self.best_record(name+'-stdout.json',r.stdout);self.best_record(name+'-stderr.txt',r.stderr)
        self.best_record(name+'-result.json',dict(returncode=r.returncode,retry_allowed=False,utc_seconds=time.time()))
        g.require(r.returncode==0,'EMERGENCY_API_FAILED')
        return g.parse(r.stdout) if r.stdout.strip() else {}
    def argv(self,service,operation,payload):
        region='us-east-1' if service=='iam' else g.REGION
        endpoint='https://iam.amazonaws.com' if service=='iam' else f'https://{service}.{g.REGION}.amazonaws.com'
        return ['/opt/homebrew/bin/aws',service,operation,'--profile','mie-phase6','--region',region,
            '--endpoint-url',endpoint,'--output','json','--no-cli-pager','--no-cli-auto-prompt','--no-paginate',
            '--cli-connect-timeout','3','--cli-read-timeout','15','--cli-input-json',g.canonical(payload)]
    def call(self,service,operation,payload):
        allowed={(s,o,g.canonical(p)) for _,s,o,p in g.requests()}
        if operation=='continue-update-rollback':
            intent=read(self.root/'continue-intent.json')
            g.validate_request(payload,intent['request']['ClientRequestToken'])
            g.require(service=='cloudformation' and payload==intent['request'] and self.identity_ok
                and self.protection_armed,'CONTINUE_INTENT')
        elif operation=='detect-stack-resource-drift':
            g.require(service=='cloudformation' and payload=={'StackName':g.STACK,'LogicalResourceId':'ReceiptInstance'}
                and read(self.root/'drift-intent.json')['request']==payload and self.identity_ok,'DRIFT_INTENT')
        else:g.require((service,operation,g.canonical(payload)) in allowed,'API_ALLOWLIST')
        self.sequence+=1
        mutation=operation in ('continue-update-rollback','detect-stack-resource-drift')
        name=operation if mutation else f'api-{self.sequence:04d}-{operation}'
        argv=self.argv(service,operation,payload)
        self.record(name+'-request.json',dict(service=service,operation=operation,payload=payload,
            utc_seconds=time.time(),aws_max_attempts=1,timeout_seconds=30,endpoint=argv[argv.index('--endpoint-url')+1]))
        try:r=self.runner(argv,dict(os.environ,AWS_MAX_ATTEMPTS='1',AWS_RETRY_MODE='standard'),timeout=30)
        except BaseException as exc:
            if isinstance(exc,legacy.CallUnknown):
                self.record(name+'-stdout.json',exc.stdout);self.record(name+'-stderr.txt',exc.stderr)
            self.record(name+'-unknown.json',dict(status='OUTCOME_UNKNOWN',exception_type=type(exc).__name__,retry_allowed=False))
            raise
        self.record(name+'-stdout.json',r.stdout);self.record(name+'-stderr.txt',r.stderr)
        self.record(name+'-result.json',dict(returncode=r.returncode,retry_allowed=False,utc_seconds=time.time()))
        g.require(r.returncode==0,'API_FAILED')
        value=g.parse(r.stdout) if r.stdout.strip() else {}
        if operation=='get-caller-identity':
            self.identity_ok=value==g.baseline()['identity'];g.require(self.identity_ok,'PRINCIPAL')
        return value


def collect(api,window,label):
    start=time.time();data={}
    for name,s,o,p in g.requests():
        window.check();data[name]=api.call(s,o,p);window.check()
        if name=='identity':api.arm_protection()
    g.require(time.time()-start<=120,'STALE_SNAPSHOT')
    api.record(label+'-snapshot.json',dict(data=data,started_utc_seconds=start,finished_utc_seconds=time.time(),atomic=False))
    return data


def final_events(value,token,started):
    rows=value['StackEvents'];old_end=g.epoch('2026-10-22T20:05:21.198000+00:00')
    new=[e for e in rows if g.epoch(e['Timestamp'])>old_end]
    g.require(new and all(e['StackId']==g.STACK and e.get('ClientRequestToken')==token and
        e['LogicalResourceId'] in ('ReceiptInstance','mie-p6-qualification-001') and
        g.epoch(e['Timestamp'])>=started-5 for e in new),'RECOVERY_EVENT_SCOPE')
    latest=max(new,key=lambda e:g.epoch(e['Timestamp']))
    g.require(latest['ResourceType']=='AWS::CloudFormation::Stack' and latest['ResourceStatus']=='UPDATE_ROLLBACK_COMPLETE','RECOVERY_TERMINAL_EVENT')
    g.require(any(e['LogicalResourceId']=='ReceiptInstance' and e['ResourceStatus']=='UPDATE_COMPLETE' and
        'skip' in e.get('ResourceStatusReason','').lower() for e in new),'SKIP_EVENT')


def operate(api,window,*,sleep=time.sleep,repin=lambda:None):
    """Single-use injected orchestration. API records intent BEFORE each mutation."""
    outcome={'status':'RECOVERY_UNRESOLVED','phase6_complete':False,'runtime_qualified':False,
        'automatic_next_step':False,'cloud_schedule_backstop':False,'continuous_observation_proven':False}
    requested=False;stop_used=False;obs=0;head='0'*64;last_observation=0
    def observe():
        nonlocal obs,head,last_observation
        clock=window.check()
        inst=api.call('ec2','describe-instances',{'InstanceIds':g.PAIR});states=g.pair_state(inst)
        stack=g.one(api.call('cloudformation','describe-stacks',{'StackName':g.STACK}),'Stacks')
        end=window.check();g.require(stack['StackId']==g.STACK,'OBSERVER_STACK')
        g.require(0<=end['elapsed_seconds']-last_observation<=90,'PAIR_OBSERVATION_GAP')
        last_observation=end['elapsed_seconds']
        obs+=1;event=dict(schema='p6.stack_recovery_observation.v1',sequence=obs,previous_sha256=head,
            clock=clock,read_finished_clock=end,atomic=False,states=states,stack_status=stack['StackStatus'])
        head=sha(g.canonical(event).encode());api.record(f'observation-{obs:04d}.json',dict(event,sha256=head))
        g.require(all(s=='stopped' for s in states.values()),'HOST_NOT_STOPPED')
        return stack['StackStatus']
    def protect(reason):
        nonlocal stop_used
        if api.identity_ok and api.protection_armed and not stop_used:
            stop_used=True
            # Both IDs are fixed; allowed even when a read is unavailable. This
            # never starts/terminates a host and does not retry an unknown result.
            try:api.protective_stop(reason)
            except BaseException as exc:outcome['protective_stop_error']=type(exc).__name__
    try:
        before=collect(api,window,'before');g.snapshot(before,phase='before')
        g.require(observe()=='UPDATE_ROLLBACK_FAILED','INITIAL_OBSERVATION')
        repin();window.check(minimum=600)
        token=sha(g.canonical(dict(version=g.VERSION,root=str(ROOT),start=window.start_wall)).encode())
        request=g.recovery_request(token);g.validate_request(request,token)
        api.record('continue-intent.json',dict(request=request,window_start=window.start_wall,
            deadline_utc_seconds=window.start_wall+900,retry_allowed=False,preflight_observation_sha256=head))
        requested=True
        try:
            api.call('cloudformation','continue-update-rollback',request)
            outcome['request_outcome']='RESPONSE_RECEIVED_NOT_COMPLETION'
        except Exception as exc:
            outcome['request_outcome']='UNKNOWN_OR_FAILED_NO_RESEND';outcome['request_error']=type(exc).__name__
        # Read-only observation continues after uncertain submission. No resend.
        for _ in range(180):
            status=observe()
            if status=='UPDATE_ROLLBACK_COMPLETE':break
            g.require(status in ('UPDATE_ROLLBACK_FAILED','UPDATE_ROLLBACK_IN_PROGRESS',
                'UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS'),'UNEXPECTED_STACK_STATE')
            # The old failed state can be temporarily visible after submission.
            if status=='UPDATE_ROLLBACK_FAILED':
                events=api.call('cloudformation','describe-stack-events',{'StackName':g.STACK})
                newest=max(events['StackEvents'],key=lambda e:g.epoch(e['Timestamp']))
                g.require(newest.get('ClientRequestToken')!=token,'RECOVERY_FAILED')
            sleep(3)
        else:raise ValueError('RECOVERY_POLL_LIMIT')
        payload={'StackName':g.STACK,'LogicalResourceId':'ReceiptInstance'}
        window.check(minimum=150);started=time.time()
        api.record('drift-intent.json',dict(request=payload,started_utc_seconds=started,retry_allowed=False))
        result=api.call('cloudformation','detect-stack-resource-drift',payload);finished=time.time()
        outcome['drift']=g.drift(result,started,finished)
        after=collect(api,window,'after');outcome['configuration']=g.snapshot(after,phase='after')
        final_events(after['events'],token,window.start_wall)
        g.require(observe()=='UPDATE_ROLLBACK_COMPLETE','FINAL_OBSERVATION')
        outcome['status']='RECOVERY_VERIFIED_PENDING_INDEPENDENT_REVIEW'
    except BaseException as exc:
        outcome['error_type']=type(exc).__name__;outcome['error']=str(exc)[:500]
        protect(outcome['error'])
        # Bounded reconciliation remains useful after deadline/clock failures.
        # It cannot turn an interrupted operation into acceptance.
        for field,service,operation,payload,interpret in [
            ('final_observed_states','ec2','describe-instances',{'InstanceIds':g.PAIR},g.pair_state),
            ('final_observed_stack_status','cloudformation','describe-stacks',{'StackName':g.STACK},
                lambda v:g.one(v,'Stacks')['StackStatus'])]:
            try:outcome[field]=interpret(api.emergency_call(service,operation,payload))
            except BaseException as check:outcome[field+'_error']=type(check).__name__
    outcome.update(continue_request_may_have_been_sent=requested,protective_stop_attempted=stop_used,
        observation_count=obs,observation_head=head)
    outcome['audit_errors']=api.audit_errors
    try:api.record('outcome.json',outcome)
    except Exception as exc:
        outcome['audit_persistence_failed']=True;outcome['outcome_write_error']=type(exc).__name__
        outcome['status']='RECOVERY_UNRESOLVED'
    return outcome


def main():
    p=argparse.ArgumentParser(description=__doc__)
    choice=p.add_mutually_exclusive_group(required=True)
    choice.add_argument('--create-root',action='store_true');choice.add_argument('--run',action='store_true')
    p.add_argument('--approved',action='store_true');p.add_argument('--approval-text')
    a=p.parse_args();g.require(a.approved,'EXPLICIT_APPROVAL_REQUIRED')
    if a.create_root:create_root(a.approval_text);return
    auth=authorized(ROOT)
    save(ROOT,'execution-started.json',dict(version=g.VERSION,utc_seconds=time.time(),pid=os.getpid(),
        authorization_sha256=sha(binding.raw_file(ROOT/'authorization.json'))))
    api=API(ROOT);window=Window()
    result=operate(api,window,repin=lambda:sources(read(ROOT/'source-pins.json')))
    print(g.canonical(result),flush=True)
    if result['status']!='RECOVERY_VERIFIED_PENDING_INDEPENDENT_REVIEW':sys.exit(2)


if __name__=='__main__':main()
