"""Actual preview classification plus SYNTHETIC execution integration; never AWS.

The raw attempt004 reference remains unchanged. Fake execution changes only its
ARN/name and supplies a synthetic installed policy; this does not reauthorize004.
"""
import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[1]
BASE = REPO/'deployment/phase6/receipt-medium-control-v2'
spec = importlib.util.spec_from_file_location('medium_control_v2', BASE/'control.py')
control = importlib.util.module_from_spec(spec);spec.loader.exec_module(control)
STOP = 'at(2026-10-21T14:24:23)'
NOW = control.stop_time(STOP) - 2600000
CID = 'arn:aws:cloudformation:eu-north-1:989879505232:changeSet/mie-p6-receipt-medium-v2-005/11111111-1111-1111-1111-111111111111'


def actual():
    return json.loads((BASE/'preview-reference.json').read_bytes())


def synthetic_preview():
    p = actual()
    p['ChangeSetId'] = CID
    p['ChangeSetName'] = 'mie-p6-receipt-medium-v2-005'
    return p


def row(p, name):
    return next(r['ResourceChange'] for r in p['Changes'] if r['ResourceChange']['LogicalResourceId'] == name)


def classify(p):
    return control.validate_preview_content(p, STOP)


def test_actual_four_resource_preview_can_be_classified_without_execution_authority():
    answer = classify(actual())
    assert answer['cloud_executed'] is False and answer['execution_authorized'] is False


def test_v1_still_rejects_original_actual_preview():
    p = actual()
    with pytest.raises(ValueError):
        control.v1.validate_change_set(p, expected_change_set_id=p['ChangeSetId'],
            expected_stop_expression=STOP, template_request=dict(StackName=control.STACK,
            ChangeSetName=p['ChangeSetId'], TemplateStage='Original'),
            template_response={'TemplateBody':json.loads((control.V1/'qualification-v1.json').read_bytes())})


@pytest.mark.parametrize('fault', ['missing','extra','duplicate','replacement','large','profile','source',
    'stop_target','stop_role','iam_action','iam_resource','resolved_wrong_reference','unknown_context',
    'unknown_detail','nested','drift_mode','disable_rollback','parameters','pagination','duplicate_json'])
def test_preview_refuses_any_unreviewed_change(fault):
    p = synthetic_preview();r = row(p,'ReceiptInstance');d = r['Details'][0]
    if fault=='missing':p['Changes'].pop()
    elif fault=='extra':p['Changes'].append(dict(Type='Resource',ResourceChange=dict(LogicalResourceId='Other',Details=[])))
    elif fault=='duplicate':p['Changes'].append(copy.deepcopy(p['Changes'][0]))
    elif fault=='replacement':r['Replacement']='True'
    elif fault=='large':d['Target']['AfterValue']='t4g.large'
    elif fault=='profile':
        c=json.loads(r['AfterContext']);c['Properties']['IamInstanceProfile']='wrong';r['AfterContext']=json.dumps(c)
    elif fault=='source':r['LogicalResourceId']='SourceInstance'
    elif fault=='stop_target':
        r=row(p,'ExternalStop');c=json.loads(r['AfterContext']);c['Properties']['Target']['Arn']='wrong';r['AfterContext']=json.dumps(c)
    elif fault=='stop_role':
        r=row(p,'ExternalStop');c=json.loads(r['AfterContext']);c['Properties']['Target']['RoleArn']='wrong';r['AfterContext']=json.dumps(c)
    elif fault in ('iam_action','iam_resource'):
        r=row(p,'SourceRole');c=json.loads(r['AfterContext'])
        c['Properties']['Policies'][0]['PolicyDocument']['Statement'][0]['Action' if fault=='iam_action' else 'Resource']=['*'];r['AfterContext']=json.dumps(c)
    elif fault=='resolved_wrong_reference':row(p,'StopRole')['Details'][0]['Target']['AfterValue']='wrong-instance'
    elif fault=='unknown_context':
        c=json.loads(r['AfterContext']);c['Unreviewed']=True;r['AfterContext']=json.dumps(c)
    elif fault=='unknown_detail':d['Additional']='unexpected'
    elif fault=='nested':p['IncludeNestedStacks']=True
    elif fault=='drift_mode':p['DeploymentMode']='REVERT_DRIFT'
    elif fault=='disable_rollback':p['DeploymentConfig']['DisableRollback']=True
    elif fault=='parameters':p['Parameters'].append(copy.deepcopy(p['Parameters'][0]))
    elif fault=='pagination':p['NextToken']='next'
    elif fault=='duplicate_json':r['BeforeContext']='{"Properties":{},"Properties":{}}'
    with pytest.raises((ValueError,KeyError)):classify(p)


