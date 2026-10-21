"""Actual005 readback replay plus synthetic supervisor-to-execute tests. No AWS."""
import copy
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

REPO=Path(__file__).resolve().parents[1]
BASE=REPO/'deployment/phase6/receipt-update-states-v3'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
live=load('live_states_v3_test',BASE/'execute-live.py')
progress,op=live.progress,live.operator
old=load('live_test_setup_v3',REPO/'tests/test_phase6_receipt_medium_live.py')
helper=old.helper
from app.research.phase6_anchor_validation import _hash
from app.research.phase6_object_store import ObjectWriter


def setup_history(root,states):
    sha,_,events=old.setup_operator(root)
    first=events[0];first['schema_version']=progress.EVENT_SCHEMA;first['data']['control_version']=progress.VERSION
    events=[first]
    for i,state in enumerate(states):
        offset=(len(states)-i-1)*1000
        events.append(dict(schema_version=progress.EVENT_SCHEMA,sequence=i+2,previous_hash='',kind='observation',
            data=dict(clock_available=True,instances={x:'stopped' for x in live.control.v1.INSTANCES.values()},
                stack_status=state,utc_ms=old.NOW-offset,continuous_ns=old.ELAPSED-offset*1000000)))
    old.write_events(root,events)
    cmd=[live.PYTHON,'-u',str(BASE/'watch-operator.py'),'--contract',str(root/'launch-contract.json'),
         '--sha256',sha,'--evidence-root',str(root/'operator'),'--execute-approved']
    check=live.Supervisor(root,sha,123,utc_ms=lambda:old.NOW,continuous_ns=lambda:old.ELAPSED,command=lambda pid:cmd)
    return check,events,cmd


def test_waiter_replays_actual005_transition_without_early_completion():
    folder=REPO/'tests/fixtures/phase6_update_states_v3';meta=json.loads((folder/'provenance.json').read_bytes())
    responses=[]
    for item in meta['files']:
        raw=(folder/item['fixture']).read_bytes();assert hashlib.sha256(raw).hexdigest()==item['sha256'];responses.append(json.loads(raw))
    states=[r['Stacks'][0]['StackStatus'] for r in responses]
    assert states==['UPDATE_IN_PROGRESS','UPDATE_IN_PROGRESS','UPDATE_COMPLETE_CLEANUP_IN_PROGRESS','UPDATE_COMPLETE']
    called=[];slept=[];latest=[states[0]];checks=[]
    def call(*args):
        value=responses[len(called)];called.append(args);latest[0]=value['Stacks'][0]['StackStatus'];return value
    def health(seconds,settled):
        checks.append((seconds,settled));return {'data':{'stack_status':latest[0]}}
    result=progress.wait_stack(call,health,stack_id=live.control.STACK,prefix='actual005-replay',min_seconds=2400,sleep=slept.append)
    assert result['StackStatus']=='UPDATE_COMPLETE' and len(called)==4 and slept==[5,5,5]
    assert checks==[(2400,False)]*8
    assert all(c[1:3]==('cloudformation','describe-stacks') for c in called)


@pytest.mark.parametrize('bad',['UPDATE_FAILED','UPDATE_ROLLBACK_IN_PROGRESS','UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS',
    'UPDATE_ROLLBACK_COMPLETE','UPDATE_ROLLBACK_FAILED','ROLLBACK_COMPLETE','REVIEW_IN_PROGRESS','DELETE_COMPLETE','CREATE_COMPLETE','UNKNOWN',None])
def test_shared_classifier_waiter_and_supervisor_reject_failure_or_unreviewed_state(tmp_path,bad):
    with pytest.raises(ValueError):progress.classify(bad)
    responses=iter(['UPDATE_COMPLETE_CLEANUP_IN_PROGRESS',bad]);calls=[]
    def call(*args):
        calls.append(args);return {'Stacks':[{'StackId':live.control.STACK,'StackStatus':next(responses)}]}
    with pytest.raises(ValueError):progress.wait_stack(call,lambda *a,**k:{'data':{'stack_status':'UPDATE_IN_PROGRESS'}},
        stack_id=live.control.STACK,prefix='negative',min_seconds=2400,sleep=lambda _:None)
    assert len(calls)==2
    check,_,_=setup_history(tmp_path,['UPDATE_COMPLETE_CLEANUP_IN_PROGRESS',bad,'UPDATE_COMPLETE'])
    with pytest.raises(ValueError):check()


