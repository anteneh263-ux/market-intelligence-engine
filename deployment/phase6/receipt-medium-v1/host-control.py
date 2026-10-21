"""Offline receipt-medium guards; read supplied observations, never invoke AWS.

These validate hardware/ownership and a proposed receipt-only change set. The
operator must also verify fresh account/stack/template, resource inventory, stop
schedule, observer health and the absolute/continuous deadline before execution.
"""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

ACCOUNT = '989879505232'
STACK = 'arn:aws:cloudformation:eu-north-1:989879505232:stack/mie-p6-qualification-001/c0769b00-caf6-11f1-8e71-0affdc797e93'
INSTANCES = {'source': 'i-08e89ad2e3feedb8a', 'receipt': 'i-0c5a2a75ca70db228'}
PROFILES = {'source': 'mie-p6-qualification-001-SourceRoleProfile-zUFLg78C0FsN',
            'receipt': 'mie-p6-qualification-001-ReceiptRoleProfile-9F2A3mVypDyp'}
VOLUMES = {'source': 'vol-0c92009fa2fb849af', 'receipt': 'vol-0d777f69ef62e0bbd'}
ORIGINAL_TEMPLATE_SHA = 'ef9f30a8af6bfb817f2ddcebeedbbe5abf88f6b89a17a2a969fb9e36c4591754'


def require(ok):
    if not ok:
        raise ValueError('MEDIUM_CONTROL_REFUSED')


def unique(rows, key):
    result = {}
    for row in rows:
        name = row[key]
        require(name not in result)
        result[name] = row
    return result


def validate_pair(response, volumes, credits, *, stage):
    require(stage in ('before_resize', 'after_resize', 'delivery'))
    require(not response.get('NextToken') and not volumes.get('NextToken') and not credits.get('NextToken'))
    found = {}
    for reservation in response['Reservations']:
        require(reservation['OwnerId'] == ACCOUNT)
        for instance in reservation['Instances']:
            tags = {k: v['Value'] for k, v in unique(instance['Tags'], 'Key').items()}
            logical = tags.get('aws:cloudformation:logical-id')
            require(logical in ('SourceInstance', 'ReceiptInstance'))
            role = 'source' if logical == 'SourceInstance' else 'receipt'
            require(role not in found and instance['InstanceId'] == INSTANCES[role])
            require(tags.get('aws:cloudformation:stack-id') == STACK
                    and tags.get('Purpose') == 'phase6-qualification-only')
            expected_type = 't4g.medium' if role == 'receipt' and stage != 'before_resize' else 't4g.small'
            expected_state = 'running' if role == 'receipt' and stage == 'delivery' else 'stopped'
            require(instance['InstanceType'] == expected_type and instance['State']['Name'] == expected_state
                    and instance['ImageId'] == 'ami-04e0c7320ad3a3a48' and instance['Architecture'] == 'arm64')
            require(instance['IamInstanceProfile']['Arn'] ==
                    f'arn:aws:iam::{ACCOUNT}:instance-profile/{PROFILES[role]}')
            require(instance['MetadataOptions']['HttpTokens'] == 'required'
                    and instance['MetadataOptions']['HttpEndpoint'] == 'enabled'
                    and instance['MetadataOptions']['HttpPutResponseHopLimit'] == 1
                    and instance['Placement']['AvailabilityZone'] == 'eu-north-1a'
                    and instance['Placement']['Tenancy'] == 'default'
                    and instance['SubnetId'] == 'subnet-0063a05d947cface6'
                    and instance['VpcId'] == 'vpc-08598a29ae0886e9d')
            require([g['GroupId'] for g in instance['SecurityGroups']] == ['sg-04209bb68f81f49b9'])
            require([b['Ebs']['VolumeId'] for b in instance['BlockDeviceMappings']] == [VOLUMES[role]])
            found[role] = instance['InstanceId']
    require(set(found) == set(INSTANCES))
    credit_rows = unique(credits['InstanceCreditSpecifications'], 'InstanceId')
    require(set(credit_rows) == set(INSTANCES.values())
            and all(v['CpuCredits'] == 'standard' for v in credit_rows.values()))
    volume_rows = unique(volumes['Volumes'], 'VolumeId')
    require(set(volume_rows) == set(VOLUMES.values()))
    for role, volume_id in VOLUMES.items():
        v = volume_rows[volume_id]
        require(v['Encrypted'] is True and v['Size'] == 8 and v['VolumeType'] == 'gp3'
                and v['AvailabilityZone'] == 'eu-north-1a' and v['State'] == 'in-use')
        require(len(v['Attachments']) == 1)
        a = v['Attachments'][0]
        require(a['InstanceId'] == INSTANCES[role] and a['State'] == 'attached'
                and a['DeleteOnTermination'] is False)
    return dict(status='PAIR_VALIDATED', stage=stage, instances=found, cloud_executed=False)


