"""Synthetic local tests of the actual006 entry points; never contact AWS."""
import copy
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

REPO=Path(__file__).resolve().parents[1]
BASE=REPO/'deployment/phase6/receipt-update-states-v3'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
checks=load('checks006_tests',BASE/'checks.py');m=checks.m
creator=load('creator006_tests',BASE/'create-root.py')
NEW_STOP='at(2026-10-22T14:45:00)'


def setup_root(root,monkeypatch):
    root.chmod(0o700);(root/'operator').mkdir(mode=0o700)
    monkeypatch.setattr(m,'ROOT',root)
    monkeypatch.setattr(m,'pins',lambda:None)
    monkeypatch.setattr(m,'stamp',lambda:'synthetic001')
    data=checks.baseline();calls=[]
    lookup={(s,o,m.v2.canonical(p)):name for name,s,o,p in checks.requests()}
    def call(name,service,operation,payload):
        calls.append((name,service,operation,copy.deepcopy(payload)))
        key=lookup[(service,operation,m.v2.canonical(payload))]
        value=copy.deepcopy(data[key])
        m.write(name+'-request.json',dict(service=service,operation=operation,payload=payload))
        m.write(name+'-stdout.json',value)
        return value
    monkeypatch.setattr(m,'call',call)
    return data,calls


def snapshot(data):
    result=copy.deepcopy(data);result['stack_confirm']=copy.deepcopy(data['stack']);result['instances_confirm']=copy.deepcopy(data['instances'])
    return result


def contract(root):
    stop=m.v2.stop_time(NEW_STOP)
    c=dict(schema_version='p6.qualification_launch.v1',account_id=m.ACCOUNT,region=m.REGION,stack_id=m.STACK,
        operator_arn='arn:aws:iam::'+m.ACCOUNT+':root',not_before_utc_ms=stop-2700000,stop_utc_ms=stop,
        template_sha256=m.v2.TEMPLATE_SHA,runtime_bundle_sha256=m.SHA)
    m.write('launch-contract.json',c);return c


def scheduled(root):
    c=contract(root)
    m.write('schedule-update-once-request.json',dict(service='cloudformation',operation='update-stack',payload=dict(
        StackName=m.STACK,UsePreviousTemplate=True,Capabilities=['CAPABILITY_IAM'],Parameters=[
        dict(ParameterKey='LaunchCompute',UsePreviousValue=True),dict(ParameterKey='StopAtUtc',ParameterValue=NEW_STOP)])))
    return c


def new_deadline(data):
    data['stack']['Stacks'][0]['Parameters']=[dict(ParameterKey='LaunchCompute',ParameterValue='yes'),dict(ParameterKey='StopAtUtc',ParameterValue=NEW_STOP)]
    data['schedule']['ScheduleExpression']=NEW_STOP


def test_actual_preflight_main_records_sequential_fresh005_baseline(tmp_path,monkeypatch):
    data,calls=setup_root(tmp_path,monkeypatch)
    assert checks.main(['preflight'])==0
    result=m.read('preflight-summary.json')
    assert result['parameters']['StopAtUtc']=='at(2026-10-21T15:17:43)'
    assert result['status']=='READ_ONLY_PREFLIGHT_PASS' and result['exact_stack_policy_verified'] is True
    assert len(calls)==14 and result['batch']['reused_responses']==[] and result['batch']['atomic_snapshot'] is False
    assert all(op.startswith(('get-','describe-','list-')) for _,_,op,_ in calls)
    assert (tmp_path/'preflight2-policy-stdout.json').is_file()
    assert (tmp_path/'preflight2-source-policy-stdout.json').is_file()
    assert not (tmp_path/'launch-contract.json').exists()


@pytest.mark.parametrize('fault',['old004_deadline','absent_policy','broad_policy','wrong_account','wrong_arn','wrong_stack','updating',
    'resources','pagination','source_running','source_medium','receipt_medium','credits','volume','template',
    'stop_policy','source_policy','role_name','policy_name','ingress','egress','schedule_target','schedule_role','duplicate_parameters'])