def test_waiter_never_extends_deadline_or_returns_cleanup():
    calls=[];health_calls=[]
    def call(*args):calls.append(args);return {'Stacks':[{'StackId':live.control.STACK,'StackStatus':'UPDATE_COMPLETE_CLEANUP_IN_PROGRESS'}]}
    def health(seconds,settled):
        health_calls.append(seconds)
        if len(health_calls)==5:raise ValueError('FROZEN_DEADLINE')
        return {'data':{'stack_status':'UPDATE_COMPLETE_CLEANUP_IN_PROGRESS'}}
    with pytest.raises(ValueError,match='FROZEN_DEADLINE'):
        progress.wait_stack(call,health,stack_id=live.control.STACK,prefix='deadline',min_seconds=2400,sleep=lambda _:None)
    assert len(calls)==2 and health_calls==[2400]*5


def test_waiter_has_finite_poll_budget_and_requires_observer_settled():
    calls=[]
    def call(*args):calls.append(args);return {'Stacks':[{'StackId':live.control.STACK,'StackStatus':'UPDATE_COMPLETE'}]}
    with pytest.raises(TimeoutError):progress.wait_stack(call,lambda *a,**k:{'data':{'stack_status':'UPDATE_COMPLETE_CLEANUP_IN_PROGRESS'}},
        stack_id=live.control.STACK,prefix='bounded',min_seconds=2400,sleep=lambda _:None)
    assert len(calls)==36


def test_historical_cleanup_with_fresh_complete_allows_but_latest_cleanup_does_not(tmp_path):
    check,_,_=setup_history(tmp_path,['UPDATE_IN_PROGRESS','UPDATE_COMPLETE_CLEANUP_IN_PROGRESS','UPDATE_COMPLETE'])
    assert check()['stack_status']=='UPDATE_COMPLETE'
    second=tmp_path/'second';second.mkdir()
    check,_,_=setup_history(second,['UPDATE_IN_PROGRESS','UPDATE_COMPLETE_CLEANUP_IN_PROGRESS'])
    observed=check.observe(min_seconds=2400,settled=False)
    assert observed['stack_status']=='UPDATE_COMPLETE_CLEANUP_IN_PROGRESS'
    with pytest.raises(ValueError):check()
    fake=helper.FakeAPI()
    with pytest.raises(ValueError):live.control.execute_once(fake,expected_id=helper.CID,stop_expression=helper.STOP,
        supervisor=check,evidence_dir=str(second),now_ms=lambda:helper.NOW)
    assert not fake.calls


@pytest.mark.parametrize('fault',['old_schema','old_version','old_command','short_continuous','short_utc','stale','source_running','wrong_pair'])
def test_v3_supervisor_keeps_identity_and_deadline_boundaries(tmp_path,fault):
    check,events,cmd=setup_history(tmp_path,['UPDATE_COMPLETE_CLEANUP_IN_PROGRESS','UPDATE_COMPLETE'])
    if fault=='old_schema':
        for e in events:e['schema_version']='p6.qualification_operator_event.v1'
    elif fault=='old_version':events[0]['data']['control_version']='v1'
    elif fault=='old_command':cmd[:3]=[live.PYTHON,'-u','-m','app.research.phase6_qualification_operator']
    elif fault=='short_continuous':check.continuous_ns=lambda:old.ELAPSED+300000000000
    elif fault=='short_utc':check.utc_ms=lambda:old.NOW+300000
    elif fault=='stale':
        check.continuous_ns=lambda:old.ELAPSED+26000000000;check.utc_ms=lambda:old.NOW+26000
    elif fault=='source_running':events[-1]['data']['instances'][op.SOURCE]='running'
    elif fault=='wrong_pair':events[-1]['data']['instances']['i-'+'a'*17]='stopped'
    old.write_events(tmp_path,events)
    with pytest.raises(ValueError):check()


def test_receipt_transition_can_be_observed_but_never_execute_admitted(tmp_path):
    check,events,_=setup_history(tmp_path,['UPDATE_COMPLETE_CLEANUP_IN_PROGRESS','UPDATE_COMPLETE'])
    events[-1]['data']['instances'][op.RECEIPT]='running';old.write_events(tmp_path,events)
    assert check.observe(min_seconds=2100,receipt_transition=True)['instances'][op.RECEIPT]=='running'
    with pytest.raises(ValueError):check()


class Clock:
    def __init__(self):self.base=live.control.stop_time(helper.STOP)-2700000;self.now=0
    def utc(self):return self.base+self.now
    def continuous(self):return self.now*1000000