def test_resource_detail_order_and_json_whitespace_are_not_changes():
    p=synthetic_preview();p['Changes'].reverse()
    for x in p['Changes']:
        r=x['ResourceChange'];r['Details'].reverse()
        for key in ('BeforeContext','AfterContext'):r[key]=json.dumps(json.loads(r[key]),indent=4,sort_keys=True)
    classify(p)


def test_new_stop_deadline_is_the_only_reference_context_substitution():
    p=synthetic_preview();new='at(2026-10-22T12:00:00)'
    for x in p['Parameters']:
        if x['ParameterKey']=='StopAtUtc':x['ParameterValue']=new
    for key in ('BeforeContext','AfterContext'):
        r=row(p,'ExternalStop');v=json.loads(r[key]);v['Properties']['ScheduleExpression']=new;r[key]=json.dumps(v)
    control.validate_preview_content(p,new)
    with pytest.raises(ValueError):classify(p)


def policy_response():
    return {'StackPolicyBody':json.dumps(control.policy())}


@pytest.mark.parametrize('fault',['absent','broad','no_replace_deny','no_source_deny','wrong_principal','extra','duplicate','invalid'])
def test_changed_or_missing_policy_refuses_execution_request(fault):
    response=policy_response()
    p=control.policy()
    if fault=='absent':response={}
    elif fault=='broad':p['Statement'][0]['Action']='Update:*'
    elif fault=='no_replace_deny':p['Statement'].pop(1)
    elif fault=='no_source_deny':p['Statement'].pop()
    elif fault=='wrong_principal':p['Statement'][1]['Principal']='someone'
    elif fault=='extra':p['Statement'].append({'Effect':'Allow','Action':'Update:*','Principal':'*','Resource':'*'})
    elif fault=='duplicate':response={'StackPolicyBody':'{"Statement":[],"Statement":[]}'}
    elif fault=='invalid':response={'StackPolicyBody':'not json'}
    if fault not in ('absent','duplicate','invalid'):response={'StackPolicyBody':json.dumps(p)}
    with pytest.raises((ValueError,KeyError)):execution(policy=response)


def execution(p=None,**overrides):
    args=dict(expected_id=CID,stop_expression=STOP,
              template_request=dict(StackName=control.STACK,ChangeSetName=CID,TemplateStage='Original'),
              template_response={'TemplateBody':json.loads((control.V1/'qualification-v1.json').read_bytes())},
              policy_request={'StackName':control.STACK},policy_response=overrides.pop('policy',policy_response()))
    args.update(overrides)
    return control.execution_request(synthetic_preview() if p is None else p,**args)


def test_request_exact_new_arn_and_rollback_without_policy_override():
    req=execution()
    assert set(req)=={'StackName','ChangeSetName','ClientRequestToken','DisableRollback'}
    assert req['ChangeSetName']==CID and req['DisableRollback'] is False


@pytest.mark.parametrize('fault',['old004','wrong_id','wrong_name','wrong_template','wrong_template_request','wrong_policy_request'])
def test_execution_identity_bindings(fault):
    p=synthetic_preview();kw={}
    if fault=='old004':p=actual();kw['expected_id']=p['ChangeSetId']
    elif fault=='wrong_id':p['ChangeSetId']=CID.replace('11111111','22222222',1)
    elif fault=='wrong_name':p['ChangeSetName']='other'
    elif fault=='wrong_template':
        t=json.loads((control.V1/'qualification-v1.json').read_bytes());t['Resources']['SourceInstance']['Properties']['InstanceType']='t4g.medium';kw['template_response']={'TemplateBody':t}
    elif fault=='wrong_template_request':kw['template_request']={'StackName':control.STACK,'ChangeSetName':'latest','TemplateStage':'Original'}
    elif fault=='wrong_policy_request':kw['policy_request']={'StackName':'another'}
    with pytest.raises((ValueError,KeyError)):execution(p,**kw)


