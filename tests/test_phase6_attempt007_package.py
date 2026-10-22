"""Synthetic local tests of the actual007 entry points; never contact AWS."""
import copy
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
import pytest

REPO=Path(__file__).resolve().parents[1]
BASE=REPO/'deployment/phase6/receipt-attempt007'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
checks=load('checks007_tests',BASE/'checks.py');m=checks.m
creator=load('creator007_tests',BASE/'create-root.py')
procedure=load('procedure007_tests',BASE/'procedure.py')
guest=load('guest007_tests',BASE/'guest.py')
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
    assert result['status']=='ATTEMPT007_STOPPED_PENDING_REVIEW'
    assert result['observer']==dict(status='NOT_STARTED',history_verified=False,source_stopped_in_all_observations=None)
    assert result['preparation']['status']=='NO_HOST_PREPARATION_COMMAND'
    assert result['deadline_readback']=='BASELINE006_DEADLINE_RETAINED' and result['phase6_complete'] is False
    assert not any(k in result for k in ('mandatory_stop_reason','initial_final_stack_read_rate_limited'))


@pytest.mark.parametrize('new',[False,True])
def test_unknown_schedule_response_reconciles_old_or_exact_frozen_deadline_without_fabricating_acceptance(tmp_path,monkeypatch,new):
    data,_=setup_root(tmp_path,monkeypatch);scheduled(tmp_path)
    m.write('schedule-update-once-unknown.json',dict(status='OUTCOME_UNKNOWN'))
    if new:new_deadline(data)
    result=checks.closure()
    assert result['status']=='ATTEMPT007_STOPPED_PENDING_REVIEW'
    assert result['deadline_readback']==('NEW_FROZEN_DEADLINE' if new else 'BASELINE006_DEADLINE_RETAINED')
    assert result['observer']['status']=='NOT_ARMED' and result['observer']['source_stopped_in_all_observations'] is None
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
    (root/'operator-stdout.txt').write_text(json.dumps(dict(status='ARMED',process_control_version=m.CONTROL_VERSION))+'\n'+json.dumps(answer)+'\n')
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
    pins=dict(attempt='007',control_version=m.CONTROL_VERSION,package_version=m.PACKAGE_VERSION,evidence_root=str(root),commit='f'*40,
        files={n:hashlib.sha256((REPO/n).read_bytes()).hexdigest() for n in m.required_paths()})
    pins_path=tmp_path/'pins.json';pins_path.write_text(json.dumps(pins));pins_sha=hashlib.sha256(pins_path.read_bytes()).hexdigest()
    review=tmp_path/'review.md';review.write_text('SYNTHETIC TEST ONLY '+str(root)+' '+pins['commit']+' '+pins_sha)
    review_sha=hashlib.sha256(review.read_bytes()).hexdigest()
    monkeypatch.setattr(creator.subprocess,'check_output',lambda argv,**kw:pins['commit']+'\n' if argv[1]=='rev-parse' else '')
    with pytest.raises(ValueError):creator.create(review,'0'*64,pins_path,pins_sha,'SYNTHETIC TEST APPROVAL')
    assert not root.exists()
    def local_probe():
        assert not root.exists()
        return dict(status='LOCAL_PROCESS_PREFLIGHT_PASS',aws_calls=0,window_started=False)
    monkeypatch.setattr(m,'local_preflight',local_probe)
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
    procedure=load('package007_procedure_timing',BASE/'procedure.py');remaining=[2600]
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


def test_unique_context_is_shared_by007_and_not_006():
    old=load('attempt006_isolation',REPO/'deployment/phase6/receipt-update-states-v3/checks.py')
    assert old.m is not m
    assert creator.m is procedure.m is guest.m is m
    assert m.ROOT.name.endswith('007') and old.m.ROOT.name.endswith('006')