def test_preflight_rejects_changed_or_stale_configuration_without_success_marker(tmp_path,monkeypatch,fault):
    d,calls=setup_root(tmp_path,monkeypatch);s=d['stack']['Stacks'][0]
    rows={i['InstanceId']:i for r in d['instances']['Reservations'] for i in r['Instances']}
    if fault=='old004_deadline':s['Parameters'][1]['ParameterValue']='at(2026-10-21T14:24:23)'
    elif fault=='absent_policy':d['stack_policy']={}
    elif fault=='broad_policy':d['stack_policy']={'StackPolicyBody':'{}'}
    elif fault=='wrong_account':d['identity']['Account']='000000000000'
    elif fault=='wrong_arn':d['identity']['Arn']='arn:aws:iam::'+m.ACCOUNT+':user/other'
    elif fault=='wrong_stack':s['StackId']='wrong'
    elif fault=='updating':s['StackStatus']='UPDATE_COMPLETE_CLEANUP_IN_PROGRESS'
    elif fault=='resources':d['resources']['StackResourceSummaries'].pop()
    elif fault=='pagination':d['resources']['NextToken']='more'
    elif fault=='source_running':rows[m.SOURCE]['State']['Name']='running'
    elif fault=='source_medium':rows[m.SOURCE]['InstanceType']='t4g.medium'
    elif fault=='receipt_medium':rows[m.RECEIPT]['InstanceType']='t4g.medium'
    elif fault=='credits':d['credits']['InstanceCreditSpecifications'][0]['CpuCredits']='unlimited'
    elif fault=='volume':d['volumes']['Volumes'][0]['Encrypted']=False
    elif fault=='template':d['template']['TemplateBody']['Description']='different'
    elif fault=='stop_policy':d['policy']['PolicyDocument']['Statement'][0]['Action']=['*']
    elif fault=='source_policy':d['source_policy']['PolicyDocument']['Statement'][0]['Resource']=['*']
    elif fault=='role_name':d['policy']['RoleName']='another-role'
    elif fault=='policy_name':d['source_policy']['PolicyName']='another-policy'
    elif fault=='ingress':d['security']['SecurityGroups'][0]['IpPermissions']=[{'IpProtocol':'-1'}]
    elif fault=='egress':d['security']['SecurityGroups'][0]['IpPermissionsEgress'][0]['FromPort']=0
    elif fault=='schedule_target':d['schedule']['Target']['Input']='{"InstanceIds":["other"]}'
    elif fault=='schedule_role':d['schedule']['Target']['RoleArn']='wrong'
    elif fault=='duplicate_parameters':s['Parameters'].append(copy.deepcopy(s['Parameters'][0]))
    with pytest.raises((ValueError,KeyError)):checks.preflight()
    assert not (tmp_path/'preflight-summary.json').exists()
    assert all(op.startswith(('get-','describe-','list-')) for _,_,op,_ in calls)


def test_preflight_failed_read_is_not_retried(tmp_path,monkeypatch):
    _,calls=setup_root(tmp_path,monkeypatch);original=m.call
    def fail(name,*args):
        if name=='preflight2-stack':raise TimeoutError('synthetic unknown read')
        return original(name,*args)
    monkeypatch.setattr(m,'call',fail)
    with pytest.raises(TimeoutError):checks.preflight()
    assert len(calls)==1 and not (tmp_path/'preflight-summary.json').exists()


def test_closure_before_launch_separates_stopped_snapshot_from_unstarted_observer(tmp_path,monkeypatch):
    _,calls=setup_root(tmp_path,monkeypatch)
    # Pin failure must not block read-only closure.
    monkeypatch.setattr(m,'pins',lambda:pytest.fail('closure must not depend on launch pins'))
    assert checks.main(['closure'])==0
    result=m.read('closure-synthetic001-summary.json')
    assert result['status']=='ATTEMPT006_STOPPED_PENDING_REVIEW'
    assert result['observer']==dict(status='NOT_STARTED',history_verified=False,source_stopped_in_all_observations=None)
    assert result['preparation']['status']=='NO_HOST_PREPARATION_COMMAND'
    assert result['deadline_readback']=='OLD005_DEADLINE_RETAINED' and result['phase6_complete'] is False
    assert not any(k in result for k in ('mandatory_stop_reason','initial_final_stack_read_rate_limited'))


@pytest.mark.parametrize('new',[False,True])
def test_unknown_schedule_response_reconciles_old_or_exact_frozen_deadline_without_fabricating_acceptance(tmp_path,monkeypatch,new):
    data,_=setup_root(tmp_path,monkeypatch);scheduled(tmp_path)
    m.write('schedule-update-once-unknown.json',dict(status='OUTCOME_UNKNOWN'))
    if new:new_deadline(data)
    result=checks.closure()
    assert result['status']=='ATTEMPT006_STOPPED_PENDING_REVIEW'
    assert result['deadline_readback']==('NEW_FROZEN_DEADLINE' if new else 'OLD005_DEADLINE_RETAINED')
    assert result['observer']['status']=='UNVERIFIED' and result['observer']['source_stopped_in_all_observations'] is None
    assert result['request_audit']['counts']['update-stack']==1
    assert result['request_audit']['unknown_files']==['schedule-update-once-unknown.json']
    assert not (tmp_path/'schedule-update-accepted.json').exists()