def validate_templates(original_raw, candidate_raw):
    require(hashlib.sha256(original_raw).hexdigest() == ORIGINAL_TEMPLATE_SHA)
    old, new = json.loads(original_raw), json.loads(candidate_raw)
    expected = copy.deepcopy(old)
    expected['Resources']['ReceiptInstance']['Properties']['InstanceType'] = 't4g.medium'
    require(new == expected)
    return dict(status='RECEIPT_TYPE_ONLY_TEMPLATE', candidate_sha256=hashlib.sha256(candidate_raw).hexdigest())


def validate_change_set(value, *, expected_change_set_id, expected_stop_expression,
                        template_request, template_response):
    require(type(expected_change_set_id) is str and re.fullmatch(
        r'arn:aws:cloudformation:eu-north-1:989879505232:changeSet/mie-p6-receipt-medium-v1-[0-9]{3}/[0-9a-f-]{36}',
        expected_change_set_id) is not None)
    require(value['ChangeSetId'] == expected_change_set_id)
    require(type(expected_stop_expression) is str and re.fullmatch(
        r'at\([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\)', expected_stop_expression) is not None)
    parameters = unique(value['Parameters'], 'ParameterKey')
    require(set(parameters) == {'LaunchCompute', 'StopAtUtc'}
            and parameters['LaunchCompute']['ParameterValue'] == 'yes'
            and parameters['StopAtUtc']['ParameterValue'] == expected_stop_expression)
    require(template_request == dict(StackName=STACK, ChangeSetName=expected_change_set_id,
                                     TemplateStage='Original'))
    here = Path(__file__).resolve().parent
    original_raw = (here.parent / 'qualification-v1.json').read_bytes()
    candidate_raw = (here / 'qualification-v1.json').read_bytes()
    checked = validate_templates(original_raw, candidate_raw)
    body = template_response['TemplateBody']
    if type(body) is str:
        body = json.loads(body)
    require(body == json.loads(candidate_raw))
    require(not value.get('ImportExistingResources') and not value.get('IncludeNestedStacks')
            and not value.get('ParentChangeSetId') and not value.get('DeploymentMode'))
    require(value['StackId'] == STACK and value['Status'] == 'CREATE_COMPLETE'
            and value['ExecutionStatus'] == 'AVAILABLE' and not value.get('NextToken'))
    changes = value['Changes']
    require(len(changes) == 1 and changes[0]['Type'] == 'Resource')
    change = changes[0]['ResourceChange']
    require(change['Action'] == 'Modify' and change['LogicalResourceId'] == 'ReceiptInstance'
            and change['PhysicalResourceId'] == INSTANCES['receipt']
            and change['ResourceType'] == 'AWS::EC2::Instance'
            and change['Replacement'] == 'False' and change['Scope'] == ['Properties'])
    require(not change.get('ModuleInfo') and not change.get('ChangeSetId'))
    details = change['Details']
    require(len(details) == 1)
    d = details[0]
    require(d['Evaluation'] == 'Static' and d['ChangeSource'] == 'DirectModification'
            and d['Target']['Attribute'] == 'Properties' and d['Target']['Name'] == 'InstanceType'
            and d['Target']['RequiresRecreation'] == 'Never'
            and d['Target']['BeforeValue'] == 't4g.small'
            and d['Target']['AfterValue'] == 't4g.medium'
            and d['Target']['AttributeChangeType'] == 'Modify')
    return dict(status='RECEIPT_TYPE_ONLY_PREVIEW', cloud_executed=False,
                change_set_id=expected_change_set_id, template_sha256=checked['candidate_sha256'],
                note='Does not prove actual update behavior, runtime readiness or capacity.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--instances', type=Path, required=True)
    p.add_argument('--volumes', type=Path, required=True)
    p.add_argument('--credits', type=Path, required=True)
    p.add_argument('--stage', choices=['before_resize', 'after_resize', 'delivery'], required=True)
    a = p.parse_args()
    result = validate_pair(*(json.loads(f.read_bytes()) for f in (a.instances, a.volumes, a.credits)), stage=a.stage)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
