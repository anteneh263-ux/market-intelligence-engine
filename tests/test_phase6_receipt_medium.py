"""Receipt-medium candidate tests: mocked identity, local artifacts; no AWS/host install."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import pytest

REPO = Path(__file__).resolve().parents[1]
BASE = REPO / 'deployment/phase6'
CANDIDATE = BASE / 'receipt-medium-v1'
RECEIPT = 'i-0c5a2a75ca70db228'
SOURCE = 'i-08e89ad2e3feedb8a'
ACCOUNT = '989879505232'
STACK = 'arn:aws:cloudformation:eu-north-1:989879505232:stack/mie-p6-qualification-001/c0769b00-caf6-11f1-8e71-0affdc797e93'
PROFILES = {'receipt': 'mie-p6-qualification-001-ReceiptRoleProfile-9F2A3mVypDyp',
            'source': 'mie-p6-qualification-001-SourceRoleProfile-zUFLg78C0FsN'}

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

host = load('medium_host', CANDIDATE/'prepare-qualification-host.py')
timer = load('medium_timer', CANDIDATE/'receipt-recovery-timer.py')


def metadata(monkeypatch, module, role='receipt', size='t4g.medium', **changes):
    document = dict(instanceId=RECEIPT if role=='receipt' else SOURCE, accountId=ACCOUNT,
                    region='eu-north-1', imageId='ami-04e0c7320ad3a3a48', architecture='arm64', instanceType=size)
    document.update(changes)
    profile = dict(Code='Success', InstanceProfileArn=f'arn:aws:iam::{ACCOUNT}:instance-profile/{PROFILES[role]}')
    calls = []
    class Metadata:
        def open(self, request, timeout):
            calls.append(request.full_url)
            if request.method == 'PUT': return io.BytesIO(b'token')
            assert request.headers.get('X-aws-ec2-metadata-token') == 'token'
            if request.full_url.endswith('/document'): return io.BytesIO(json.dumps(document).encode())
            assert request.full_url.endswith('/iam/info')
            return io.BytesIO(json.dumps(profile).encode())
    monkeypatch.setattr(module.urllib.request, 'build_opener', lambda *a: Metadata())
    return document, profile, calls


def test_medium_installer_accepts_only_exact_receipt_and_source_stays_small(monkeypatch):
    doc, _, calls = metadata(monkeypatch, host)
    assert host.imds_identity(RECEIPT, 'receipt')['document'] == doc
    doc, _, _ = metadata(monkeypatch, host, 'source', 't4g.small')
    assert host.imds_identity(SOURCE, 'source')['document'] == doc
    assert all('/security-credentials' not in p for p in calls)


@pytest.mark.parametrize('role,size', [('receipt','t4g.small'),('receipt','t4g.large'),('source','t4g.medium')])
def test_wrong_size_is_rejected_per_role(monkeypatch, role, size):
    doc, _, _ = metadata(monkeypatch, host, role, size)
    with pytest.raises(ValueError): host.imds_identity(doc['instanceId'], role)


def test_caller_cannot_substitute_another_instance_with_valid_profile(monkeypatch):
    other = 'i-'+'1'*17
    metadata(monkeypatch, host, 'source', 't4g.small', instanceId=other)
    with pytest.raises(ValueError): host.imds_identity(other, 'source')


def test_medium_timer_accepts_medium_receipt(monkeypatch):
    doc, _, _ = metadata(monkeypatch, timer)
    assert timer.identity() == doc


def test_medium_timer_refuses_old_small_receipt(monkeypatch):
    metadata(monkeypatch, timer, size='t4g.small')
    with pytest.raises(ValueError): timer.identity()


def test_template_changes_only_receipt_instance_type():
    old = json.loads((BASE/'qualification-v1.json').read_bytes())
    new = json.loads((CANDIDATE/'qualification-v1.json').read_bytes())
    assert new['Resources']['ReceiptInstance']['Properties']['InstanceType'] == 't4g.medium'
    restored = copy.deepcopy(new)
    restored['Resources']['ReceiptInstance']['Properties']['InstanceType'] = 't4g.small'
    assert restored == old

control = load('medium_control', CANDIDATE/'host-control.py')
delivery = load('medium_delivery', CANDIDATE/'host-preparation-command.py')


@pytest.mark.parametrize('changes', [dict(instanceId=SOURCE), dict(accountId='000000000000'),
    dict(region='us-east-1'), dict(imageId='ami-unapproved'), dict(architecture='x86_64')])
@pytest.mark.parametrize('which', ['installer','timer'])
def test_wrong_identity_fields_refused(monkeypatch, changes, which):
    module = host if which=='installer' else timer
    metadata(monkeypatch, module, **changes)
    with pytest.raises(ValueError):
        host.imds_identity(RECEIPT, 'receipt') if which=='installer' else timer.identity()


@pytest.mark.parametrize('which', ['installer','timer'])
def test_source_profile_cannot_act_as_receipt(monkeypatch, which):
    module = host if which=='installer' else timer
    _, profile, _ = metadata(monkeypatch, module)
    profile['InstanceProfileArn'] = f'arn:aws:iam::{ACCOUNT}:instance-profile/{PROFILES["source"]}'
    with pytest.raises(ValueError):
        host.imds_identity(RECEIPT,'receipt') if which=='installer' else timer.identity()


def test_source_cannot_enter_candidate_installer(monkeypatch):
    def unexpected(): raise AssertionError('candidate source preparation reached host operations')
    monkeypatch.setattr(host.platform, 'system', unexpected)
    with pytest.raises(ValueError):host.prepare('unused','unused','unused','a'*64,'source',SOURCE)


def test_old_small_receipt_refused_before_installation_files_or_commands(monkeypatch):
    metadata(monkeypatch, host, size='t4g.small')
    monkeypatch.setattr(host.platform, 'system', lambda:'Linux')
    monkeypatch.setattr(host.platform, 'machine', lambda:'aarch64')
    monkeypatch.setattr(host.os, 'geteuid', lambda:0)
    monkeypatch.setattr(host.os, 'umask', lambda mode:0)
    def unexpected(*args):raise AssertionError('read/write/install reached before correct identity')
    monkeypatch.setattr(host, 'read_file', unexpected)
    monkeypatch.setattr(host, 'mkdir_fresh', unexpected)
    monkeypatch.setattr(host, 'Commands', unexpected)
    with pytest.raises(ValueError):host.prepare('unused','unused','unused','a'*64,'receipt',RECEIPT)


def observations(stage='after_resize'):
    instances=[];volumes=[];credits=[]
    for role, iid in [('source',SOURCE),('receipt',RECEIPT)]:
        logical='SourceInstance' if role=='source' else 'ReceiptInstance'
        volume='vol-0c92009fa2fb849af' if role=='source' else 'vol-0d777f69ef62e0bbd'
        size='t4g.small' if role=='source' or stage=='before_resize' else 't4g.medium'
        state='running' if role=='receipt' and stage=='delivery' else 'stopped'
        instances.append(dict(InstanceId=iid,InstanceType=size,State={'Name':state},
            ImageId='ami-04e0c7320ad3a3a48',Architecture='arm64',
            IamInstanceProfile={'Arn':f'arn:aws:iam::{ACCOUNT}:instance-profile/{PROFILES[role]}'},
            MetadataOptions={'HttpTokens':'required','HttpEndpoint':'enabled','HttpPutResponseHopLimit':1},
            Placement={'AvailabilityZone':'eu-north-1a','Tenancy':'default'},
            SubnetId='subnet-0063a05d947cface6',VpcId='vpc-08598a29ae0886e9d',
            SecurityGroups=[{'GroupId':'sg-04209bb68f81f49b9'}],
            BlockDeviceMappings=[{'Ebs':{'VolumeId':volume}}],
            Tags=[{'Key':'aws:cloudformation:stack-id','Value':STACK},
                  {'Key':'aws:cloudformation:logical-id','Value':logical},
                  {'Key':'Purpose','Value':'phase6-qualification-only'}]))
        volumes.append(dict(VolumeId=volume,Encrypted=True,Size=8,VolumeType='gp3',State='in-use',
            AvailabilityZone='eu-north-1a',Attachments=[{'InstanceId':iid,'State':'attached','DeleteOnTermination':False}]))
        credits.append(dict(InstanceId=iid,CpuCredits='standard'))
    return ({'Reservations':[{'OwnerId':ACCOUNT,'Instances':instances}]},
            {'Volumes':volumes},{'InstanceCreditSpecifications':credits})


@pytest.mark.parametrize('stage', ['before_resize','after_resize','delivery'])
def test_pair_guard_at_each_stage_and_local_cli(tmp_path, stage):
    docs=observations(stage)
    assert control.validate_pair(*docs,stage=stage)['status']=='PAIR_VALIDATED'
    args=[]
    for name,doc in zip(('instances','volumes','credits'),docs):
        path=tmp_path/(name+'.json');path.write_text(json.dumps(doc));args += ['--'+name,str(path)]
    result=subprocess.run([__import__('sys').executable,str(CANDIDATE/'host-control.py'),*args,'--stage',stage],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['cloud_executed'] is False


@pytest.mark.parametrize('fault', ['source_medium','receipt_small','source_running','wrong_owner',
    'wrong_instance','wrong_profile','wrong_zone','extra_security_group','imds_optional','wrong_volume',
    'unencrypted','disk_deletion','wrong_attachment','unlimited','duplicate_host','missing_host','pagination','duplicate_tag'])
def test_cloud_guard_refuses_identity_resource_or_source_change(fault):
    response,volumes,credits=observations();r=response['Reservations'][0];source,receipt=r['Instances']
    if fault=='source_medium':source['InstanceType']='t4g.medium'
    elif fault=='receipt_small':receipt['InstanceType']='t4g.small'
    elif fault=='source_running':source['State']['Name']='running'
    elif fault=='wrong_owner':r['OwnerId']='000000000000'
    elif fault=='wrong_instance':receipt['InstanceId']='i-'+'1'*17
    elif fault=='wrong_profile':receipt['IamInstanceProfile']=source['IamInstanceProfile']
    elif fault=='wrong_zone':receipt['Placement']['AvailabilityZone']='eu-north-1b'
    elif fault=='extra_security_group':receipt['SecurityGroups'].append({'GroupId':'sg-other'})
    elif fault=='imds_optional':receipt['MetadataOptions']['HttpTokens']='optional'
    elif fault=='wrong_volume':receipt['BlockDeviceMappings'][0]['Ebs']['VolumeId']='vol-other'
    elif fault=='unencrypted':volumes['Volumes'][1]['Encrypted']=False
    elif fault=='disk_deletion':volumes['Volumes'][1]['Attachments'][0]['DeleteOnTermination']=True
    elif fault=='wrong_attachment':volumes['Volumes'][1]['Attachments'][0]['InstanceId']=SOURCE
    elif fault=='unlimited':credits['InstanceCreditSpecifications'][1]['CpuCredits']='unlimited'
    elif fault=='duplicate_host':r['Instances'].append(copy.deepcopy(receipt))
    elif fault=='missing_host':r['Instances'].pop()
    elif fault=='pagination':response['NextToken']='more'
    elif fault=='duplicate_tag':receipt['Tags'].append(receipt['Tags'][0].copy())
    with pytest.raises(ValueError):control.validate_pair(response,volumes,credits,stage='after_resize')


def preview():
    return dict(StackId=STACK,ChangeSetId=CHANGE_SET,Parameters=[dict(ParameterKey='LaunchCompute',ParameterValue='yes'),dict(ParameterKey='StopAtUtc',ParameterValue=STOP_EXPRESSION)],Status='CREATE_COMPLETE',ExecutionStatus='AVAILABLE',Changes=[dict(Type='Resource',ResourceChange=dict(
        Action='Modify',LogicalResourceId='ReceiptInstance',PhysicalResourceId=RECEIPT,
        ResourceType='AWS::EC2::Instance',Replacement='False',Scope=['Properties'],
        Details=[dict(Evaluation='Static',ChangeSource='DirectModification',Target=dict(Attribute='Properties',Name='InstanceType',RequiresRecreation='Never',BeforeValue='t4g.small',AfterValue='t4g.medium',AttributeChangeType='Modify'))]))])


def test_template_and_restrictive_preview_accept_only_receipt_type():
    result=control.validate_templates((BASE/'qualification-v1.json').read_bytes(),(CANDIDATE/'qualification-v1.json').read_bytes())
    assert result['status']=='RECEIPT_TYPE_ONLY_TEMPLATE'
    assert check_preview(preview())['cloud_executed'] is False


@pytest.mark.parametrize('fault',['replace','conditional','source','extra_resource','wrong_stack','wrong_id','remove','iam_property','dynamic','recreation','scope','pagination'])
def test_preview_rejects_unplanned_or_uncertain_changes(fault):
    p=preview();r=p['Changes'][0]['ResourceChange'];d=r['Details'][0]
    if fault=='replace':r['Replacement']='True'
    elif fault=='conditional':r['Replacement']='Conditional'
    elif fault=='source':r['LogicalResourceId']='SourceInstance'
    elif fault=='extra_resource':p['Changes'].append(copy.deepcopy(p['Changes'][0]))
    elif fault=='wrong_stack':p['StackId']='other'
    elif fault=='wrong_id':r['PhysicalResourceId']=SOURCE
    elif fault=='remove':r['Action']='Remove'
    elif fault=='iam_property':d['Target']['Name']='IamInstanceProfile'
    elif fault=='dynamic':d['Evaluation']='Dynamic'
    elif fault=='recreation':d['Target']['RequiresRecreation']='Conditionally'
    elif fault=='scope':r['Scope'].append('Tags')
    elif fault=='pagination':p['NextToken']='more'
    with pytest.raises(ValueError):check_preview(p)


def test_template_rejects_source_or_iam_change():
    old=(BASE/'qualification-v1.json').read_bytes();new=json.loads((CANDIDATE/'qualification-v1.json').read_bytes())
    for resource,property_,value in [('SourceInstance','InstanceType','t4g.medium'),('ReceiptInstance','IamInstanceProfile','different')]:
        bad=copy.deepcopy(new);bad['Resources'][resource]['Properties'][property_]=value
        with pytest.raises(ValueError):control.validate_templates(old,json.dumps(bad).encode())


@pytest.mark.parametrize('changes',[dict(role='source'),dict(instance_id=SOURCE),dict(version_id='null'),dict(version_id='a;touch /tmp/bad'),dict(expected_timer_us=None),dict(stop_utc_ms=None)])
def test_candidate_delivery_refuses_missing_pins_or_source(changes):
    args=dict(role='receipt',instance_id=RECEIPT,version_id='test-version',stop_utc_ms=1792332400000,expected_timer_us=2570000000)
    args.update(changes)
    with pytest.raises(ValueError):delivery.request(**args)


def test_candidate_request_pins_hash_version_timer_and_deadlines(tmp_path):
    req=delivery.request('receipt',RECEIPT,'test-version',stop_utc_ms=1792332400000,expected_timer_us=2570000000)
    script=req['Parameters']['commands'][0]
    assert req['InstanceIds']==[RECEIPT] and req['DocumentVersion']=='1'
    assert '--version-id test-version' in script and delivery.KIT_SHA in script
    assert 'host-preparation-delivery-medium-v1' in script and 'delivery-v2' not in script
    assert script.index('--expected-monotonic-us') < script.index('mkdir -m 0700')
    assert script.rindex('--expected-monotonic-us') > script.index('tar --extract')
    assert script.rindex('--expected-monotonic-us') < script.index('/usr/bin/timeout --kill-after=5s 1900')
    assert "d['instanceType'] == 't4g.medium'" in script
    path=tmp_path/'syntax.sh';path.write_text(script);subprocess.run(['/bin/bash','-n',str(path)],check=True)
    arm=delivery.timer_request(1792332400000)
    assert arm['InstanceIds']==[RECEIPT] and arm['Parameters']['executionTimeout']==['120']


def test_modified_timer_bytes_refused(monkeypatch):
    monkeypatch.setattr(delivery,'TIMER_SHA','0'*64)
    with pytest.raises(ValueError):delivery.timer_request(1792332400000)


def test_frozen_legacy_bytes_and_core_timer_algorithm_unchanged():
    pins={'qualification-v1.json':'ef9f30a8af6bfb817f2ddcebeedbbe5abf88f6b89a17a2a969fb9e36c4591754',
          'host-preparation-command.py':'6b19b2ddad040cfb449827cfe2428ab8628d02049a8d23bb06bb9d17d8841eb9',
          'receipt-recovery-timer.py':'8cbd4273ea287f0fb4d99b0b3c9dff791e767b49e16ce6cd90f368e1b3ad54fa'}
    for name,digest in pins.items():assert hashlib.sha256((BASE/name).read_bytes()).hexdigest()==digest
    original=(BASE/'receipt-recovery-timer.py').read_text()
    candidate=(CANDIDATE/'receipt-recovery-timer.py').read_text().replace('Receipt-medium-v1 guest stop only','Attempt002 guest stop only').replace("d['instanceType'] == 't4g.medium'","d['instanceType'] == 't4g.small'")
    assert candidate==original


def test_preview_cannot_change_to_large_despite_matching_property_name():
    p=preview()
    p['Changes'][0]['ResourceChange']['Details'][0]['Target'].update(BeforeValue='t4g.small',AfterValue='t4g.large',AttributeChangeType='Modify')
    with pytest.raises(ValueError):check_preview(p)


CHANGE_SET='arn:aws:cloudformation:eu-north-1:989879505232:changeSet/mie-p6-receipt-medium-v1-004/11111111-1111-1111-1111-111111111111'
STOP_EXPRESSION='at(2026-10-21T20:00:00)'
def check_preview(value, **changes):
    args=dict(expected_change_set_id=CHANGE_SET,expected_stop_expression=STOP_EXPRESSION,
              template_request=dict(StackName=STACK,ChangeSetName=CHANGE_SET,TemplateStage='Original'),
              template_response={'TemplateBody':json.loads((CANDIDATE/'qualification-v1.json').read_bytes())})
    args.update(changes)
    return control.validate_change_set(value,**args)


@pytest.mark.parametrize('fault',['different_id','wrong_template_request','different_template','missing_values','stop_changed','source_disabled'])
def test_preview_is_bound_to_exact_id_template_and_parameters(fault):
    p=preview();args={}
    if fault=='different_id':p['ChangeSetId']=CHANGE_SET.replace('11111111-','22222222-',1)
    elif fault=='wrong_template_request':args['template_request']=dict(StackName=STACK,ChangeSetName='latest',TemplateStage='Original')
    elif fault=='different_template':
        t=json.loads((CANDIDATE/'qualification-v1.json').read_bytes());t['Resources']['ReceiptInstance']['Properties']['InstanceType']='t4g.large'
        args['template_response']={'TemplateBody':t}
    elif fault=='missing_values':del p['Changes'][0]['ResourceChange']['Details'][0]['Target']['AfterValue']
    elif fault=='stop_changed':p['Parameters'][1]['ParameterValue']='at(2026-10-22T20:00:00)'
    elif fault=='source_disabled':p['Parameters'][0]['ParameterValue']='no'
    with pytest.raises((ValueError,KeyError)):check_preview(p,**args)
