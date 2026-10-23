"""Small008 admission only. Historical recovery001 is not reclassified."""
import copy
import hashlib
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
old = module('small008_old_guard', HERE.parent/'receipt-stack-recovery001/guard.py')
hardware = module('small008_hardware', HERE.parent/'receipt-medium-v1/host-control.py')
require, parse, canonical = old.require, old.parse, old.canonical
ACCOUNT, REGION, STACK, SOURCE, RECEIPT, PAIR = old.ACCOUNT, old.REGION, old.STACK, old.SOURCE, old.RECEIPT, old.PAIR
VERSION = 'receipt-host-preparation.small008.v1'
BASELINE_SHA = 'ff23ef4e465dca306eed116a25e93c9640ccf14549e5438a99a4d6f71a8a9f5e'
ENIS = {SOURCE:'eni-0775e4cc4f34ca23a', RECEIPT:'eni-01b79eeb1cb3f69fc'}
ATTACHMENTS = {SOURCE:'eni-attach-0d7a9225155dde9f2', RECEIPT:'eni-attach-04bf5fae5790976fc'}
KIT_SHA = 'ea831b59505cfdd04e4c6f78bff4de63fc3fa71045e5279d5d7fe6b7d02c5c69'
KIT_BYTES = 21585589
KIT_VERSION = 'eD3X.1K3pNj0QsWfMPCOWYrgac7toEAL'
BUCKET = 'mie-p6-qualification-001-989879505232-artifacts'

