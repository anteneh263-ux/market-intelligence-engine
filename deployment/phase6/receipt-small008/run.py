"""One opt-in small008 host-preparation process. Import/help performs no work.

No medium resize, network repair, pilot, secret material, or mutation retry.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import importlib.util

HERE=Path(__file__).resolve().parent
def module(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
g=module('small008_guard',HERE/'guard.py')
recovery=module('small008_recovery',HERE.parent/'receipt-stack-recovery001/recover.py')
guest=module('small008_guest',HERE/'guest.py')
binding=recovery.binding
PLAN=recovery.PLAN;PREP=recovery.PREP;ROOT=PLAN/'phase6-host-preparation-run-008'
REPO=HERE.parents[2];OWNER='Anteneh Hailu'
KIT=PREP/'receipt-host-kit-v2.tar.gz'
save=recovery.save;read=recovery.read;sha=recovery.sha

def sources(pins):
    g.require(pins['package_version']==g.VERSION and pins['owner']==OWNER and
        pins['evidence_root']==str(ROOT),'SMALL008_PINS_SCOPE')
    prefix=HERE.relative_to(REPO).as_posix()+'/'
    g.require(all(prefix+n in pins['files'] for n in
        ('guard.py','run.py','guest.py','baseline.json','RUNBOOK.md','dependencies.json')) and
        'tests/test_phase6_small008.py' in pins['files'],'SMALL008_PIN_MEMBERSHIP')
    # Dependencies are the already reviewed recovery001 source manifest. That
    # manifest itself is pinned by this package and all listed hashes must remain.
    deps=read(HERE/'dependencies.json')
    g.require(all(pins['files'].get(k)==v for k,v in deps['files'].items()),'SMALL008_DEPENDENCIES')
    binding.source_pins(pins)
    binding.identity.validate_pin(read(HERE.parent/'receipt-process-identity-v4/macos-python-pin.json'))
    g.require(sys.executable==recovery.legacy.PYTHON,'SMALL008_INTERPRETER')
    raw=KIT.read_bytes();g.require(len(raw)==g.KIT_BYTES and sha(raw)==g.KIT_SHA,'SMALL008_LOCAL_KIT')

def create_root(text):
    g.require(type(text) is str and text.strip(),'SMALL008_APPROVAL')
    g.require(not ROOT.exists() and not ROOT.is_symlink() and ROOT.parent.resolve()==ROOT.parent,'SMALL008_NEW_ROOT')
    pins_raw=binding.raw_file(PREP/'small008-source-pins.json');pins=g.parse(pins_raw);sources(pins)
    review=binding.raw_file(PLAN/'PHASE6-SMALL008-REVIEW.md')
    g.require(sha(review)==pins['review_sha256'],'SMALL008_REVIEW_HASH')
    ROOT.mkdir(mode=0o700)
    save(ROOT,'source-pins.json',pins_raw);save(ROOT,'EXECUTION-REVIEW.md',review)
    save(ROOT,'authorization.json',dict(approved=True,approval_text=text,owner=OWNER,evidence_root=str(ROOT),
        package_version=g.VERSION,review_sha256=sha(review),source_pins_sha256=sha(pins_raw),
        free_plan_only=True,receipt_start_limit=1,protective_stop_exact_pair=True,window_seconds=2700))

def authorized():
    binding.root_check(ROOT);a=read(ROOT/'authorization.json')
    g.require(a['approved'] is True and a['approval_text'].strip() and a['owner']==OWNER and
        a['evidence_root']==str(ROOT) and a['package_version']==g.VERSION and a['free_plan_only'] is True and
        a['receipt_start_limit']==1 and a['protective_stop_exact_pair'] is True and a['window_seconds']==2700,'SMALL008_AUTH')
    for n,k in [('source-pins.json','source_pins_sha256'),('EXECUTION-REVIEW.md','review_sha256')]:
        g.require(sha(binding.raw_file(ROOT/n))==a[k],'SMALL008_AUTH_HASH')
    g.require(not any((ROOT/n).exists() or (ROOT/n).is_symlink() for n in
        ('execution-started.json','operation-closure.json','closed-files-inventory.json')),'SMALL008_USED_ROOT')
    sources(read(ROOT/'source-pins.json'))

class Window:
    def __init__(self,wall=time.time,elapsed=time.monotonic):
        self.wall=wall;self.elapsed=elapsed;self.start_wall=wall();self.start_elapsed=elapsed()
        self.last=self.start_elapsed;self.stop_ms=(int(self.start_wall)+2700)*1000
    def check(self,minimum=0):
        w=self.wall();m=self.elapsed();e=m-self.start_elapsed
        g.require(e>=0 and 0<=m-self.last<=90 and abs((w-self.start_wall)-e)<=2,'SMALL008_CLOCK_GAP')
        left=self.stop_ms/1000-w
        g.require(left>minimum and 2700-e>minimum,'SMALL008_DEADLINE');self.last=m
        return {'utc_seconds':w,'elapsed_seconds':e,'remaining_seconds':left}
    def expression(self):return datetime.fromtimestamp(self.stop_ms/1000,timezone.utc).strftime('at(%Y-%m-%dT%H:%M:%S)')

class InvocationPending(Exception):pass

class API(recovery.API):
    def __init__(self,root,runner=recovery.legacy.bounded_cli):
        super().__init__(root,runner);self.used=set();self.commands=set();self.window=None
    def argv(self,service,operation,payload):
        result=super().argv(service,operation,payload)
        if service=='s3api':
            result[result.index('--endpoint-url')+1]='https://s3.eu-north-1.amazonaws.com'
            # AWS CLI custom get-object requires explicit options plus outfile;
            # the earlier attempt002 diagnostics rejected cli-input-json here.
            at=result.index('--cli-input-json');del result[at:at+2]
            for key,flag in [('Bucket','--bucket'),('Key','--key'),('VersionId','--version-id'),
                ('ExpectedBucketOwner','--expected-bucket-owner'),('ChecksumMode','--checksum-mode')]:
                result.extend([flag,payload[key]])
        return result
    def call(self,service,operation,payload,*,kind=None):
        allowed={(s,o,g.canonical(p)) for _,s,o,p in g.requests()}
        allowed.add(('ssm','describe-instance-information',g.canonical({'Filters':[{'Key':'InstanceIds','Values':[g.RECEIPT]}]})))
        mutation=operation in ('update-stack','start-instances','send-command')
        if mutation:
            g.require(self.identity_ok and self.protection_armed and kind and kind not in self.used,'SMALL008_MUTATION_ONCE')
            intent=read(self.root/(kind+'-intent.json'))
            g.require(intent['service']==service and intent['operation']==operation and intent['payload']==payload,'SMALL008_INTENT')
            if operation=='update-stack':
                c=read(self.root/'window.json')
                g.require(service=='cloudformation' and kind=='schedule' and payload==g.update_request(c['stop_expression'],c['token']),'SMALL008_UPDATE_SCOPE')
            elif operation=='start-instances':
                g.require(service=='ec2' and kind=='receipt-start' and payload=={'InstanceIds':[g.RECEIPT]},'SMALL008_START_SCOPE')
            else:
                g.require(service=='ssm' and payload['DocumentName']=='AWS-RunShellScript' and payload['DocumentVersion']=='1' and
                    payload['InstanceIds']==[g.RECEIPT] and (kind in ('admission','timer','preparation','logs-inventory') or
                    (kind.startswith('logs-chunk-') and kind[11:].isdigit() and int(kind[11:])<44)),'SMALL008_SSM_SCOPE')
            self.used.add(kind)  # unknown outcomes cannot be retried even if recording fails
        elif operation=='get-command-invocation':
            g.require(service=='ssm' and set(payload)=={'CommandId','InstanceId'} and payload['InstanceId']==g.RECEIPT and
                payload['CommandId'] in self.commands,'SMALL008_COMMAND_BINDING')
        elif operation=='get-object':
            g.require(service=='s3api' and payload==guest.kit_request(),'SMALL008_KIT_SCOPE')
        else:g.require((service,operation,g.canonical(payload)) in allowed,'SMALL008_API_ALLOWLIST')
        if self.window:self.window.check()
        self.sequence+=1;g.require(self.sequence<=1600,'SMALL008_API_BOUND')
        name=f'api-{self.sequence:04d}-{operation}';argv=self.argv(service,operation,payload)
        if operation=='get-object':
            out=self.root/'kit-exact-version.tar.gz';g.require(not out.exists() and not out.is_symlink(),'SMALL008_KIT_NEW')
            argv.append(str(out))
        self.record(name+'-request.json',dict(service=service,operation=operation,payload=payload,kind=kind,
            utc_seconds=time.time(),aws_max_attempts=1,timeout_seconds=30))
        try:r=self.runner(argv,dict(os.environ,AWS_MAX_ATTEMPTS='1',AWS_RETRY_MODE='standard'),timeout=30)
        except BaseException as exc:
            if isinstance(exc,recovery.legacy.CallUnknown):
                self.best_record(name+'-stdout.json',exc.stdout);self.best_record(name+'-stderr.txt',exc.stderr)
            self.best_record(name+'-unknown.json',dict(error_type=type(exc).__name__,retry_allowed=False));raise
        self.record(name+'-stdout.json',r.stdout);self.record(name+'-stderr.txt',r.stderr)
        self.record(name+'-result.json',dict(returncode=r.returncode,utc_seconds=time.time()))
        if r.returncode and operation=='get-command-invocation' and b'(InvocationDoesNotExist)' in r.stderr:
            raise InvocationPending('SSM_READ_NOT_VISIBLE_YET')
        g.require(r.returncode==0,'SMALL008_API_FAILED')
        value=g.parse(r.stdout) if r.stdout.strip() else {}
        if operation=='get-caller-identity':
            self.identity_ok=value==g.baseline()['data']['identity'];g.require(self.identity_ok,'SMALL008_PRINCIPAL')
        if operation=='send-command':
            c=value['Command'];g.require(c['InstanceIds']==[g.RECEIPT] and c['DocumentName']=='AWS-RunShellScript','SMALL008_COMMAND_RESPONSE')
            g.require(type(c['CommandId']) is str and len(c['CommandId'])==36 and c['CommandId'] not in self.commands,'SMALL008_COMMAND_ID')
            self.commands.add(c['CommandId'])
        if self.window:self.window.check()
        return value

def operate(api,window,*,sleep=time.sleep,repin=lambda:None):
    outcome={'status':'SMALL008_UNRESOLVED','phase6_complete':False,'receipt_runtime_qualified':False,
        'automatic_next_step':False,'original_recovery001_outcome':'RECOVERY_UNRESOLVED'}
    api.window=window;count=0;head='0'*64;last_observation=window.start_elapsed;phase='before'
    stop=window.expression();token=sha(g.canonical({'root':str(ROOT),'start':window.start_wall,'version':g.VERSION}).encode())
    def observe():
        nonlocal count,head,last_observation
        window.check()
        inst=api.call('ec2','describe-instances',{'InstanceIds':g.PAIR});states=g.old.pair_state(inst)
        s=g.old.one(api.call('cloudformation','describe-stacks',{'StackName':g.STACK}),'Stacks')
        stamp=window.check();now=window.elapsed()
        g.require(0<=now-last_observation<=90,'SMALL008_OBSERVATION_GAP');last_observation=now
        count+=1;row={'sequence':count,'previous_sha256':head,'states':states,'stack_status':s['StackStatus'],'clock':stamp,'atomic':False}
        head=sha(g.canonical(row).encode());api.record(f'observation-{count:04d}.json',dict(row,sha256=head))
        g.require(s['StackId']==g.STACK and states[g.SOURCE]=='stopped','SMALL008_OBSERVER_SCOPE')
        allowed=('stopped',) if phase in ('before','schedule') else ('pending','running') if phase=='boot' else ('running',)
        g.require(states[g.RECEIPT] in allowed,'SMALL008_OBSERVER_STATE')
        statuses=('UPDATE_ROLLBACK_COMPLETE',) if phase=='before' else ('UPDATE_ROLLBACK_COMPLETE','UPDATE_IN_PROGRESS','UPDATE_COMPLETE_CLEANUP_IN_PROGRESS','UPDATE_COMPLETE') if phase=='schedule' else ('UPDATE_COMPLETE',)
        g.require(s['StackStatus'] in statuses,'SMALL008_OBSERVER_STACK')
        return states,s['StackStatus']
    def collect(label,minimum):
        start=time.time();data={}
        for n,(key,s,o,p) in enumerate(g.requests()):
            window.check(minimum);data[key]=api.call(s,o,p)
            if key=='identity':api.arm_protection()
            if n%4==3:observe()
        window.check(minimum);g.require(time.time()-start<=120,'SMALL008_SNAPSHOT_AGE')
        api.record(label+'-snapshot.json',dict(started_utc=start,finished_utc=time.time(),data=data,atomic=False))
        return data
    def mutate(kind,s,o,p,minimum):
        repin();observe();window.check(minimum)
        api.record(kind+'-intent.json',{'service':s,'operation':o,'payload':p,'retry_allowed':False})
        return api.call(s,o,p,kind=kind)
    def command(kind,payload,minimum,polls):
        r=mutate(kind,'ssm','send-command',payload,minimum)
        cid=r['Command']['CommandId'];api.record(kind+'-command.json',{'command_id':cid,'instance_id':g.RECEIPT})
        for _ in range(polls):
            observe();window.check(90)
            try:v=api.call('ssm','get-command-invocation',{'CommandId':cid,'InstanceId':g.RECEIPT})
            except InvocationPending:
                sleep(5);continue  # bounded read retry only; never resend the command
            g.require(v['CommandId']==cid and v['InstanceId']==g.RECEIPT and v['DocumentName']=='AWS-RunShellScript','SMALL008_INVOCATION')
            if v['Status'] in ('Success','Failed','Cancelled','TimedOut'):
                api.record(kind+'-result.json',v)
                g.require(v['Status']=='Success' and v['ResponseCode']==0,'SMALL008_COMMAND_FAILED')
                return v
            g.require(v['Status'] in ('Pending','InProgress','Delayed','Cancelling'),'SMALL008_COMMAND_STATUS')
            sleep(5)
        raise ValueError('SMALL008_COMMAND_POLL_BOUND')
    try:
        api.record('window.json',{'start_utc_seconds':window.start_wall,'stop_utc_ms':window.stop_ms,
            'stop_expression':stop,'token':token,'window_seconds':2700})
        data=collect('before',2520);api.record('preflight.json',g.snapshot(data))
        observe();window.check(2500)
        kit=api.call('s3api','get-object',guest.kit_request());guest.verify_kit(kit,(api.root/'kit-exact-version.tar.gz').read_bytes())
        api.record('kit-verified.json',{'version':g.KIT_VERSION,'sha256':g.KIT_SHA,'bytes':g.KIT_BYTES})
        phase='schedule'
        r=mutate('schedule','cloudformation','update-stack',g.update_request(stop,token),2450)
        g.require(r['StackId']==g.STACK,'SMALL008_UPDATE_RESPONSE')
        for _ in range(80):
            _,status=observe();window.check(2350)
            if status=='UPDATE_COMPLETE':break
            sleep(3)
        else:raise ValueError('SMALL008_SCHEDULE_POLL_BOUND')
        after=collect('schedule',2300);api.record('schedule-verified.json',g.snapshot(after,stop=stop))
        g.schedule_events(after['events'],token,window.start_wall)
        r=mutate('receipt-start','ec2','start-instances',{'InstanceIds':[g.RECEIPT]},2300)
        rows=r['StartingInstances'];g.require(len(rows)==1 and rows[0]['InstanceId']==g.RECEIPT and
            rows[0]['PreviousState']['Name']=='stopped' and rows[0]['CurrentState']['Name']=='pending','SMALL008_START_RESPONSE')
        phase='boot'
        for _ in range(24):
            states,_=observe();window.check(2230)
            s=api.call('ssm','describe-instance-information',{'Filters':[{'Key':'InstanceIds','Values':[g.RECEIPT]}]})
            rows=s['InstanceInformationList'];g.require(not s.get('NextToken') and len(rows)<=1,'SMALL008_SSM_SET')
            if rows:g.require(rows[0]['InstanceId']==g.RECEIPT,'SMALL008_SSM_ID')
            if states[g.RECEIPT]=='running' and rows and rows[0]['PingStatus']=='Online':break
            sleep(2)
        else:raise ValueError('SMALL008_READINESS_BOUND')
        phase='running'
        data=collect('running',2200);g.snapshot(data,stop=stop,allowed=('running',))
        a=guest.answer(command('admission',guest.admission(),2180,18));g.require(a['status']=='FRESH_TARGET_ADMITTED','SMALL008_ADMISSION')
        timer=guest.answer(command('timer',guest.generator.timer_request(window.stop_ms),2160,24))
        guest.timer_ok(timer,window.stop_ms)
        payload=guest.generator.request('receipt',g.RECEIPT,g.KIT_VERSION,attempt='receipt-002',
            stop_utc_ms=window.stop_ms,expected_timer_us=timer['timer']['next_us'])
        subprocess.run(['/bin/bash','-n'],input=payload['Parameters']['commands'][0],text=True,check=True,timeout=5)
        preparation=command('preparation',payload,2080,410)
        outcome['host_preparation_command_success']=True
        inventory=guest.answer(command('logs-inventory',guest.log_request(None),120,18));guest.inventory_ok(inventory)
        chunks=[]
        for i in range(inventory['chunk_count']):
            chunks.append(guest.answer(command('logs-chunk-'+str(i),guest.log_request(i),120,18)))
        logs=guest.decode(inventory,chunks)
        api.record('receipt-operation-files.json',logs)
        api.record('receipt-exact-log-verification.json',{'status':'ALL_OPERATION_FILES_BYTE_HASH_VERIFIED','files':len(logs),
            'inventory_sha256':sha(g.canonical(inventory).encode())})
        after=collect('after',90);outcome['configuration']=g.snapshot(after,stop=stop,allowed=('running',))
        outcome['status']='HOST_PREPARED_PENDING_INDEPENDENT_REVIEW'
    except BaseException as exc:
        outcome.update(error_type=type(exc).__name__,error=str(exc)[:500])
    # Completion AND failure stop the pair once. Prearmed stop survives storage
    # failure; final reads are bounded independently of the expired forward window.
    if api.identity_ok and api.protection_armed:
        try:api.protective_stop(outcome['status'])
        except BaseException as exc:outcome['stop_error']=type(exc).__name__
        for n in range(4):
            try:
                # Base emergency helper records exclusive names. Use it once;
                # subsequent final observations have unique best-effort names.
                if n==0:v=api.emergency_call('ec2','describe-instances',{'InstanceIds':g.PAIR})
                else:
                    api.window=None;v=api.call('ec2','describe-instances',{'InstanceIds':g.PAIR})
                states=g.old.pair_state(v);outcome['final_observed_states']=states
                if set(states.values())=={'stopped'}:break
                g.require(set(states.values())<= {'running','pending','stopping','stopped'},'SMALL008_FINAL_STATES')
            except BaseException as exc:outcome['final_state_error']=type(exc).__name__;break
            if n<3:sleep(5)
        try:
            v=api.emergency_call('cloudformation','describe-stacks',{'StackName':g.STACK})
            outcome['final_stack_status']=g.old.one(v,'Stacks')['StackStatus']
        except BaseException as exc:outcome['final_stack_error']=type(exc).__name__
    if outcome.get('final_observed_states')!={i:'stopped' for i in g.PAIR} or outcome.get('final_stack_status')!='UPDATE_COMPLETE' or api.audit_errors:
        outcome['status']='SMALL008_UNRESOLVED'
    outcome.update(observation_count=count,observation_head=head,protective_stop_attempted=api.stop_used,audit_errors=api.audit_errors,
        continuous_observation_proven=False,cloud_stop_not_guaranteed=True)
    try:api.record('outcome.json',outcome)
    except BaseException as exc:outcome.update(status='SMALL008_UNRESOLVED',outcome_write_error=type(exc).__name__)
    return outcome

def main():
    p=argparse.ArgumentParser(description=__doc__);c=p.add_mutually_exclusive_group(required=True)
    c.add_argument('--create-root',action='store_true');c.add_argument('--run',action='store_true')
    p.add_argument('--approved',action='store_true');p.add_argument('--approval-text');a=p.parse_args()
    raise ValueError('SMALL008_PAUSED_BY_OWNER_2026_10_23_NO_RUNTIME_AUTHORIZED')
    g.require(a.approved,'SMALL008_EXPLICIT_APPROVAL')
    if a.create_root:create_root(a.approval_text);return
    authorized();save(ROOT,'execution-started.json',{'utc_seconds':time.time(),'pid':os.getpid(),'version':g.VERSION})
    result=operate(API(ROOT),Window(),repin=lambda:sources(read(ROOT/'source-pins.json')))
    print(g.canonical(result),flush=True)
    if result['status']!='HOST_PREPARED_PENDING_INDEPENDENT_REVIEW':sys.exit(2)

if __name__=='__main__':main()