@pytest.mark.parametrize('marker',['operation-closure.json','closed-files-inventory.json'])
def test_closed_root_refuses_write_call_pins_interrupt_and_closure(tmp_path,monkeypatch,marker):
    tmp_path.chmod(0o700);monkeypatch.setattr(m,'ROOT',tmp_path)
    (tmp_path/marker).write_text('{}')
    monkeypatch.setattr(m.live,'bounded_cli',lambda *a,**k:pytest.fail('NO AWS'))
    before=list(tmp_path.iterdir())
    for op in [lambda:m.write('new.json',{}),lambda:m.call('x','sts','get-caller-identity',{}),
               m.pins,procedure.interrupt,checks.closure,guest.decode]:
        with pytest.raises(ValueError):op()
    assert list(tmp_path.iterdir())==before


def test_watch_native_failure_precedes_contract_window_and_all_aws(tmp_path,monkeypatch):
    setup_root(tmp_path,monkeypatch)
    m.write('kit-version-verification.json',dict(status='EXACT_VERSION_BYTES_VERIFIED'))
    monkeypatch.setattr(m,'call',lambda *a,**kw:pytest.fail('AWS before native preflight'))
    monkeypatch.setattr(m,'local_preflight',lambda:(_ for _ in ()).throw(ValueError('native denied')))
    with pytest.raises(ValueError,match='native denied'):procedure.watch()
    assert not (tmp_path/'launch-contract.json').exists()
    assert not (tmp_path/'process-launch.json').exists()


class ExecCaptured(Exception):pass


def test_watch_publishes_exact_v4_launch_before_exec(tmp_path,monkeypatch):
    setup_root(tmp_path,monkeypatch);sequence=[]
    m.write('kit-version-verification.json',dict(status='EXACT_VERSION_BYTES_VERIFIED'))
    def probe():
        assert not (tmp_path/'launch-contract.json').exists();sequence.append('probe')
        return dict(status='LOCAL_PROCESS_PREFLIGHT_PASS')
    monkeypatch.setattr(m,'local_preflight',probe)
    def call(name,*args,**kwargs):
        assert sequence[0]=='probe';sequence.append(name)
        if name=='watch-identity':return dict(Arn='arn:aws:iam::'+m.ACCOUNT+':root')
        return dict(Stacks=[dict(StackId=m.STACK,StackStatus='UPDATE_COMPLETE')])
    monkeypatch.setattr(m,'call',call);monkeypatch.setattr(m,'pair',lambda *a:None)
    monkeypatch.setattr(procedure.os,'dup2',lambda *a:None);monkeypatch.setattr(procedure.os,'chdir',lambda *a:None)
    def execve(exe,argv,env):
        sha,expected=m.observer_command();launch=m.read('process-launch.json')
        assert launch==dict(initial_pid=os.getpid(),uid=os.getuid(),argv=expected,contract_sha256=sha,control_version=m.CONTROL_VERSION)
        assert expected[2]==str(m.PROCESS_DIR/'watch-operator.py')
        assert argv==['/usr/bin/caffeinate','-i','-t','2880',*expected]
        assert m.read('launch-contract.json')['stop_utc_ms']-m.read('launch-contract.json')['not_before_utc_ms']==2700000
        assert not (tmp_path/'process-identity.json').exists()
        raise ExecCaptured()
    monkeypatch.setattr(procedure.os,'execve',execve)
    with pytest.raises(ExecCaptured):procedure.main(['watch'])


def test_execute_uses_native_lookup_and_v4_adapter(tmp_path,monkeypatch):
    setup_root(tmp_path,monkeypatch);contract(tmp_path)
    m.write('medium-preview-verified.json',dict(ChangeSetName='synthetic007'))
    m.write('medium-change-set-id.json',dict(change_set_id='synthetic007'))
    monkeypatch.setattr(m,'health',lambda *a,**kw:None)
    calls=[]
    monkeypatch.setattr(m.process,'lookup',lambda root,sha:calls.append((root,sha)) or 12345)
    monkeypatch.setattr(procedure.os,'dup2',lambda *a:None);monkeypatch.setattr(procedure.os,'chdir',lambda *a:None)
    def execve(exe,argv,env):
        assert argv[1]==str(m.PROCESS_DIR/'execute-live.py')
        assert argv[argv.index('--operator-pid')+1]=='12345'
        assert len(calls)==1 and calls[0][0]==tmp_path
        raise ExecCaptured()
    monkeypatch.setattr(procedure.os,'execve',execve)
    with pytest.raises(ExecCaptured):procedure.execute()