class WatchAPI:
    def __init__(self,c,clock,fault=None):
        self.c,self.clock,self.fault=c,clock,fault;self.calls=[];self.states={op.SOURCE:'stopped',op.RECEIPT:'stopped'}
    def call(self,service,operation,payload):
        self.calls.append((service,operation,copy.deepcopy(payload)))
        if operation=='get-caller-identity':return dict(Account=op.ACCOUNT,Arn=self.c['operator_arn'])
        if operation=='describe-stacks':
            t=self.clock.now
            state='UPDATE_COMPLETE' if t==0 or t>=15000 else ('UPDATE_IN_PROGRESS' if t<10000 else 'UPDATE_COMPLETE_CLEANUP_IN_PROGRESS')
            if self.fault in ('UPDATE_ROLLBACK_FAILED','UNKNOWN') and t>=15000:state=self.fault
            if self.fault=='stuck_cleanup' and t>=10000:state='UPDATE_COMPLETE_CLEANUP_IN_PROGRESS'
            return {'Stacks':[{'StackId':self.c['stack_id'],'StackStatus':state}]}
        if operation=='describe-instances':
            if self.clock.now>=5000 and self.fault in ('running','UPDATE_ROLLBACK_FAILED','UNKNOWN','stuck_cleanup') and not any(x[1]=='stop-instances' for x in self.calls):self.states[op.RECEIPT]='running'
            if self.fault=='source_running' and self.clock.now>=5000 and not any(x[1]=='stop-instances' for x in self.calls):self.states[op.SOURCE]='running'
            rows=[]
            for instance_id in payload.get('InstanceIds',sorted(self.states)):
                rows.append(dict(InstanceId=instance_id,State={'Name':self.states[instance_id]},Tags=[
                    {'Key':'aws:cloudformation:stack-id','Value':self.c['stack_id']},
                    {'Key':'aws:cloudformation:logical-id','Value':'SourceInstance' if instance_id==op.SOURCE else 'ReceiptInstance'},
                    {'Key':'Purpose','Value':'phase6-qualification-only'}]))
            return {'Reservations':[{'OwnerId':op.ACCOUNT,'Instances':rows}]}
        if operation=='stop-instances':
            for instance_id in payload['InstanceIds']:self.states[instance_id]='stopped'
            return {}
        raise AssertionError('Watch-only API unexpectedly used '+operation)


def watch_setup(root,fault=None):
    root.chmod(0o700);(root/'operator').mkdir(mode=0o700)
    clock=Clock()
    c=dict(schema_version='p6.qualification_launch.v1',account_id=op.ACCOUNT,region=op.REGION,
        stack_id=op.STACK,operator_arn='arn:aws:iam::'+op.ACCOUNT+':root',not_before_utc_ms=clock.base,
        stop_utc_ms=clock.base+2700000,template_sha256=live.control.TEMPLATE_SHA,runtime_bundle_sha256=live.KIT_SHA)
    raw=(json.dumps(c,indent=2)+'\n').encode();(root/'launch-contract.json').write_bytes(raw)
    sha=hashlib.sha256(raw).hexdigest()
    writer=ObjectWriter(str(root/'operator'),owner=op.ACCOUNT,run_id=_hash(c),max_bytes=16*1024*1024)
    return c,clock,WatchAPI(c,clock,fault),writer,sha


@pytest.mark.parametrize('fault',[None,'running','UPDATE_ROLLBACK_FAILED','UNKNOWN','stuck_cleanup','source_running'])
def test_watch_only_observer_handles_cleanup_without_weakening_protective_stop(tmp_path,fault):
    c,clock,api,writer,_=watch_setup(tmp_path,fault)
    def sleep(seconds):clock.now=clock.now+5000 if clock.now<20000 else clock.now+90000
    result=op.supervise(c,api=api,writer=writer,utc_ms=clock.utc,elapsed_ns=clock.continuous,sleep=sleep)
    events=sorted([json.loads(p.read_bytes()) for p in (tmp_path/'operator').glob('*.blob')],key=lambda e:e['sequence'])
    assert not any(x[1] in ('update-stack','start-instances','execute-change-set') for x in api.calls)
    stops=[x for x in api.calls if x[1]=='stop-instances']
    assert len(stops)==len({x[2]['InstanceIds'][0] for x in stops})
    reason=events[-1]['data']['reason']
    if fault in (None,'running','stuck_cleanup'):assert reason=='DEADLINE'
    elif fault=='source_running':assert reason=='PAIR_STATE_CHANGED'
    else:assert reason=='STACK_FAILURE'
    if fault in ('UNKNOWN','stuck_cleanup'):assert result['status']=='UNRESOLVED'
    else:assert result['status']=='STOP_CONFIRMED'
    if fault is not None:assert stops
    if fault in (None,'running','stuck_cleanup'):
        assert any(e['data'].get('stack_status')=='UPDATE_COMPLETE_CLEANUP_IN_PROGRESS' for e in events)
    assert events[0]['schema_version']==progress.EVENT_SCHEMA


