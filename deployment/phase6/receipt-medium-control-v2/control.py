"""Versioned receipt-medium control: exact observed dependency preview + stack policy.

No CLI and no AWS client are constructed here. Content classification is offline.
execute_once requires an explicit caller-supplied API, pre-existing approved log
directory, and fresh supervisory check. Tests use a recording fake API only.
"""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import time

HERE = Path(__file__).resolve().parent
V1 = HERE.parent / 'receipt-medium-v1'
PINS = {
    'preview-reference.json': 'a5f47d625f09d2def971e484cb5d8eb8ebb16f4f912d0d968c6d7a23c4b0b6df',
    'resource-bindings.json': '6c8cb68aa39b7f8f0b49117a2415cd6649ee971d29ebbfa4bc96a122b32ff1eb',
    'stack-policy.json': 'd70f1bdd30506a0663c9607a6a099f9751c123e28ab6ddf16cbeed861b5dfe23',
}
TEMPLATE_SHA = '6e23770f5b50aefd4f5491a09d8803c7cc2baea59527e85b8528f634407103a9'
V1_GUARD_SHA = '2ac21909c36e4b0a2a417f5272ec84f8135b93f21e45135af65f033b57950c90'


def require(ok):
    if not ok:
        raise ValueError('MEDIUM_V2_CONTROL_REFUSED')


def parse(raw):
    def pairs(items):
        d = {}
        for k, v in items:
            require(k not in d)
            d[k] = v
        return d
    def constant(value):
        raise ValueError('NONFINITE_JSON_REFUSED')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def pinned(name):
    raw = (HERE / name).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == PINS[name])
    return parse(raw)


require(hashlib.sha256((V1 / 'host-control.py').read_bytes()).hexdigest() == V1_GUARD_SHA)
spec = importlib.util.spec_from_file_location('receipt_medium_v1_preserved', V1 / 'host-control.py')
v1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v1)
STACK, ACCOUNT = v1.STACK, v1.ACCOUNT


def stop_time(expression):
    require(type(expression) is str and re.fullmatch(r'at\(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\)', expression))
    return int(datetime.strptime(expression, 'at(%Y-%m-%dT%H:%M:%S)').replace(tzinfo=timezone.utc).timestamp() * 1000)


def nested(value):
    """Ignore JSON serialization whitespace, not value/type or array changes."""
    if isinstance(value, dict):
        return {k: nested(v) for k, v in value.items()}
    if isinstance(value, list):
        return [nested(v) for v in value]
    if type(value) is str and value.lstrip().startswith(('{', '[')) and not value.lstrip().startswith('{{'):
        return nested(parse(value))
    return value


def changes(value):
    require(type(value) is list)
    result = {}
    for row in value:
        require(set(row) == {'Type', 'ResourceChange'} and row['Type'] == 'Resource')
        r = nested(copy.deepcopy(row['ResourceChange']))
        key = r['LogicalResourceId']
        require(key not in result)
        require(type(r['Details']) is list)
        r['Details'] = sorted(r['Details'], key=canonical)
        result[key] = r
    return result


def expected_changes(expression):
    stop_time(expression)
    result = changes(pinned('preview-reference.json')['Changes'])
    for which in ('BeforeContext', 'AfterContext'):
        result['ExternalStop'][which]['Properties']['ScheduleExpression'] = expression
    return result


def validate_preview_content(value, stop_expression):
    """Classify exact reference shape; does NOT authorize execution of any ARN."""
    ref = pinned('preview-reference.json')
    stop_time(stop_expression)
    # Deliberately refuse new top-level modes/fields instead of silently ignoring them.
    require(set(value) == set(ref))
    varying = {'ChangeSetId', 'ChangeSetName', 'CreationTime', 'Description', 'Changes', 'Parameters'}
    require(canonical({k: v for k, v in value.items() if k not in varying}) ==
            canonical({k: v for k, v in ref.items() if k not in varying}))
    require(type(value['CreationTime']) is str and type(value['Description']) is str)
    params = v1.unique(value['Parameters'], 'ParameterKey')
    require(canonical(params) == canonical({
        'LaunchCompute': {'ParameterKey': 'LaunchCompute', 'ParameterValue': 'yes'},
        'StopAtUtc': {'ParameterKey': 'StopAtUtc', 'ParameterValue': stop_expression}}))
    require(canonical(changes(value['Changes'])) == canonical(expected_changes(stop_expression)))
    return dict(schema_version='p6.receipt_medium_preview_content.v2',
                status='EXACT_RECEIPT_AND_REFERENCE_CHANGES', cloud_executed=False,
                execution_authorized=False, replacement_requires_enforced_policy=True)


