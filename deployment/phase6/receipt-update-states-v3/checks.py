"""Attempt006 read-only preflight and post-stop reconciliation; no AWS on import.

Outputs are operational readbacks pending independent review, never phase/pilot
completion. No mutation, retries, root creation or closed-root writes occur here.
"""
from pathlib import Path
import copy
import hashlib
import json
import re
import sys
import time

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import context as m
BASELINE_SHA='3f67e421b5ed8d7375d05a040afd5a2ae91c7ed214d5813d0f80fc347d942303'
require=m.live.control.require


def baseline():
    raw=m.live.read_regular(HERE/'baseline005.json',1024*1024)
    require(hashlib.sha256(raw).hexdigest()==BASELINE_SHA)
    return m.v2.parse(raw)['data']


def read(path):
    return m.v2.parse(m.live.read_regular(path,1024*1024))


def singleton(response,key):
    require(not response.get('NextToken') and len(response[key])==1)
    return response[key][0]


def parameters(stack):
    rows=m.guard.unique(stack['Parameters'],'ParameterKey')
    require(set(rows)=={'LaunchCompute','StopAtUtc'})
    return {k:v['ParameterValue'] for k,v in rows.items()}


def schedule_identity(value):
    v=copy.deepcopy(value);v.pop('LastModificationDate',None);v.pop('ScheduleExpression',None)
    v['Target']['Input']=m.v2.parse(v['Target']['Input'])
    return v


def security_identity(value):
    g=copy.deepcopy(singleton(value,'SecurityGroups'))
    g['Tags']={k:v['Value'] for k,v in m.guard.unique(g['Tags'],'Key').items()}
    return g


def requests():
    b=baseline();jobs=[('identity','sts','get-caller-identity',{}),
        ('stack','cloudformation','describe-stacks',dict(StackName=m.STACK)),
        ('resources','cloudformation','list-stack-resources',dict(StackName=m.STACK)),
        ('template','cloudformation','get-template',dict(StackName=m.STACK,TemplateStage='Original')),
        ('schedule','scheduler','get-schedule',dict(Name='mie-p6-qualification-001-stop')),
        ('stack_policy','cloudformation','get-stack-policy',dict(StackName=m.STACK))]
    for name in ('policy','source_policy'):
        jobs.append((name,'iam','get-role-policy',{k:b[name][k] for k in ('RoleName','PolicyName')}))
    jobs.extend([('security','ec2','describe-security-groups',dict(GroupIds=['sg-04209bb68f81f49b9'])),
        ('instances','ec2','describe-instances',dict(InstanceIds=[m.SOURCE,m.RECEIPT])),
        ('volumes','ec2','describe-volumes',dict(VolumeIds=list(m.guard.VOLUMES.values()))),
        ('credits','ec2','describe-instance-credit-specifications',dict(InstanceIds=[m.SOURCE,m.RECEIPT]))])
    return jobs


def collect(prefix):
    # Sequential, fresh read batch. No reused responses or automatic retry.
    start=time.time_ns()//1000000;data={};files={}
    for name,service,operation,payload in requests():
        label=name.replace('_','-')
        data[name]=m.call(prefix+'-'+label,service,operation,payload)
        files[name]=prefix+'-'+label+'-stdout.json'
    data['stack_confirm']=m.call(prefix+'-stack-confirm','cloudformation','describe-stacks',dict(StackName=m.STACK))
    data['instances_confirm']=m.call(prefix+'-instances-confirm','ec2','describe-instances',dict(InstanceIds=[m.SOURCE,m.RECEIPT]))
    files.update(stack_confirm=prefix+'-stack-confirm-stdout.json',instances_confirm=prefix+'-instances-confirm-stdout.json')
    return data,dict(started_utc_ms=start,finished_utc_ms=time.time_ns()//1000000,
        sequential=True,atomic_snapshot=False,reused_responses=[],response_files=files)