def baseline():
    raw=(HERE/'baseline.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest()==BASELINE_SHA,'SMALL008_BASELINE')
    return parse(raw)

def pair(instances, volumes, credits, allowed):
    states=old.pair_state(instances)
    require(states[SOURCE]=='stopped' and states[RECEIPT] in allowed,'SMALL008_STATES')
    # Reuse unchanged hardware validator; actual states are validated above and
    # preserved in raw observations, never relabelled in evidence.
    checked=copy.deepcopy(instances)
    for r in checked['Reservations']:
        for i in r['Instances']:i['State']['Name']='stopped'
    hardware.validate_pair(checked, volumes, credits, stage='before_resize')
    for r in instances['Reservations']:
        for i in r['Instances']:
            n=i['NetworkInterfaces'];require(len(n)==1,'SMALL008_PRIMARY_ENI')
            require(n[0]['NetworkInterfaceId']==ENIS[i['InstanceId']] and
                n[0]['Attachment']['AttachmentId']==ATTACHMENTS[i['InstanceId']] and
                n[0]['Attachment']['DeviceIndex']==0,'SMALL008_ENI_BINDING')
    return states

def network(data):
    rows=old.unique(data['network']['NetworkInterfaces'],'NetworkInterfaceId')
    require(not data['network'].get('NextToken') and set(rows)==set(ENIS.values()),'SMALL008_NETWORK_SET')
    for role, ident in [('source',SOURCE),('receipt',RECEIPT)]:
        n=rows[ENIS[ident]];a=n['Attachment'];attr=data[role+'_attr']
        require(n['OwnerId']==ACCOUNT and n['Status']=='in-use' and
            n['SubnetId']=='subnet-0063a05d947cface6' and n['VpcId']=='vpc-08598a29ae0886e9d' and
            [v['GroupId'] for v in n['Groups']]==['sg-04209bb68f81f49b9'] and
            a['InstanceId']==ident and a['InstanceOwnerId']==ACCOUNT and
            a['AttachmentId']==ATTACHMENTS[ident] and a['DeviceIndex']==0 and
            a['Status']=='attached' and a['DeleteOnTermination'] is True,'SMALL008_NETWORK_BINDING')
        require(attr=={'NetworkInterfaceId':ENIS[ident],'AssociatePublicIpAddress':True},'SMALL008_ASSIGNMENT')

def drift(value):
    """Accept only the recorded discrepancy or exact IN_SYNC; never arbitrary drift.

    This is a cached CFN report, not a fresh drift run or proof of historical cause.
    Fresh exact EC2 attributes must separately pass network().
    """
    rows=value['StackResourceDrifts'];require(not value.get('NextToken') and len(rows)==1,'SMALL008_DRIFT_SET')
    d=rows[0];b=baseline()['drift']['StackResourceDrift']
    for key in ('StackId','LogicalResourceId','PhysicalResourceId','ResourceType','ExpectedProperties'):
        if key=='ExpectedProperties':require(parse(d[key])==parse(b[key]),'SMALL008_DRIFT_EXPECTED')
        else:require(d[key]==b[key],'SMALL008_DRIFT_IDENTITY')
    if d['StackResourceDriftStatus']=='IN_SYNC':
        require(not d.get('PropertyDifferences') and parse(d['ActualProperties'])==parse(d['ExpectedProperties']),'SMALL008_DRIFT_SYNC')
        return 'CACHED_CFN_IN_SYNC'
    require(d['StackResourceDriftStatus']=='MODIFIED' and
        d['PropertyDifferences']==b['PropertyDifferences'] and
        parse(d['ActualProperties'])==parse(b['ActualProperties']),'SMALL008_DRIFT_EXCEPTION')
    return 'CACHED_CFN_PUBLIC_IP_DISCREPANCY_UNRESOLVED'

def snapshot(data, *, stop=None, allowed=('stopped',)):
    b=baseline()['data'];require(data['identity']==b['identity'],'SMALL008_PRINCIPAL')
    for key in ('stack','stack_confirm'):
        s=old.one(data[key],'Stacks');prior=old.one(b[key],'Stacks')
        expected_status='UPDATE_ROLLBACK_COMPLETE' if stop is None else 'UPDATE_COMPLETE'
        require(s['StackStatus']==expected_status,'SMALL008_STACK_STATUS')
        actual=old.stack_identity(s);expected=old.stack_identity(prior)
        if stop is not None:
            expected=copy.deepcopy(expected)
            for p in expected['Parameters']:
                if p['ParameterKey']=='StopAtUtc':p['ParameterValue']=stop
        require(actual==expected,'SMALL008_STACK_AUTHORITY')
    rows,ids=old.resource_identity(data['resources']);br,bids=old.resource_identity(b['resources'])
    require(ids==bids and len(ids)==18,'SMALL008_RESOURCES')
    require(all(r['ResourceStatus']==br[k]['ResourceStatus'] for k,r in rows.items()),'SMALL008_RESOURCE_STATUS')
    require(old.body(data['template'])==old.body(b['template']),'SMALL008_TEMPLATE')
    for name in ('policy','source_policy','security'):
        require(data[name]==b[name],'SMALL008_'+name)
    require(parse(data['stack_policy']['StackPolicyBody'])==parse(b['stack_policy']['StackPolicyBody']),'SMALL008_STACK_POLICY')
    schedule(data['schedule'],stop)
    for key in ('instances','instances_confirm'):pair(data[key],data['volumes'],data['credits'],allowed)
    network(data)
    return {'status':'SMALL008_CONFIGURATION_VERIFIED','cfn_report':drift(data['drifts']),
        'original_recovery001_outcome':'RECOVERY_UNRESOLVED','phase6_complete':False}

def schedule(value,stop):
    old_schedule=baseline()['data']['schedule']
    ignore={'LastModificationDate'}
    expected={k:v for k,v in old_schedule.items() if k not in ignore}
    if stop is not None:expected['ScheduleExpression']=stop
    require({k:v for k,v in value.items() if k not in ignore}==expected,'SMALL008_SCHEDULE')
    require(value['State']=='ENABLED' and value['FlexibleTimeWindow']=={'Mode':'OFF'} and
        value['ScheduleExpressionTimezone']=='UTC','SMALL008_SCHEDULE_ACTIVE')

def requests():
    return old.requests()+[
        ('network','ec2','describe-network-interfaces',{'NetworkInterfaceIds':list(ENIS.values())}),
        ('source_attr','ec2','describe-network-interface-attribute',{'NetworkInterfaceId':ENIS[SOURCE],'Attribute':'associatePublicIpAddress'}),
        ('receipt_attr','ec2','describe-network-interface-attribute',{'NetworkInterfaceId':ENIS[RECEIPT],'Attribute':'associatePublicIpAddress'}),
        ('drifts','cloudformation','describe-stack-resource-drifts',{'StackName':STACK})]

def schedule_events(events,token,start):
    rows=[r for r in events['StackEvents'] if old.epoch(r['Timestamp'])>=start]
    require(rows and all(r['StackId']==STACK and r.get('ClientRequestToken')==token and
        r['LogicalResourceId'] in ('ExternalStop','mie-p6-qualification-001') for r in rows),'SMALL008_UPDATE_EVENTS')
    require(max(rows,key=lambda r:old.epoch(r['Timestamp']))['ResourceStatus']=='UPDATE_COMPLETE','SMALL008_UPDATE_TERMINAL')

def update_request(stop,token):
    return {'StackName':STACK,'UsePreviousTemplate':True,'Capabilities':['CAPABILITY_IAM'],
        'Parameters':[{'ParameterKey':'LaunchCompute','UsePreviousValue':True},
                      {'ParameterKey':'StopAtUtc','ParameterValue':stop}], 'ClientRequestToken':token}