@pytest.mark.parametrize('fault',['new_without_request','unrelated_deadline','running_receipt','unrecorded_resize','double_update','wrong_update_deadline','forbidden_policy_write'])
def test_closure_conflicts_stay_unresolved(tmp_path,monkeypatch,fault):
    data,_=setup_root(tmp_path,monkeypatch)
    if fault=='new_without_request':contract(tmp_path);new_deadline(data)
    elif fault=='unrelated_deadline':
        scheduled(tmp_path);new_deadline(data);data['schedule']['ScheduleExpression']='at(2026-10-22T15:00:00)'
    elif fault=='running_receipt':
        for r in data['instances']['Reservations']:
            for i in r['Instances']:
                if i['InstanceId']==m.RECEIPT:i['State']['Name']='running'
    elif fault=='unrecorded_resize':
        for r in data['instances']['Reservations']:
            for i in r['Instances']:
                if i['InstanceId']==m.RECEIPT:i['InstanceType']='t4g.medium'
        data['template']['TemplateBody']=json.loads((m.VARIANT/'qualification-v1.json').read_bytes())
    elif fault=='double_update':
        scheduled(tmp_path);m.write('repeat-request.json',m.read('schedule-update-once-request.json'))
    elif fault=='wrong_update_deadline':
        scheduled(tmp_path);r=m.read('schedule-update-once-request.json');r['payload']['Parameters'][1]['ParameterValue']='at(2026-10-22T15:00:00)'
        (tmp_path/'schedule-update-once-request.json').write_text(json.dumps(r))  # synthetic-only mutation
    elif fault=='forbidden_policy_write':m.write('policy-request.json',dict(service='cloudformation',operation='set-stack-policy',payload={}))
    assert checks.closure()['status']=='RECONCILIATION_UNRESOLVED'


def genuine_observer(root,c):
    from app.research.phase6_object_store import ObjectWriter
    from app.research.phase6_anchor_validation import _hash
    op=m.live.operator;clock=[0]
    class API:
        def call(self,service,operation,payload):
            if operation=='get-caller-identity':return dict(Account=m.ACCOUNT,Arn=c['operator_arn'])
            if operation=='describe-stacks':return {'Stacks':[dict(StackId=m.STACK,StackStatus='UPDATE_COMPLETE')]}
            if operation=='describe-instances':return {'Reservations':[dict(OwnerId=m.ACCOUNT,Instances=[dict(InstanceId=i,State={'Name':'stopped'},Tags=[
                {'Key':'aws:cloudformation:stack-id','Value':m.STACK},{'Key':'aws:cloudformation:logical-id','Value':logical},
                {'Key':'Purpose','Value':'phase6-qualification-only'}]) for i,logical in [(m.SOURCE,'SourceInstance'),(m.RECEIPT,'ReceiptInstance')]])]}
            raise AssertionError(operation)
    writer=ObjectWriter(str(root/'operator'),owner=m.ACCOUNT,run_id=_hash(c),max_bytes=16*1024*1024)
    def sleep(_):clock[0]=5000 if clock[0]<5000 else 2700000
    answer=op.supervise(c,api=API(),writer=writer,utc_ms=lambda:c['not_before_utc_ms']+clock[0],
        elapsed_ns=lambda:clock[0]*1000000,sleep=sleep,armed=lambda _:clock.__setitem__(0,1))
    (root/'operator-stdout.txt').write_text(json.dumps(dict(status='ARMED'))+'\n'+json.dumps(answer)+'\n')
    (root/'operator-stderr.txt').write_bytes(b'')
    return answer


def test_real_watch_output_is_verified_separately_from_actual_stopped_readbacks(tmp_path,monkeypatch):
    data,_=setup_root(tmp_path,monkeypatch);c=scheduled(tmp_path);new_deadline(data)
    genuine_observer(tmp_path,c);result=checks.reconcile(tmp_path,snapshot(data))
    assert result['observer']['history_verified'] is True and result['observer']['source_stopped_in_all_observations'] is True
    assert result['observer']['recorded_reason']=='DEADLINE'
    assert result['observer']['clock_history_valid'] is True and result['phase6_complete'] is False
    event=next(p for p in (tmp_path/'operator').glob('*.blob'))
    event.chmod(0o600);event.write_text('{}')  # synthetic-only corruption
    result=checks.reconcile(tmp_path,snapshot(data))
    assert result['source_stopped_now'] is True and result['observer']['history_verified'] is False
    assert result['observer']['source_stopped_in_all_observations'] is None