def test_interrupt_uses_v4_without_preparation_pins(tmp_path,monkeypatch):
    setup_root(tmp_path,monkeypatch);contract(tmp_path)
    monkeypatch.setattr(m,'pins',lambda:pytest.fail('protective action must survive source drift'))
    calls=[];monkeypatch.setattr(m.process,'interrupt',lambda root,sha:calls.append((root,sha)) or {'status':'REQUESTED'})
    procedure.main(['interrupt'])
    assert calls==[(tmp_path,m.observer_command()[0])]


def resize_fixture(tmp_path,monkeypatch,states=('pending','running'),online=('ConnectionLost','Online'),initial='stopped'):
    data,_=setup_root(tmp_path,monkeypatch);calls=[];gates=[];remaining=[2400]
    monkeypatch.setattr(procedure,'wait_stack',lambda prefix,minimum:gates.append(minimum))
    monkeypatch.setattr(procedure,'fresh_protection',lambda prefix,minimum,*args:gates.append(minimum))
    monkeypatch.setattr(procedure.time,'sleep',lambda _:None)
    def health(minimum,**kwargs):
        gates.append(minimum)
        if remaining[0]<minimum:raise ValueError('INSUFFICIENT_REMAINING')
        return {'data':{'instances':{m.SOURCE:'stopped',m.RECEIPT:initial}}}
    monkeypatch.setattr(m,'health',health)
    def instance_response(state):
        value=copy.deepcopy(data['instances'])
        for r in value['Reservations']:
            for i in r['Instances']:
                if i['InstanceId']==m.RECEIPT:i['State']['Name']=state;i['InstanceType']='t4g.medium'
        return value
    def call(name,service,op,payload,**kwargs):
        calls.append((name,op))
        if op=='start-instances':return dict(StartingInstances=[dict(InstanceId=m.RECEIPT,PreviousState={'Name':'stopped'},CurrentState={'Name':'pending'})])
        if name=='resize-state':return instance_response(initial)
        if name.startswith('receipt-ready-'):n=int(name.split('-')[2])
        else:n=0
        if op=='describe-instances':return instance_response(states[min(n,len(states)-1)])
        if op=='describe-volumes':return copy.deepcopy(data['volumes'])
        if op=='describe-instance-credit-specifications':return copy.deepcopy(data['credits'])
        if op=='describe-instance-information':return dict(InstanceInformationList=[dict(InstanceId=m.RECEIPT,PingStatus=online[min(n,len(online)-1)])])
        if op=='send-command':return dict(Command=dict(InstanceIds=[m.RECEIPT],CommandId='synthetic-admission007'))
        raise AssertionError(op)
    monkeypatch.setattr(m,'call',call)
    return calls,gates,remaining


@pytest.mark.parametrize('initial',['stopped','running'])
def test_actual_after_resize_waits_for_running_online_before_admission(tmp_path,monkeypatch,initial):
    calls,gates,remaining=resize_fixture(tmp_path,monkeypatch,initial=initial)
    procedure.main(['after_resize'])
    assert sum(op=='start-instances' for _,op in calls)==(initial=='stopped')
    assert m.read('receipt-readiness-verified.json')['read_batches']==2
    assert gates[:3]==([2300,2300,2300] if initial=='stopped' else [2300,2300,2230])
    assert not any(op=='send-command' for _,op in calls)
    original=m.call
    def call(name,service,op,payload,**kw):
        if name=='admission-ssm-online':return dict(InstanceInformationList=[dict(InstanceId=m.RECEIPT,PingStatus='Online')])
        return original(name,service,op,payload,**kw)
    monkeypatch.setattr(m,'call',call);monkeypatch.setattr(m,'pair',lambda *a:None)
    guest.admission()
    assert sum(op=='send-command' for _,op in calls)==1
    assert m.read('receipt-admission-command.json')['operation_attempt']=='007'