def validate_snapshot(data,*,allowed_stops,preflight):
    b=baseline();identity=data['identity']
    require(identity['Account']==m.ACCOUNT and identity['Arn']=='arn:aws:iam::'+m.ACCOUNT+':root')
    stack=singleton(data['stack'],'Stacks');confirm=singleton(data['stack_confirm'],'Stacks')
    statuses={'UPDATE_COMPLETE'} if preflight else {'UPDATE_COMPLETE','UPDATE_ROLLBACK_COMPLETE'}
    require(stack['StackId']==confirm['StackId']==m.STACK and stack['StackStatus'] in statuses
            and confirm['StackStatus']==stack['StackStatus'] and parameters(confirm)==parameters(stack))
    params=parameters(stack)
    require(params['LaunchCompute']=='yes' and params['StopAtUtc'] in allowed_stops)
    resource_rows=m.guard.unique(data['resources']['StackResourceSummaries'],'LogicalResourceId')
    require(not data['resources'].get('NextToken'))
    require({k:r['PhysicalResourceId'] for k,r in resource_rows.items()}==m.v2.pinned('resource-bindings.json'))
    require(all(r['ResourceStatus'] in ('CREATE_COMPLETE','UPDATE_COMPLETE') for r in resource_rows.values()))
    rows=m.guard.unique([i for r in data['instances']['Reservations'] for i in r['Instances']],'InstanceId')
    receipt_type=rows[m.RECEIPT]['InstanceType']
    require(receipt_type in ({'t4g.small'} if preflight else {'t4g.small','t4g.medium'}))
    stage='before_resize' if receipt_type=='t4g.small' else 'after_resize'
    for key in ('instances','instances_confirm'):
        m.guard.validate_pair(data[key],data['volumes'],data['credits'],stage=stage)
    template=m.VARIANT.parent/'qualification-v1.json' if stage=='before_resize' else m.VARIANT/'qualification-v1.json'
    require(m.body(data['template'])==json.loads(template.read_bytes()))
    require(schedule_identity(data['schedule'])==schedule_identity(b['schedule'])
            and data['schedule']['ScheduleExpression']==params['StopAtUtc'])
    m.v2.validate_policy(data['stack_policy'])
    for name in ('policy','source_policy'):
        current=data[name];expected=b[name]
        require(current['RoleName']==expected['RoleName'] and current['PolicyName']==expected['PolicyName'])
        require(m.v2.canonical(m.v2.policy_document(current['PolicyDocument']))==
                m.v2.canonical(m.v2.policy_document(expected['PolicyDocument'])))
    require(security_identity(data['security'])==security_identity(b['security']))
    return dict(states={m.SOURCE:'stopped',m.RECEIPT:'stopped'},receipt_type=receipt_type,
        stack_status=stack['StackStatus'],parameters=params,resources={k:r['PhysicalResourceId'] for k,r in resource_rows.items()},
        exact_stack_policy_verified=True,source_stopped_now=True)


def preflight():
    m.pins()
    require(not (m.ROOT/'launch-contract.json').exists() and not list((m.ROOT/'operator').iterdir()))
    data,batch=collect('preflight2')
    old=parameters(singleton(baseline()['stack'],'Stacks'))['StopAtUtc']
    answer=validate_snapshot(data,allowed_stops={old},preflight=True)
    result=dict(answer,status='READ_ONLY_PREFLIGHT_PASS',batch=batch,capacity_proven=False,runtime_accepted=False)
    m.write('preflight-summary.json',result)
    return result


def frozen_contract(root):
    c=read(root/'launch-contract.json')
    m.live.operator.contract(c,c['not_before_utc_ms'])
    require(c['stop_utc_ms']-c['not_before_utc_ms']==2700000 and c['stack_id']==m.STACK
        and c['operator_arn']=='arn:aws:iam::'+m.ACCOUNT+':root' and c['template_sha256']==m.v2.TEMPLATE_SHA
        and c['runtime_bundle_sha256']==m.SHA)
    return c