def prep_records(root,*,success=True,logs=False):
    m.write('receipt-preparation-command.json',dict(command_id='synthetic-command',instance_id=m.RECEIPT))
    prepared=dict(status='HOST_PREPARED_PENDING_REVIEW',instance_id=m.RECEIPT,instance_type='t4g.medium')
    m.write('receipt-preparation-result.json',dict(Status='Success' if success else 'Failed',ResponseCode=0 if success else 1,
        CommandId='synthetic-command',InstanceId=m.RECEIPT,StandardOutputContent=json.dumps(prepared)))
    if logs:
        folder=root/'receipt-operations-exact';folder.mkdir(mode=0o700)
        raw=json.dumps(prepared).encode();(folder/'prepared.json').write_bytes(raw)
        inventory=dict(status='OPERATIONS_INVENTORY',files=[dict(name='prepared.json',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())])
        m.write('receipt-operations-inventory-command.json',dict(command_id='synthetic-inventory',instance_id=m.RECEIPT))
        m.write('receipt-operations-inventory-result.json',dict(Status='Success',ResponseCode=0,CommandId='synthetic-inventory',InstanceId=m.RECEIPT,StandardOutputContent=json.dumps(inventory)))
        m.write('receipt-exact-log-verification.json',dict(status='ALL_OPERATION_FILES_BYTE_HASH_VERIFIED'))


@pytest.mark.parametrize('kind',['missing','failed','success_without_logs','success_verified_logs','tampered_log','mismatched_command'])
def test_preparation_claims_never_promote_missing_or_partial_results(tmp_path,monkeypatch,kind):
    setup_root(tmp_path,monkeypatch)
    if kind=='missing':m.write('receipt-preparation-command.json',dict(command_id='synthetic-command',instance_id=m.RECEIPT))
    else:prep_records(tmp_path,success=kind!='failed',logs=kind in ('success_verified_logs','tampered_log','mismatched_command'))
    if kind=='tampered_log':(tmp_path/'receipt-operations-exact/prepared.json').write_text('{}')
    if kind=='mismatched_command':
        r=m.read('receipt-preparation-result.json');r['CommandId']='other';(tmp_path/'receipt-preparation-result.json').write_text(json.dumps(r))
    expected={'missing':'PREPARATION_OUTCOME_UNRESOLVED','failed':'PREPARATION_FAILED',
        'success_verified_logs':'HOST_PREPARED_PENDING_INDEPENDENT_REVIEW',
        'mismatched_command':'PREPARATION_RESULT_IDENTITY_UNVERIFIED'}.get(kind,'PREPARATION_REPORTED_SUCCESS_EVIDENCE_INCOMPLETE')
    assert checks.preparation_summary(tmp_path)['status']==expected