@pytest.mark.parametrize('remaining',[2140,2229,2230,2299])
def test_no_start_when_onward_readiness_margin_is_unavailable(tmp_path,monkeypatch,remaining):
    calls,gates,clock=resize_fixture(tmp_path,monkeypatch);clock[0]=remaining
    with pytest.raises(ValueError,match='INSUFFICIENT_REMAINING'):procedure.after_resize()
    assert not any(op=='start-instances' for _,op in calls)


@pytest.mark.parametrize('state',['stopped','stopping','terminated','shutting-down'])
def test_readiness_never_restarts_after_accepted_start(tmp_path,monkeypatch,state):
    calls,_,_=resize_fixture(tmp_path,monkeypatch,states=(state,))
    with pytest.raises(AssertionError):procedure.after_resize()
    assert sum(op=='start-instances' for _,op in calls)==1
    assert not (tmp_path/'receipt-readiness-verified.json').exists()


@pytest.mark.parametrize('fault',['source_running','type','profile','volume','credit','duplicate','ssm_other','ssm_page','time_after_reads','unknown_read','never_online'])
def test_readiness_faults_block_guest_delivery_without_retries(tmp_path,monkeypatch,fault):
    calls,_,remaining=resize_fixture(tmp_path,monkeypatch,states=('running',),online=('Online',))
    original=m.call
    def call(name,service,op,payload,**kwargs):
        if fault=='unknown_read' and name=='receipt-ready-0-ssm':raise TimeoutError('unknown read')
        value=original(name,service,op,payload,**kwargs)
        if name.startswith('receipt-ready-'):
            if op=='describe-instances':
                rows=[i for r in value['Reservations'] for i in r['Instances']]
                source=next(i for i in rows if i['InstanceId']==m.SOURCE);receipt=next(i for i in rows if i['InstanceId']==m.RECEIPT)
                if fault=='source_running':source['State']['Name']='running'
                if fault=='type':receipt['InstanceType']='t4g.small'
                if fault=='profile':receipt['IamInstanceProfile']['Arn']='wrong'
                if fault=='duplicate':value['Reservations'][0]['Instances'].append(copy.deepcopy(receipt))
            if op=='describe-volumes' and fault=='volume':value['Volumes'][0]['Encrypted']=False
            if op=='describe-instance-credit-specifications' and fault=='credit':value['InstanceCreditSpecifications'][0]['CpuCredits']='unlimited'
            if op=='describe-instance-information':
                if fault=='ssm_other':value['InstanceInformationList'][0]['InstanceId']='wrong'
                if fault=='ssm_page':value['NextToken']='more'
                if fault=='time_after_reads':remaining[0]=2229
                if fault=='never_online':value['InstanceInformationList'][0]['PingStatus']='ConnectionLost'
        return value
    monkeypatch.setattr(m,'call',call)
    with pytest.raises((ValueError,AssertionError,TimeoutError,RuntimeError)):procedure.after_resize()
    assert sum(op=='start-instances' for _,op in calls)==1
    assert not (tmp_path/'receipt-readiness-verified.json').exists()
    assert not any(op=='send-command' for _,op in calls)
    if fault=='never_online':assert sum(op=='describe-instance-information' for _,op in calls)==12