def test_policy_request_only_installs_when_absent_or_reports_exact_policy():
    req=control.policy_install_request({})
    assert req['StackName']==control.STACK
    assert control.policy_install_request(policy_response()) is None
    with pytest.raises(ValueError):control.policy_install_request({'StackPolicyBody':'{}'})


def policy_allows(action,resource):
    # Tiny test-only interpreter of this closed policy subset, not AWS simulation.
    decisions=[]
    for s in control.policy()['Statement']:
        actions=s['Action'] if isinstance(s['Action'],list) else [s['Action']]
        resources=s['Resource'] if isinstance(s['Resource'],list) else [s['Resource']]
        if (action in actions or 'Update:*' in actions) and ('*' in resources or 'LogicalResourceId/'+resource in resources):
            decisions.append(s['Effect'])
    return 'Allow' in decisions and 'Deny' not in decisions


@pytest.mark.parametrize('action',['Update:Replace','Update:Delete'])
@pytest.mark.parametrize('resource',list(control.pinned('resource-bindings.json')))
def test_policy_denies_replacement_deletion_for_all_existing_resources(action,resource):
    assert not policy_allows(action,resource)


@pytest.mark.parametrize('resource',list(control.pinned('resource-bindings.json')))
def test_policy_modify_allowlist(resource):
    assert policy_allows('Update:Modify',resource)==(resource in {'ExternalStop','ReceiptInstance','SourceRole','StopRole'})


class FakeAPI:
    def __init__(self):
        self.data=json.loads((REPO/'tests/fixtures/phase6_receipt_medium_control_v2/readbacks.json').read_bytes())
        self.preview=synthetic_preview();self.policy=policy_response();self.calls=[];self.fail_execute=False
        self.current_template=json.loads((BASE.parent/'qualification-v1.json').read_bytes())
        self.future_template=json.loads((control.V1/'qualification-v1.json').read_bytes())
    def call(self,service,operation,payload):
        self.calls.append((service,operation,copy.deepcopy(payload)))
        if operation=='execute-change-set':
            if self.fail_execute:raise TimeoutError('unknown outcome')
            return {}
        if operation=='get-caller-identity':return {'Account':control.ACCOUNT,'Arn':'arn:aws:iam::989879505232:root'}
        if operation=='get-stack-policy':return copy.deepcopy(self.policy)
        if operation=='describe-change-set':return copy.deepcopy(self.preview)
        if operation=='get-template':return {'TemplateBody':copy.deepcopy(self.future_template if 'ChangeSetName' in payload else self.current_template)}
        if operation=='get-role-policy':key='final-source-policy' if 'SourceRole-' in payload['RoleName'] else 'final-stop-policy'
        else:key={'describe-stacks':'final-stack','list-stack-resources':'final-resources','describe-instances':'final-instances','describe-volumes':'final-volumes','describe-instance-credit-specifications':'final-credits','get-schedule':'final-schedule'}[operation]
        return copy.deepcopy(self.data[key])
    def executions(self):return [c for c in self.calls if c[1]=='execute-change-set']


def supervisor():
    return dict(status='ARMED',stop_utc_ms=control.stop_time(STOP),stack_id=control.STACK,
                stack_status='UPDATE_COMPLETE',instances={i:'stopped' for i in control.v1.INSTANCES.values()},observed_utc_ms=NOW)


def invoke(api,path,check=supervisor):
    path.chmod(0o700)
    return control.execute_once(api,expected_id=CID,stop_expression=STOP,supervisor=check,evidence_dir=str(path),now_ms=lambda:NOW)