def test_create_root_refuses_bad_review_and_existing_root_before_writes(tmp_path,monkeypatch):
    root=tmp_path/'synthetic-root';monkeypatch.setattr(m,'ROOT',root)
    pins=dict(attempt='006',control_version=m.live.progress.VERSION,evidence_root=str(root),commit='f'*40,
        files={'deployment/phase6/receipt-update-states-v3/'+n:hashlib.sha256((BASE/n).read_bytes()).hexdigest() for n in m.REQUIRED_SOURCES})
    pins_path=tmp_path/'pins.json';pins_path.write_text(json.dumps(pins));pins_sha=hashlib.sha256(pins_path.read_bytes()).hexdigest()
    review=tmp_path/'review.md';review.write_text('SYNTHETIC TEST ONLY '+str(root)+' '+pins['commit']+' '+pins_sha)
    review_sha=hashlib.sha256(review.read_bytes()).hexdigest()
    monkeypatch.setattr(creator.subprocess,'check_output',lambda argv,**kw:pins['commit']+'\n' if argv[1]=='rev-parse' else '')
    with pytest.raises(ValueError):creator.create(review,'0'*64,pins_path,pins_sha,'SYNTHETIC TEST APPROVAL')
    assert not root.exists()
    result=creator.create(review,review_sha,pins_path,pins_sha,'SYNTHETIC TEST APPROVAL')
    assert result['aws_calls']==0 and result['window_started'] is False
    assert (root.stat().st_mode & 0o777)==0o700 and not list((root/'operator').iterdir())
    before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):creator.create(review,review_sha,pins_path,pins_sha,'SYNTHETIC TEST APPROVAL')
    after={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert before==after


def test_observation_gap_is_explicit_and_never_claims_continuity(tmp_path,monkeypatch):
    setup_root(tmp_path,monkeypatch);c=contract(tmp_path);genuine_observer(tmp_path,c)
    folder=tmp_path/'operator';events=sorted([json.loads(p.read_bytes()) for p in folder.glob('*.blob')],key=lambda e:e['sequence'])
    events[1]['kind']='observation_unavailable';events[1]['data']={k:v for k,v in events[1]['data'].items() if k in ('clock_available','utc_ms','continuous_ns')}
    # Rehash a clearly synthetic fixture with an unavailable sample.
    for path in folder.glob('*.blob'):path.unlink()
    previous='0'*64
    for e in events:
        e['previous_hash']=previous;raw=m.v2.canonical(e).encode();previous=hashlib.sha256(raw).hexdigest()
        (folder/(previous+'.blob')).write_bytes(raw)
    lines=(tmp_path/'operator-stdout.txt').read_text().splitlines();end=json.loads(lines[-1]);end['final_event_hash']=previous
    (tmp_path/'operator-stdout.txt').write_text(lines[0]+'\n'+json.dumps(end)+'\n')
    summary=checks.observer_summary(tmp_path)
    assert summary['observation_gaps']==1 and summary['continuous_source_state_proven'] is False
    assert summary['source_stopped_in_all_observations'] is True and summary['clock_history_valid'] is False
    assert 'source_stopped_throughout' not in summary


def test_mutation_audit_rejects_wrong_service_even_with_valid_operation(tmp_path,monkeypatch):
    data,_=setup_root(tmp_path,monkeypatch);scheduled(tmp_path)
    r=m.read('schedule-update-once-request.json');r['service']='ec2'
    (tmp_path/'schedule-update-once-request.json').write_text(json.dumps(r))
    with pytest.raises(ValueError):checks.request_audit(tmp_path)
    assert checks.closure()['status']=='RECONCILIATION_UNRESOLVED'


@pytest.mark.parametrize('fault',['orphan','failed_wrong_command','inventory_wrong_command','pending'])
def test_preparation_result_binding_and_partial_status(tmp_path,monkeypatch,fault):
    setup_root(tmp_path,monkeypatch)
    if fault=='orphan':
        m.write('receipt-preparation-result.json',dict(Status='Success',ResponseCode=0))
        expected='PREPARATION_EVIDENCE_CONFLICT'
    else:
        prep_records(tmp_path,success=fault!='failed_wrong_command',logs=True)
        filename='receipt-operations-inventory-result.json' if fault=='inventory_wrong_command' else 'receipt-preparation-result.json'
        r=m.read(filename)
        if fault=='pending':r['Status']='InProgress';expected='PREPARATION_OUTCOME_UNRESOLVED'
        else:
            r['CommandId']='another-command'
            expected='PREPARATION_REPORTED_SUCCESS_EVIDENCE_INCOMPLETE' if fault=='inventory_wrong_command' else 'PREPARATION_RESULT_IDENTITY_UNVERIFIED'
        (tmp_path/filename).write_text(json.dumps(r))
    assert checks.preparation_summary(tmp_path)['status']==expected


def test_schedule_refuses_before_mutation_when_prechecks_consume_wait_margin(tmp_path,monkeypatch):
    data,calls=setup_root(tmp_path,monkeypatch);checks.preflight();contract(tmp_path)
    procedure=load('package006_procedure_timing',BASE/'procedure.py');remaining=[2600]
    def health(min_seconds,**kwargs):
        if remaining[0]<min_seconds:raise ValueError('BELOW_FROZEN_MARGIN')
        return {'data':{'instances':{m.SOURCE:'stopped',m.RECEIPT:'stopped'}}}
    monkeypatch.setattr(m,'health',health);original=m.call
    def call(name,*args,**kwargs):
        result=original(name,*args,**kwargs)
        if name=='schedule-template-before':remaining[0]=2519
        return result
    monkeypatch.setattr(m,'call',call)
    with pytest.raises(ValueError,match='BELOW_FROZEN_MARGIN'):procedure.schedule()
    assert not any(op=='update-stack' for _,_,op,_ in calls)
    assert not (tmp_path/'schedule-update-once-request.json').exists()