def request_audit(root):
    counts={name:0 for name in ('update-stack','create-change-set','execute-change-set','start-instances','send-command')}
    raw_requests=[]
    for path in sorted(root.glob('*-request.json')):
        r=read(path);op=r.get('operation');service=r.get('service')
        if not service:continue  # Local process-launch record, not an AWS request.
        require(op not in ('set-stack-policy','put-object','delete-stack','terminate-instances'))
        if op in counts:
            expected_service={'update-stack':'cloudformation','create-change-set':'cloudformation',
                'execute-change-set':'cloudformation','start-instances':'ec2','send-command':'ssm'}[op]
            require(service==expected_service)
            counts[op]+=1;payload=r['payload']
            if op in ('start-instances','send-command'):require(payload['InstanceIds']==[m.RECEIPT])
            else:require(payload['StackName']==m.STACK)
            if op=='create-change-set':require(payload['ChangeSetName']=='mie-p6-receipt-medium-v2-006')
            if op=='execute-change-set':require(re.fullmatch(r'arn:aws:cloudformation:eu-north-1:989879505232:changeSet/mie-p6-receipt-medium-v2-006/[0-9a-f-]{36}',payload['ChangeSetName']) is not None)
        else:require((service,op) in {(s,o) for _,s,o,_ in requests()}|{('s3api','get-object'),
            ('cloudformation','describe-change-set'),('ssm','get-command-invocation'),('ssm','describe-instance-information')})
        raw_requests.append(path.name)
    require(all(counts[k]<=1 for k in counts if k!='send-command') and counts['send-command']<=48)
    return dict(counts=counts,request_files=raw_requests,unknown_files=sorted(p.name for p in root.glob('*-unknown.json')),
                scope='Recorded local requests only; not an account-wide AWS audit')


def observer_summary(root):
    if not (root/'launch-contract.json').exists():
        require(not list((root/'operator').glob('*.blob')))
        return dict(status='NOT_STARTED',history_verified=False,source_stopped_in_all_observations=None)
    c=frozen_contract(root);folder=m.live.root_check(root/'operator')
    header=read(folder/'writer.json')
    require(header==dict(schema_version='p6.object_store.v1',owner=m.ACCOUNT,
        run_id=hashlib.sha256(m.v2.canonical(c).encode()).hexdigest(),max_bytes=16*1024*1024))
    events=[]
    for path in folder.glob('*.blob'):
        raw=m.live.read_regular(path,65536);h=hashlib.sha256(raw).hexdigest();require(h==path.stem)
        events.append((m.v2.parse(raw),h))
    events.sort(key=lambda e:e[0]['sequence']);require(2<=len(events)<=4096)
    previous='0'*64;observations=[];last_clock=None;armed_clock=None;clock_valid=True;reason=None;stop_requests=[];gaps=0
    for i,(event,h) in enumerate(events,1):
        require(event['schema_version']==m.live.progress.EVENT_SCHEMA and event['sequence']==i and event['previous_hash']==previous)
        kind=event['kind'];d=event['data'];previous=h
        require(kind in {'armed','observation','observation_unavailable','stop_requested','stop_response_received','stop_unresolved','finished'})
        if i==1:
            require(kind=='armed' and d['contract']==c and d['control_version']==m.live.progress.VERSION
                    and d['qualification_only'] is True and d['observed_instances']=={m.SOURCE:'stopped',m.RECEIPT:'stopped'})
        elif i==len(events):
            require(kind=='finished' and d['runtime_accepted'] is False);reason=d['reason']
        else:require(kind not in ('armed','finished'))
        if kind in ('armed','observation'):
            pair=d.get('instances',d.get('observed_instances'));require(set(pair)=={m.SOURCE,m.RECEIPT})
            if kind=='observation':observations.append(pair)
            wall,elapsed=d['utc_ms'],d['continuous_ns']
            valid=type(wall) is int and type(elapsed) is int and elapsed>=0 and (kind=='armed' or d['clock_available'] is True)
            if kind=='armed':
                require(valid and c['not_before_utc_ms']<=wall<c['stop_utc_ms'])
                armed_clock=(wall,elapsed)
            if valid and last_clock:
                valid=wall>=last_clock[0] and elapsed>last_clock[1] and abs((wall-last_clock[0])*1000000-(elapsed-last_clock[1]))<=2000000000
            if valid:
                valid=abs((wall-armed_clock[0])*1000000-(elapsed-armed_clock[1]))<=2000000000
            clock_valid=clock_valid and valid
            if valid:last_clock=(wall,elapsed)
        if kind=='observation_unavailable':clock_valid=False;gaps+=1
        if kind=='stop_requested':
            require(d['instance_id'] in (m.SOURCE,m.RECEIPT) and d['instance_id'] not in stop_requests);stop_requests.append(d['instance_id'])
    lines=m.live.read_regular(root/'operator-stdout.txt',1024*1024).decode().splitlines()
    require(len(lines)==2 and m.v2.parse(lines[0])['status']=='ARMED')
    end=m.v2.parse(lines[-1]);require(end['final_event_hash']==previous and end['status']==events[-1][0]['data']['status'])
    require(end['runtime_accepted'] is False and m.live.read_regular(root/'operator-stderr.txt',1024*1024)==b'')
    require(bool(observations))
    return dict(status=end['status'],history_verified=True,event_count=len(events),final_hash=previous,
        source_stopped_in_all_observations=all(p[m.SOURCE]=='stopped' for p in observations),
        observation_gaps=gaps,continuous_source_state_proven=False,clock_history_valid=clock_valid,
        recorded_reason=reason,stop_requests=stop_requests)


