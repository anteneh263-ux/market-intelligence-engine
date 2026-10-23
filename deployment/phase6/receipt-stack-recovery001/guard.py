"""Recovery001 offline checks. No AWS, stores, mutation or work on import."""
import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSION = 'receipt-stack-recovery.001.v1'
ACCOUNT = '989879505232'
REGION = 'eu-north-1'
STACK = 'arn:aws:cloudformation:eu-north-1:989879505232:stack/mie-p6-qualification-001/c0769b00-caf6-11f1-8e71-0affdc797e93'
RECEIPT = 'i-0c5a2a75ca70db228'
SOURCE = 'i-08e89ad2e3feedb8a'
PAIR = [SOURCE, RECEIPT]
AMI = 'ami-04e0c7320ad3a3a48'
BASELINE_SHA = '86385cb0a0293777ae34099bcbaa3bde4601ce88768bb62f93700aa93a40625c'
OLD_TOKEN = '83136e31b86ba8c53be7b6e847741cd1c34ac0f8b8ca42dd5e00f4a4d5c66b3d'


def require(ok, reason='CONTRACT'):
    if not ok:
        raise ValueError('RECOVERY001_REFUSED:'+reason)


def load_module(name, path):
    spec=importlib.util.spec_from_file_location(name, path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def parse(raw):
    def pairs(rows):
        d={}
        for k,v in rows:
            require(k not in d,'DUPLICATE_JSON_KEY');d[k]=v
        return d
    def invalid(value):
        raise ValueError('NONFINITE_JSON')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)