def process_archive(root):
    c=contract(root);sha=m.observer_command()[0];pin=m.binding.read(m.PROCESS_DIR/'macos-python-pin.json')
    pins=dict(attempt=m.ATTEMPT,control_version=m.CONTROL_VERSION,package_version=m.PACKAGE_VERSION,
        evidence_root=str(root),commit='f'*40,files={n:hashlib.sha256((REPO/n).read_bytes()).hexdigest() for n in m.required_paths()})
    m.write('approved-source-pins.json',pins);m.write('EXECUTION-REVIEW.md',b'SYNTHETIC ONLY')
    m.write('interpreter-pin.json',(m.PROCESS_DIR/'macos-python-pin.json').read_bytes())
    auth=dict(approved=True,owner='Anteneh Hailu',attempt=m.ATTEMPT,package_version=m.PACKAGE_VERSION,
        evidence_root=str(root),source_commit=pins['commit'],control_version=m.CONTROL_VERSION)
    for name,field in [('approved-source-pins.json','source_pins_sha256'),('EXECUTION-REVIEW.md','review_sha256'),('interpreter-pin.json','interpreter_pin_sha256')]:
        auth[field]=hashlib.sha256((root/name).read_bytes()).hexdigest()
    m.write('authorization.json',auth)
    _,argv=m.observer_command();launch=dict(initial_pid=12345,uid=os.getuid(),argv=argv,contract_sha256=sha,control_version=m.CONTROL_VERSION)
    m.write('process-launch.json',launch)
    native=m.binding.identity;expected=native.expectation(pid=12345,uid=os.getuid(),argv=argv,
        script_sha256=pins['files']['deployment/phase6/receipt-process-identity-v4/watch-operator.py'],root=root,contract_sha256=sha,pin=pin)
    birth=dict(pid=12345,uid=os.getuid(),ruid=os.getuid(),svuid=os.getuid(),start_sec=1790000000,start_usec=123456,
        boot_uuid='00000000-0000-0000-0000-000000000001',kernel_executable=pin['kernel_executable'],
        executable_sha256=pin['kernel_sha256'],argv=[pin['kernel_executable'],*argv[1:]])
    m.write('process-identity.json',dict(schema_version=native.SCHEMA,expectation_sha256=native.digest(expected),birth=birth))


def test_archived_binding_closure_never_queries_live_pid_or_current_source(tmp_path,monkeypatch):
    setup_root(tmp_path,monkeypatch);process_archive(tmp_path)
    monkeypatch.setattr(m.binding.identity,'snapshot',lambda *a:pytest.fail('No live PID at closure'))
    monkeypatch.setattr(m.binding,'source_pins',lambda *a:pytest.fail('No current source requirement at closure'))
    monkeypatch.setattr(m.binding.identity,'file_hash',lambda *a:pytest.fail('No current interpreter requirement at closure'))
    result=checks.process_summary(tmp_path)
    assert result['archive_verified'] is True and result['live_process_checked'] is False


@pytest.mark.parametrize('fault',['pid','uid','argv','contract','digest','birth','pin','auth','source_manifest'])
def test_archive_mismatch_never_becomes_verified(tmp_path,monkeypatch,fault):
    setup_root(tmp_path,monkeypatch);process_archive(tmp_path)
    name='process-identity.json';value=m.read(name)
    if fault in ('pid','uid','argv','contract'):
        name='process-launch.json';value=m.read(name)
        if fault=='pid':value['initial_pid']+=1
        elif fault=='uid':value['uid']+=1
        elif fault=='argv':value['argv'][-1]='--other'
        else:value['contract_sha256']='0'*64
    elif fault=='digest':value['expectation_sha256']='0'*64
    elif fault=='birth':value['birth']['start_usec']=-1
    elif fault=='pin':name='interpreter-pin.json';value={}
    elif fault=='auth':name='authorization.json';value=m.read(name);value['attempt']='006'
    elif fault=='source_manifest':name='approved-source-pins.json';value={}
    (tmp_path/name).write_text(json.dumps(value))  # synthetic fixture only
    assert checks.process_summary(tmp_path)['status']=='PROCESS_BINDING_UNVERIFIED'