def preparation_summary(root):
    requested=(root/'receipt-preparation-send-once-request.json').exists() or (root/'receipt-preparation-command.json').exists()
    if not requested:
        status='PREPARATION_EVIDENCE_CONFLICT' if (root/'receipt-preparation-result.json').exists() else 'NO_HOST_PREPARATION_COMMAND'
        return dict(status=status)
    result=root/'receipt-preparation-result.json'
    if not result.exists():return dict(status='PREPARATION_OUTCOME_UNRESOLVED')
    r=read(result)
    try:
        command=read(root/'receipt-preparation-command.json')
        require(r['CommandId']==command['command_id'] and r['InstanceId']==command['instance_id']==m.RECEIPT)
    except (ValueError,KeyError,FileNotFoundError,NotADirectoryError):
        return dict(status='PREPARATION_RESULT_IDENTITY_UNVERIFIED')
    if r.get('Status') not in ('Success','Failed','Cancelled','TimedOut'):
        return dict(status='PREPARATION_OUTCOME_UNRESOLVED')
    if r.get('Status')!='Success':
        return dict(status='PREPARATION_'+r['Status'].upper(),response_code=r.get('ResponseCode'))
    try:
        require(r.get('ResponseCode')==0)
        reported=m.v2.parse(r['StandardOutputContent'].splitlines()[-1])
        require(reported['status']=='HOST_PREPARED_PENDING_REVIEW' and reported['instance_id']==m.RECEIPT and reported['instance_type']=='t4g.medium')
        inv_result=read(root/'receipt-operations-inventory-result.json')
        inv_command=read(root/'receipt-operations-inventory-command.json')
        require(inv_result['CommandId']==inv_command['command_id'] and inv_command['instance_id']==m.RECEIPT)
        require(inv_result['Status']=='Success' and inv_result['ResponseCode']==0 and inv_result['InstanceId']==m.RECEIPT)
        inventory=m.v2.parse(inv_result['StandardOutputContent'].splitlines()[-1])
        require(inventory['status']=='OPERATIONS_INVENTORY')
        expected=m.guard.unique(inventory['files'],'name');folder=m.live.root_check(root/'receipt-operations-exact')
        require({p.name for p in folder.iterdir()}==set(expected) and 0<len(expected)<=128)
        for name,item in expected.items():
            require(name not in ('.','..') and '/' not in name)
            raw=m.live.read_regular(folder/name,4*1024*1024)
            require(len(raw)==item['bytes'] and hashlib.sha256(raw).hexdigest()==item['sha256'])
        require(read(folder/'prepared.json')==reported)
        require(read(root/'receipt-exact-log-verification.json')['status']=='ALL_OPERATION_FILES_BYTE_HASH_VERIFIED')
    except (ValueError,KeyError,IndexError,FileNotFoundError,NotADirectoryError):
        return dict(status='PREPARATION_REPORTED_SUCCESS_EVIDENCE_INCOMPLETE')
    return dict(status='HOST_PREPARED_PENDING_INDEPENDENT_REVIEW',exact_log_files=len(expected))