def baseline():
    raw=(HERE/'baseline007.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest()==BASELINE_SHA,'BASELINE_HASH')
    return parse(raw)['data']


def epoch(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    require(d.tzinfo is not None,'NAIVE_TIME')
    return d.timestamp()


def one(value, key):
    require(not value.get('NextToken') and len(value[key])==1,'SINGLETON')
    return value[key][0]


def unique(rows, key):
    out={}
    for row in rows:
        require(row[key] not in out,'DUPLICATE_ROW');out[row[key]]=row
    return out


def stack_identity(value):
    # Status/time/drift metadata changes during recovery, authority does not.
    ignore={'StackStatus','StackStatusReason','LastUpdatedTime','LastOperations','DriftInformation'}
    return {k:v for k,v in value.items() if k not in ignore}


def resource_identity(value):
    require(not value.get('NextToken'),'INCOMPLETE_RESOURCES')
    rows=unique(value['StackResourceSummaries'],'LogicalResourceId')
    return rows,{k:(r['PhysicalResourceId'],r['ResourceType']) for k,r in rows.items()}


def body(value):
    x=value['TemplateBody'];return parse(x) if isinstance(x,str) else x


def eligibility(value):
    expected={e['EventId']:e for e in baseline()['events']['StackEvents'] if e.get('ClientRequestToken')==OLD_TOKEN}
    rows=unique(value['StackEvents'],'EventId')
    require(all(rows.get(k)==v for k,v in expected.items()),'ROLLBACK_EVENT_BINDING')
    terminal='e9043310-ce53-11f1-a8a8-06ffd458fb75'
    cutoff=epoch(expected[terminal]['Timestamp'])
    require(all(e['StackId']==STACK and epoch(e['Timestamp'])<=cutoff for e in rows.values()),'NEW_OPERATION')
    require(max(rows.values(),key=lambda e:epoch(e['Timestamp']))['EventId']==terminal,'LATEST_EVENT')
    # The exact archived sequence contains rollback IN_PROGRESS, then the sole
    # ReceiptInstance rollback failure. A forward failure alone cannot pass.
    return {'status':'SINGLE_RECEIPT_ROLLBACK_FAILURE_VERIFIED','terminal_event_id':terminal}


def pair_state(value):
    require(not value.get('NextToken'),'INCOMPLETE_PAIR')
    rows=[]
    for reservation in value['Reservations']:
        require(reservation['OwnerId']==ACCOUNT,'PAIR_OWNER')
        rows.extend(reservation['Instances'])
    found=unique(rows,'InstanceId');require(set(found)==set(PAIR),'EXACT_PAIR')
    for ident,instance in found.items():
        tags={k:v['Value'] for k,v in unique(instance['Tags'],'Key').items()}
        require(tags.get('aws:cloudformation:stack-id')==STACK and
                tags.get('aws:cloudformation:logical-id')==('SourceInstance' if ident==SOURCE else 'ReceiptInstance'), 'PAIR_TAGS')
    return {k:v['State']['Name'] for k,v in found.items()}


def snapshot(data, *, phase):
    require(phase in ('before','after'),'PHASE')
    b=baseline()
    require(data['identity']==b['identity'],'PRINCIPAL')
    want='UPDATE_ROLLBACK_FAILED' if phase=='before' else 'UPDATE_ROLLBACK_COMPLETE'
    old=one(b['stack'],'Stacks')
    for name in ('stack','stack_confirm'):
        s=one(data[name],'Stacks')
        require(s['StackId']==STACK and s['StackStatus']==want,'STACK_STATUS')
        require(stack_identity(s)==stack_identity(old),'STACK_AUTHORITY')
        if phase=='before':
            require(s.get('LastOperations')==old.get('LastOperations') and
                    s.get('LastUpdatedTime')==old.get('LastUpdatedTime'),'STACK_OPERATION')
    rows,ids=resource_identity(data['resources']);old_rows,old_ids=resource_identity(b['resources'])
    require(ids==old_ids and len(ids)==18,'PHYSICAL_RESOURCES')
    for name,row in rows.items():
        status=('UPDATE_FAILED' if phase=='before' else 'UPDATE_COMPLETE') if name=='ReceiptInstance' else old_rows[name]['ResourceStatus']
        require(row['ResourceStatus']==status,'RESOURCE_STATUS')
    require(body(data['template'])==body(b['template']),'ORIGINAL_SMALL_TEMPLATE')
    for name in ('schedule','policy','source_policy','security'):
        require(data[name]==b[name],'UNCHANGED_'+name.upper())
    require(parse(data['stack_policy']['StackPolicyBody'])==parse(b['stack_policy']['StackPolicyBody']),'STACK_POLICY')
    hardware=load_module('recovery001_hardware',HERE.parent/'receipt-medium-v1/host-control.py')
    for name in ('instances','instances_confirm'):
        hardware.validate_pair(data[name],data['volumes'],data['credits'],stage='before_resize')
    if phase=='before':eligibility(data['events'])
    return {'status':'ELIGIBLE_STOPPED_FAILED_STACK' if phase=='before' else 'STOPPED_STACK_CONFIGURATION_CHECKED',
        'stack_status':want,'instances':{i:'stopped' for i in PAIR},'physical_resource_count':18,
        'expired_stop_schedule_is_protection':False,'phase6_complete':False}


def recovery_request(token):
    return {'StackName':STACK,'ResourcesToSkip':['ReceiptInstance'],'ClientRequestToken':token}


def validate_request(value, token):
    require(type(token) is str and len(token)==64 and all(c in '0123456789abcdef' for c in token),'TOKEN')
    require(value==recovery_request(token),'EXACT_RECOVERY_REQUEST')


def drift(value, started, finished):
    d=value['StackResourceDrift']
    require(d['StackId']==STACK and d['LogicalResourceId']=='ReceiptInstance' and
            d['PhysicalResourceId']==RECEIPT and d['ResourceType']=='AWS::EC2::Instance','DRIFT_IDENTITY')
    require(d['StackResourceDriftStatus']=='IN_SYNC' and not d.get('PropertyDifferences'),'DRIFT_STATUS')
    require(started-5<=epoch(d['Timestamp'])<=finished+5 and finished-started<=35,'DRIFT_FRESHNESS')
    expected=parse(d['ExpectedProperties']);actual=parse(d['ActualProperties'])
    require(expected==actual and expected.get('InstanceType')=='t4g.small' and expected.get('ImageId')==AMI,'DRIFT_PROPERTIES')
    return {'status':'INDIVIDUAL_DRIFT_IN_SYNC','explicit_template_properties_only':True}


def requests():
    b=baseline()
    jobs=[('identity','sts','get-caller-identity',{}),
        ('stack','cloudformation','describe-stacks',{'StackName':STACK}),
        ('resources','cloudformation','list-stack-resources',{'StackName':STACK}),
        ('template','cloudformation','get-template',{'StackName':STACK,'TemplateStage':'Original'}),
        ('schedule','scheduler','get-schedule',{'Name':'mie-p6-qualification-001-stop'}),
        ('stack_policy','cloudformation','get-stack-policy',{'StackName':STACK})]
    for name in ('policy','source_policy'):
        jobs.append((name,'iam','get-role-policy',{k:b[name][k] for k in ('RoleName','PolicyName')}))
    jobs.extend([('security','ec2','describe-security-groups',{'GroupIds':['sg-04209bb68f81f49b9']}),
        ('instances','ec2','describe-instances',{'InstanceIds':PAIR}),
        ('volumes','ec2','describe-volumes',{'VolumeIds':['vol-0c92009fa2fb849af','vol-0d777f69ef62e0bbd']}),
        ('credits','ec2','describe-instance-credit-specifications',{'InstanceIds':PAIR}),
        ('events','cloudformation','describe-stack-events',{'StackName':STACK}),
        ('stack_confirm','cloudformation','describe-stacks',{'StackName':STACK}),
        ('instances_confirm','ec2','describe-instances',{'InstanceIds':PAIR})])
    return jobs