def test_fake_call_path_checks_actual_policy_last_and_executes_exact_arn_once(tmp_path):
    api=FakeAPI();result=invoke(api,tmp_path)
    assert len(api.executions())==1 and api.calls[-2][1]=='get-stack-policy'
    assert api.executions()[0][2]==execution()
    assert result['resize_complete'] is False
    assert (tmp_path/'medium-v2-execute-intent.json').is_file()
    before=len(api.calls)
    with pytest.raises(ValueError):invoke(api,tmp_path)
    assert len(api.calls)==before


@pytest.mark.parametrize('fault',['policy','source_running','source_medium','credits','volume','resources','current_template','future_template',
    'stop_target','stop_policy','source_policy','old_preview','future_preview','preview','stale_supervisor','short_window','late_supervisor'])
def test_fake_call_path_failure_makes_zero_execute_calls(tmp_path,fault):
    api=FakeAPI();check=supervisor
    rows=api.data['final-instances']['Reservations']
    instances={i['InstanceId']:i for r in rows for i in r['Instances']}
    if fault=='policy':api.policy={}
    elif fault=='source_running':instances[control.v1.INSTANCES['source']]['State']['Name']='running'
    elif fault=='source_medium':instances[control.v1.INSTANCES['source']]['InstanceType']='t4g.medium'
    elif fault=='credits':api.data['final-credits']['InstanceCreditSpecifications'][0]['CpuCredits']='unlimited'
    elif fault=='volume':api.data['final-volumes']['Volumes'][0]['Encrypted']=False
    elif fault=='resources':api.data['final-resources']['StackResourceSummaries'].pop()
    elif fault=='current_template':api.current_template['Description']='changed'
    elif fault=='future_template':api.future_template['Description']='changed'
    elif fault=='stop_target':api.data['final-schedule']['Target']['Input']='{"InstanceIds":["different"]}'
    elif fault=='stop_policy':api.data['final-stop-policy']['PolicyDocument']['Statement'][0]['Resource']=['*']
    elif fault=='source_policy':api.data['final-source-policy']['PolicyDocument']['Statement'][0]['Action']=['*']
    elif fault=='old_preview':api.preview['CreationTime']='2026-10-20T13:40:00+00:00'
    elif fault=='future_preview':api.preview['CreationTime']='2026-10-22T13:40:00+00:00'
    elif fault=='preview':api.preview['Changes'].pop()
    elif fault=='stale_supervisor':
        def check():return dict(supervisor(),observed_utc_ms=NOW-26000)
    elif fault=='short_window':
        def check():return dict(supervisor(),stop_utc_ms=NOW+1000)
    elif fault=='late_supervisor':
        count=[0]
        def check():
            count[0]+=1
            return dict(supervisor(),status='ARMED' if count[0]==1 else 'STOPPING')
    with pytest.raises((ValueError,KeyError)):invoke(api,tmp_path,check)
    assert api.executions()==[] and not (tmp_path/'medium-v2-execute-intent.json').exists()


def test_unknown_execute_response_preserves_intent_and_forbids_retry(tmp_path):
    api=FakeAPI();api.fail_execute=True
    with pytest.raises(TimeoutError):invoke(api,tmp_path)
    assert len(api.executions())==1
    with pytest.raises(ValueError):invoke(api,tmp_path)
    assert len(api.executions())==1


def test_evidence_fsync_failure_prevents_execute_and_consumes_intent(tmp_path,monkeypatch):
    api=FakeAPI()
    def fail(fd):raise OSError('disk unavailable')
    monkeypatch.setattr(control.os,'fsync',fail)
    with pytest.raises(OSError):invoke(api,tmp_path)
    assert not api.executions()
    assert (tmp_path/'medium-v2-execute-intent.json').exists()


def test_no_implicit_creation_of_evidence_directory(tmp_path):
    api=FakeAPI()
    with pytest.raises(FileNotFoundError):
        control.execute_once(api,expected_id=CID,stop_expression=STOP,supervisor=supervisor,evidence_dir=str(tmp_path/'absent'),now_ms=lambda:NOW)
    assert not api.calls and not (tmp_path/'absent').exists()