def policy():
    return pinned('stack-policy.json')


def validate_policy(response):
    require(set(response) == {'StackPolicyBody'} and type(response['StackPolicyBody']) is str)
    require(canonical(parse(response['StackPolicyBody'])) == canonical(policy()))
    return {'status': 'EXACT_STACK_POLICY_READBACK', 'cloud_enforcement_verified': False}


def policy_install_request(previous_response):
    """Render only. Never replace an unknown existing policy."""
    if previous_response:
        validate_policy(previous_response)
        return None
    require(previous_response == {})
    return dict(StackName=STACK, StackPolicyBody=canonical(policy()))


def execution_request(value, *, expected_id, stop_expression, template_request,
                      template_response, policy_request, policy_response):
    validate_preview_content(value, stop_expression)
    match = re.fullmatch(r'arn:aws:cloudformation:eu-north-1:989879505232:changeSet/'
                         r'(mie-p6-receipt-medium-v2-[0-9]{3})/[0-9a-f-]{36}', expected_id)
    require(match is not None and value['ChangeSetId'] == expected_id
            and value['ChangeSetName'] == match.group(1))
    require(template_request == dict(StackName=STACK, ChangeSetName=expected_id, TemplateStage='Original'))
    require(policy_request == {'StackName': STACK})
    raw = (V1 / 'qualification-v1.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == TEMPLATE_SHA)
    v1.validate_templates((HERE.parent / 'qualification-v1.json').read_bytes(), raw)
    body = template_response['TemplateBody']
    require(canonical(parse(body) if type(body) is str else body) == canonical(parse(raw)))
    validate_policy(policy_response)
    # Explicit rollback behavior; no policy override or repair options are exposed.
    token = hashlib.sha256(canonical(dict(id=expected_id, stop=stop_expression,
                                          policy=policy(), template=TEMPLATE_SHA)).encode()).hexdigest()
    return dict(StackName=STACK, ChangeSetName=expected_id, ClientRequestToken=token, DisableRollback=False)


def policy_document(value):
    result = copy.deepcopy(value)
    for row in result['Statement']:
        for key in ('Action', 'Resource'):
            if type(row[key]) is str:
                row[key] = [row[key]]
    return result


def check_supervisor(callback, stop_expression, now_ms):
    value = callback()
    now = now_ms()
    stop = stop_time(stop_expression)
    require(type(now) is int and 2400000 <= stop - now <= 2700000)
    require(value['status'] == 'ARMED' and value['stop_utc_ms'] == stop
            and value['stack_id'] == STACK and value['stack_status'] == 'UPDATE_COMPLETE'
            and value['instances'] == {v1.INSTANCES['source']: 'stopped', v1.INSTANCES['receipt']: 'stopped'}
            and type(value['observed_utc_ms']) is int and 0 <= now - value['observed_utc_ms'] <= 25000)
    return value


def execute_once(api, *, expected_id, stop_expression, supervisor, evidence_dir,
                 now_ms=lambda: time.time_ns() // 1000000):
    """Fresh reads -> complete guards -> durable exclusive intent -> ONE execute call.

    Caller must hold the approved operation window and observe until settled/stopped.
    An exception after the intent is persisted is unresolved; never retry or remove it.
    This function does not install policy, update schedule, start hosts, or recover.
    """
    root = Path(evidence_dir)
    require(root.is_absolute() and root.resolve(strict=True) == root)
    st = root.lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o700)
    intent = root / 'medium-v2-execute-intent.json'
    require(not intent.exists() and not intent.is_symlink())
    check_supervisor(supervisor, stop_expression, now_ms)
    call = api.call
    identity = call('sts', 'get-caller-identity', {})
    require(identity['Account'] == ACCOUNT and identity['Arn'] == 'arn:aws:iam::989879505232:root')
    stack = call('cloudformation', 'describe-stacks', {'StackName': STACK})['Stacks']
    require(len(stack) == 1 and stack[0]['StackId'] == STACK and stack[0]['StackStatus'] == 'UPDATE_COMPLETE')
    require({p['ParameterKey']: p['ParameterValue'] for p in stack[0]['Parameters']} ==
            {'LaunchCompute': 'yes', 'StopAtUtc': stop_expression})
    resources = call('cloudformation', 'list-stack-resources', {'StackName': STACK})
    require(not resources.get('NextToken'))
    rows = v1.unique(resources['StackResourceSummaries'], 'LogicalResourceId')
    require({k: r['PhysicalResourceId'] for k, r in rows.items()} == pinned('resource-bindings.json'))
    require(all(r['ResourceStatus'] in ('CREATE_COMPLETE', 'UPDATE_COMPLETE') for r in rows.values()))
    instances = call('ec2', 'describe-instances', {'InstanceIds': list(v1.INSTANCES.values())})
    volumes = call('ec2', 'describe-volumes', {'VolumeIds': list(v1.VOLUMES.values())})
    credits = call('ec2', 'describe-instance-credit-specifications', {'InstanceIds': list(v1.INSTANCES.values())})
    v1.validate_pair(instances, volumes, credits, stage='before_resize')
    original = call('cloudformation', 'get-template', {'StackName': STACK, 'TemplateStage': 'Original'})
    body = original['TemplateBody']
    require(canonical(parse(body) if type(body) is str else body) ==
            canonical(parse((HERE.parent / 'qualification-v1.json').read_bytes())))
    baseline = expected_changes(stop_expression)
    schedule = call('scheduler', 'get-schedule', {'Name': 'mie-p6-qualification-001-stop'})
    props = baseline['ExternalStop']['BeforeContext']['Properties']
    require(schedule['State'] == 'ENABLED' and schedule['ScheduleExpression'] == stop_expression
            and schedule['ScheduleExpressionTimezone'] == 'UTC'
            and schedule['FlexibleTimeWindow'] == {'Mode': 'OFF'}
            and schedule['ActionAfterCompletion'] == 'NONE')
    require(schedule['Target']['Arn'] == props['Target']['Arn']
            and schedule['Target']['RoleArn'] == props['Target']['RoleArn']
            and parse(schedule['Target']['Input']) == props['Target']['Input']
            and schedule['Target']['RetryPolicy'] == {'MaximumEventAgeInSeconds': 3600, 'MaximumRetryAttempts': 1})
    for role in ('SourceRole', 'StopRole'):
        expected = baseline[role]['BeforeContext']['Properties']['Policies'][0]
        result = call('iam', 'get-role-policy', dict(RoleName=pinned('resource-bindings.json')[role],
                                                    PolicyName=expected['PolicyName']))
        require(result['RoleName'] == pinned('resource-bindings.json')[role]
                and result['PolicyName'] == expected['PolicyName'])
        require(canonical(policy_document(result['PolicyDocument'])) ==
                canonical(policy_document(expected['PolicyDocument'])))
    preview = call('cloudformation', 'describe-change-set',
                   dict(StackName=STACK, ChangeSetName=expected_id, IncludePropertyValues=True))
    created = datetime.fromisoformat(preview['CreationTime'].replace('Z', '+00:00'))
    require(created.tzinfo is not None)
    require(stop_time(stop_expression) - 2700000 <= int(created.timestamp() * 1000) <= now_ms())
    tr = dict(StackName=STACK, ChangeSetName=expected_id, TemplateStage='Original')
    template = call('cloudformation', 'get-template', tr)
    pr = {'StackName': STACK}
    current_policy = call('cloudformation', 'get-stack-policy', pr)
    request = execution_request(preview, expected_id=expected_id, stop_expression=stop_expression,
                                template_request=tr, template_response=template,
                                policy_request=pr, policy_response=current_policy)
    observation = check_supervisor(supervisor, stop_expression, now_ms)
    payload = canonical(dict(schema_version='p6.receipt_medium_execution_intent.v2',
                             request=request, observed_supervisor=observation,
                             preview_sha256=hashlib.sha256(canonical(preview).encode()).hexdigest(),
                             policy_sha256=PINS['stack-policy.json'],
                             outcome='UNKNOWN_UNTIL_SEPARATE_READBACK', retry_allowed=False)).encode()
    with intent.open('xb') as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    # No retry, even if the provider raises or returns an ambiguous response.
    response = call('cloudformation', 'execute-change-set', request)
    return dict(status='EXECUTE_RESPONSE_RECEIVED', response=response,
                resize_complete=False, runtime_accepted=False, retry_allowed=False)