def test_startup_binding_absence_is_separate_from_stopped_readback(tmp_path,monkeypatch):
    data,_=setup_root(tmp_path,monkeypatch);contract(tmp_path)
    result=checks.reconcile(tmp_path,snapshot(data))
    assert result['status']=='ATTEMPT007_STOPPED_PENDING_REVIEW'
    assert result['observer']['status']=='NOT_ARMED'
    assert result['process_identity']==dict(status='IDENTITY_NOT_PUBLISHED',archive_verified=False)
    (tmp_path/'operator/writer.json').write_text('{}')
    assert checks.reconcile(tmp_path,snapshot(data))['observer']['status']=='UNVERIFIED'


def test_baseline006_has_all14_exact_historical_copies():
    value=json.loads((BASE/'baseline006.json').read_bytes())
    assert len(value['data'])==len(value['sources'])==14
    for key,source in value['sources'].items():
        raw=Path(source['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==source['sha256'] and len(raw)==source['bytes']
        assert json.loads(raw)==value['data'][key]


def test_create_native_failure_cannot_create_root(tmp_path,monkeypatch):
    root=tmp_path/'never-created';monkeypatch.setattr(m,'ROOT',root)
    manifest=dict(commit='f'*40)
    pins=tmp_path/'pins.json';pins.write_text(json.dumps(manifest))
    ph=hashlib.sha256(pins.read_bytes()).hexdigest()
    review=tmp_path/'review.md';review.write_text(str(root)+' '+manifest['commit']+' '+ph)
    rh=hashlib.sha256(review.read_bytes()).hexdigest()
    monkeypatch.setattr(m,'validate_manifest',lambda _:None)
    def probe():
        assert not root.exists()
        raise ValueError('native permission unavailable')
    monkeypatch.setattr(m,'local_preflight',probe)
    with pytest.raises(ValueError,match='native permission unavailable'):creator.create(review,rh,pins,ph,'SYNTHETIC ONLY')
    assert not root.exists()


def test_context_health_routes_identity_and_observation_through_v4(tmp_path,monkeypatch):
    setup_root(tmp_path,monkeypatch);contract(tmp_path);calls=[]
    monkeypatch.setattr(m.process,'lookup',lambda root,sha:calls.append(('lookup',root,sha)) or 12345)
    class Supervisor:
        def __init__(self,root,sha,pid):calls.append(('supervisor',root,sha,pid))
        def observe(self,**kwargs):calls.append(('observe',kwargs));return dict(ok=True)
    monkeypatch.setattr(m.live,'Supervisor',Supervisor)
    assert m.health(2300)==dict(data=dict(ok=True))
    assert [c[0] for c in calls]==['lookup','supervisor','observe']
    assert calls[1][-1]==12345
    assert calls[2][1]==dict(min_seconds=2300,settled=True,receipt_transition=True)


def test_local_preflight_requires_pid_inventory_permission(monkeypatch):
    monkeypatch.setattr(m.native_probe,'run',lambda pin:dict(status='LOCAL_PROCESS_PREFLIGHT_PASS',aws_calls=0,window_started=False))
    def denied(*a,**kw):raise PermissionError('ps denied')
    monkeypatch.setattr(m.subprocess,'check_output',denied)
    with pytest.raises(PermissionError):m.local_preflight()


def test_prestart_old006_threshold_cannot_reach_guest_admission():
    # Fixed negative baseline: at2140s old after_resize admitted a start but its
    # immediate guest admission2230s gate could never pass. New start gates2300s.
    old=(REPO/'deployment/phase6/receipt-update-states-v3/procedure.py').read_text()
    old_guest=(REPO/'deployment/phase6/receipt-update-states-v3/guest.py').read_text()
    assert 'm.health(2140)' in old and 'm.health(2230)' in old_guest
    assert 2140<2230<=procedure.READY_MIN_SECONDS<procedure.START_MIN_SECONDS