def test_real_watch_chain_to_live_main_and_exact_execute_once_integration(tmp_path,monkeypatch):
    c,clock,watch,writer,sha=watch_setup(tmp_path)
    fake=helper.FakeAPI();calls=[];completed=[];errors=[]
    from subprocess import CompletedProcess
    def runner(argv,env):
        calls.append(argv[2]);payload=json.loads(argv[argv.index('--cli-input-json')+1])
        return CompletedProcess(argv,0,json.dumps(fake.call(argv[1],argv[2],payload)).encode(),b'')
    api=live.RecordedAPI(tmp_path,runner)
    command=[live.PYTHON,'-u',str(BASE/'watch-operator.py'),'--contract',str(tmp_path/'launch-contract.json'),
             '--sha256',sha,'--evidence-root',str(tmp_path/'operator'),'--execute-approved']
    checker=live.Supervisor(tmp_path,sha,123,utc_ms=clock.utc,continuous_ns=clock.continuous,command=lambda pid:command)
    monkeypatch.setattr(live,'RecordedAPI',lambda root:api);monkeypatch.setattr(live,'Supervisor',lambda *args:checker)
    original=live.control.execute_once
    monkeypatch.setattr(live.control,'execute_once',lambda *a,**k:original(*a,**k,now_ms=clock.utc))
    def sleep(seconds):
        if clock.now==10000:
            with pytest.raises(ValueError):checker()
            assert not calls
        if clock.now==15000:
            fake.preview['CreationTime']=datetime.fromtimestamp((clock.utc()-1000)/1000,timezone.utc).isoformat()
            args=['--evidence-root',str(tmp_path),'--contract-sha256',sha,'--operator-pid','123',
                  '--change-set-id',helper.CID,'--stop-expression',helper.STOP,'--execute-approved']
            try:completed.append(live.main(args))
            except BaseException:
                import traceback
                errors.append(traceback.format_exc());raise
            clock.now=2700000
        else:clock.now=(clock.now//5000+1)*5000
    # API/arming consumes time in reality; keep strict continuous-clock advance.
    result=op.supervise(c,api=watch,writer=writer,utc_ms=clock.utc,elapsed_ns=clock.continuous,sleep=sleep,
                        armed=lambda _:setattr(clock,"now",1))
    assert not errors, '\n'.join(errors)
    assert completed==[0] and result['status']=='STOP_CONFIRMED'
    assert calls[-2:]==['get-stack-policy','execute-change-set'] and len(fake.executions())==1
    assert json.loads((tmp_path/'medium-v3-execute-response.json').read_bytes())['resize_complete'] is False


def test_watch_cli_has_no_template_update_option():
    with pytest.raises(SystemExit) as e:op.main(['--template','anything'])
    assert e.value.code==2


procedure=load('v3_procedure_test',BASE/'procedure.py')
context=procedure.m


@pytest.mark.parametrize('fault',[None,'UPDATE_FAILED','UNKNOWN','stuck_cleanup'])
def test_actual_schedule_callsite_and_general_health_share_state_contract(tmp_path,monkeypatch,fault):
    check,events,_=setup_history(tmp_path,['UPDATE_COMPLETE'])
    clock=[0];check.utc_ms=lambda:old.NOW+clock[0];check.continuous_ns=lambda:old.ELAPSED+clock[0]*1000000
    fake=helper.FakeAPI();calls=[];polls=[]
    # All wire responses are synthetic. Real schedule/pair/health/waiter/chain logic runs.
    monkeypatch.setattr(context,'ROOT',tmp_path)
    monkeypatch.setattr(context,'pins',lambda:None)
    monkeypatch.setattr(context,'operator_pid',lambda:123)
    monkeypatch.setattr(context.live,'Supervisor',lambda *a:check)
    monkeypatch.setattr(procedure.time,'sleep',lambda _:None)
    for name,value in [('preflight-summary.json',dict(parameters={x['ParameterKey']:x['ParameterValue'] for x in fake.data['final-stack']['Stacks'][0]['Parameters']},resources=context.ids(fake.data['final-resources']))),
                       ('preflight2-policy-stdout.json',fake.data['final-stop-policy'])]:context.write(name,value)
    def schedule_ok(value,expression):
        assert value==fake.data['final-schedule'] and value['ScheduleExpression']==expression
    monkeypatch.setattr(context,'schedule_ok',schedule_ok)
    def call(name,service,operation,payload,**kw):
        calls.append((name,operation,copy.deepcopy(payload)))
        if operation=='update-stack':return {'StackId':context.STACK}
        if name.startswith('schedule-poll-'):
            state=['UPDATE_IN_PROGRESS','UPDATE_COMPLETE_CLEANUP_IN_PROGRESS','UPDATE_COMPLETE'][min(len(polls),2)]
            if fault and len(polls)>=2:state='UPDATE_COMPLETE_CLEANUP_IN_PROGRESS' if fault=='stuck_cleanup' else fault
            polls.append(state);clock[0]+=5000
            d=copy.deepcopy(events[-1]['data']);d.update(stack_status=state,utc_ms=old.NOW+clock[0],continuous_ns=old.ELAPSED+clock[0]*1000000)
            events.append(dict(schema_version=progress.EVENT_SCHEMA,sequence=len(events)+1,previous_hash='',kind='observation',data=d))
            old.write_events(tmp_path,events)
            return {'Stacks':[dict(StackId=context.STACK,StackStatus=state)]}
        return fake.call(service,operation,payload)
    monkeypatch.setattr(context,'call',call)
    if fault:
        with pytest.raises(ValueError):procedure.schedule()
        assert not (tmp_path/'schedule-update-verified.json').exists()
        assert not any(n.startswith('schedule-check') for n,_,_ in calls)
    else:
        procedure.schedule()
        assert polls==['UPDATE_IN_PROGRESS','UPDATE_COMPLETE_CLEANUP_IN_PROGRESS','UPDATE_COMPLETE']
        assert context.read('schedule-update-verified.json')['status']=='VERIFIED'
        assert any(n=='schedule-check-identity' for n,_,_ in calls)
    assert sum(op=='update-stack' for _,op,_ in calls)==1
    assert not any(op in ('create-change-set','execute-change-set','start-instances','send-command') for _,op,_ in calls)


def test_actual_interrupt_skips_malformed_unrelated_process_and_pin_failure(tmp_path,monkeypatch):
    import shlex
    check,_,command=setup_history(tmp_path,['UPDATE_COMPLETE'])
    monkeypatch.setattr(context,'ROOT',tmp_path)
    def fail_pins():raise ValueError('DIRTY_WORKTREE_OR_MISSING_KIT')
    monkeypatch.setattr(context,'pins',fail_pins)
    rows="99 1 python -c print('x)\n123 1 "+shlex.join(command)+'\n'
    monkeypatch.setattr(procedure.subprocess,'check_output',lambda *a,**k:rows)
    latest=max((tmp_path/'operator').glob('*.blob'),key=lambda p:json.loads(p.read_bytes())['sequence'])
    import os
    for file in (tmp_path/'operator').glob('*.blob'):
        os.utime(file,(old.NOW/1000-5,old.NOW/1000-5))
    os.utime(latest,(old.NOW/1000,old.NOW/1000))
    monkeypatch.setattr(procedure.time,'sleep',lambda _:pytest.fail('expected a fresh idle observation'))
    monkeypatch.setattr(procedure.time,'time',lambda:old.NOW/1000+0.5)
    signals=[];monkeypatch.setattr(procedure.os,'kill',lambda pid,sig:signals.append((pid,sig)))
    procedure.main(['interrupt'])
    assert signals==[(123,procedure.signal.SIGINT)]
    assert context.read('protective-interrupt-request.json')['signal_is_not_stop_confirmation'] is True
    with pytest.raises(ValueError,match='DIRTY_WORKTREE'):procedure.main(['schedule'])


def test_unapproved_candidate_root_refuses_before_any_aws_or_root_creation(tmp_path,monkeypatch):
    absent=tmp_path/'unapproved'
    monkeypatch.setattr(context,'ROOT',absent)
    calls=[];monkeypatch.setattr(context,'call',lambda *a,**k:calls.append(a))
    with pytest.raises((ValueError,FileNotFoundError)):procedure.main(['verify_policy'])
    assert not absent.exists() and not calls


def test_procedure_watch_and_execute_use_exact_v3_entrypoints():
    # Local AST check accompanies executable supervisor/main integration above.
    import ast
    tree=ast.parse((BASE/'procedure.py').read_text())
    functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
    for name,target in [('watch','receipt-update-states-v3/watch-operator.py'),('execute','receipt-update-states-v3/execute-live.py')]:
        assert any(isinstance(n,ast.Constant) and n.value==target for n in ast.walk(functions[name]))
    assert 'app.research.phase6_qualification_operator' not in (BASE/'procedure.py').read_text()
    assert 'set-stack-policy' not in (BASE/'procedure.py').read_text()