def reconcile(root,data):
    b=baseline();old=parameters(singleton(b['stack'],'Stacks'))['StopAtUtc'];allowed={old}
    audit=request_audit(root);counts=audit['counts'];new=None
    if (root/'launch-contract.json').exists():
        c=frozen_contract(root)
        new=m.datetime.fromtimestamp(c['stop_utc_ms']//1000,m.timezone.utc).strftime('at(%Y-%m-%dT%H:%M:%S)')
    if counts['update-stack']:
        require(new is not None)
        req=read(root/'schedule-update-once-request.json')
        require(req['operation']=='update-stack' and req['payload']['UsePreviousTemplate'] is True)
        p={x['ParameterKey']:x for x in req['payload']['Parameters']}
        require(p=={'LaunchCompute':dict(ParameterKey='LaunchCompute',UsePreviousValue=True),
            'StopAtUtc':dict(ParameterKey='StopAtUtc',ParameterValue=new)})
        allowed.add(new)
    result=validate_snapshot(data,allowed_stops=allowed,preflight=False)
    if counts['execute-change-set'] or counts['start-instances'] or counts['send-command']:
        require(new is not None and result['parameters']['StopAtUtc']==new)
    if result['receipt_type']=='t4g.medium':require(counts['execute-change-set']==1)
    try:observer=observer_summary(root)
    except (ValueError,KeyError,IndexError,FileNotFoundError,NotADirectoryError,UnicodeError) as exc:
        observer=dict(status='UNVERIFIED',history_verified=False,source_stopped_in_all_observations=None,error_type=type(exc).__name__)
    result.update(status='ATTEMPT006_STOPPED_PENDING_REVIEW',observer=observer,preparation=preparation_summary(root),request_audit=audit,
        deadline_readback='NEW_FROZEN_DEADLINE' if new is not None and result['parameters']['StopAtUtc']==new else 'OLD005_DEADLINE_RETAINED',
        runtime_accepted=False,phase6_complete=False,automatic_retry_allowed=False)
    return result


def closure():
    # Read-only reconciliation must remain usable after pin/preparation failure.
    m.live.root_check(m.ROOT)
    prefix='closure-'+m.stamp();data,batch=collect(prefix)
    try:result=reconcile(m.ROOT,data)
    except (ValueError,KeyError,IndexError,FileNotFoundError,NotADirectoryError) as exc:
        result=dict(status='RECONCILIATION_UNRESOLVED',error_type=type(exc).__name__,runtime_accepted=False,phase6_complete=False)
    result['batch']=batch
    m.write(prefix+'-summary.json',result)
    return result


def main(argv=None):
    args=sys.argv[1:] if argv is None else argv
    require(__debug__ and len(args)==1 and args[0] in ('preflight','closure'))
    result=preflight() if args[0]=='preflight' else closure()
    print(json.dumps(result,sort_keys=True))
    return 0 if result['status'] in ('READ_ONLY_PREFLIGHT_PASS','ATTEMPT006_STOPPED_PENDING_REVIEW') else 2


if __name__=='__main__':raise SystemExit(main())
