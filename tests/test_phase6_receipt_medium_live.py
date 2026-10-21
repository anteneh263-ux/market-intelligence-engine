"""Local subprocess/adapter tests only; all AWS responses are synthetic."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

REPO = Path(__file__).resolve().parents[1]
BASE = REPO/'deployment/phase6/receipt-medium-control-v2'
spec = importlib.util.spec_from_file_location('medium_live', BASE/'execute-live.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
spec2 = importlib.util.spec_from_file_location('control_test_helpers', REPO/'tests/test_phase6_receipt_medium_control_v2.py')
helper = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(helper)
NOW, STOP, CID = helper.NOW, helper.STOP, helper.CID
ELAPSED = 1000000000000


def setup_operator(root):
    root.chmod(0o700)
    (root/'operator').mkdir(mode=0o700)
    c = dict(schema_version='p6.qualification_launch.v1', account_id=live.control.ACCOUNT,
        region='eu-north-1', stack_id=live.control.STACK, operator_arn='arn:aws:iam::989879505232:root',
        not_before_utc_ms=live.control.stop_time(STOP)-2700000, stop_utc_ms=live.control.stop_time(STOP),
        template_sha256=live.control.TEMPLATE_SHA, runtime_bundle_sha256=live.KIT_SHA)
    raw = (json.dumps(c,indent=2)+'\n').encode()
    (root/'launch-contract.json').write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    header = dict(schema_version='p6.object_store.v1', owner=live.control.ACCOUNT,
        run_id=hashlib.sha256(live.control.canonical(c).encode()).hexdigest(),max_bytes=16*1024*1024)
    (root/'operator/writer.json').write_text(json.dumps(header))
    pair={i:'stopped' for i in live.control.v1.INSTANCES.values()}
    event1=dict(schema_version='p6.qualification_operator_event.v1', sequence=1,previous_hash='0'*64,kind='armed',
        data=dict(contract=c,observed_instances=pair,qualification_only=True,utc_ms=c['not_before_utc_ms'],
                  continuous_ns=ELAPSED-(NOW-c['not_before_utc_ms'])*1000000))
    event2=dict(schema_version=event1['schema_version'],sequence=2,previous_hash='',kind='observation',
        data=dict(clock_available=True,instances=pair,stack_status='UPDATE_COMPLETE',utc_ms=NOW,continuous_ns=ELAPSED))
    events=[event1,event2]
    write_events(root,events)
    cmd=[live.PYTHON,'-u','-m','app.research.phase6_qualification_operator','--contract',str(root/'launch-contract.json'),
         '--sha256',sha,'--evidence-root',str(root/'operator'),'--execute-approved']
    return sha,cmd,events


def write_events(root,events):
    # Synthetic test folder only; never runtime evidence.
    for p in (root/'operator').glob('*.blob'):p.unlink()
    prev='0'*64
    for e in events:
        e['previous_hash']=prev
        raw=live.control.canonical(e).encode();prev=hashlib.sha256(raw).hexdigest()
        (root/'operator'/f'{prev}.blob').write_bytes(raw)


def supervisor(root,sha,cmd,**kw):
    args=dict(utc_ms=lambda:NOW,continuous_ns=lambda:ELAPSED,command=lambda pid:cmd)
    args.update(kw)
    return live.Supervisor(root,sha,123,**args)


def test_authentic_shape_supervisor_binds_chain_contract_process_and_continuous_clock(tmp_path):
    sha,cmd,_=setup_operator(tmp_path)
    result=supervisor(tmp_path,sha,cmd)()
    assert result['status']=='ARMED' and result['continuous_remaining_ns']==2600000000000


@pytest.mark.parametrize('fault',['header','contract','wrong_process','stopped_process','source_running','stop_event',
    'unavailable','clock_false','clock_backwards','clock_stalled','drift','clock_stale','continuous_expired',
    'sequence','chain','blob_hash','latest_update','symlink'])
def test_supervisor_refuses_unhealthy_or_unbound_state(tmp_path,fault):
    sha,cmd,events=setup_operator(tmp_path);kw={}
    if fault=='header':(tmp_path/'operator/writer.json').write_text('{}')
    elif fault=='contract':(tmp_path/'launch-contract.json').write_text('{}')
    elif fault=='wrong_process':cmd=cmd+['--template','unapproved']
    elif fault=='stopped_process':kw['command']=lambda pid:[]
    elif fault=='source_running':events[1]['data']['instances'][live.control.v1.INSTANCES['source']]='running'
    elif fault=='stop_event':events[1]['kind']='stop_requested'
    elif fault=='unavailable':events[1]['kind']='observation_unavailable'
    elif fault=='clock_false':events[1]['data']['clock_available']=False
    elif fault=='clock_backwards':events[1]['data']['utc_ms']=events[0]['data']['utc_ms']-1
    elif fault=='clock_stalled':events[1]['data']['continuous_ns']=events[0]['data']['continuous_ns']
    elif fault=='drift':events[1]['data']['continuous_ns']-=3000000000
    elif fault=='clock_stale':kw=dict(utc_ms=lambda:NOW+26000,continuous_ns=lambda:ELAPSED+26000000000)
    elif fault=='continuous_expired':kw['continuous_ns']=lambda:ELAPSED+2700000000000
    elif fault=='sequence':events[1]['sequence']=3
    elif fault=='latest_update':events[1]['data']['stack_status']='UPDATE_IN_PROGRESS'
    if fault not in ('header','contract'):write_events(tmp_path,events)
    if fault=='chain':
        p=next(p for p in (tmp_path/'operator').glob('*.blob') if json.loads(p.read_bytes())['sequence']==2)
        e=json.loads(p.read_bytes());e['previous_hash']='f'*64;p.unlink();raw=live.control.canonical(e).encode()
        (tmp_path/'operator'/(hashlib.sha256(raw).hexdigest()+'.blob')).write_bytes(raw)
    if fault=='blob_hash':next((tmp_path/'operator').glob('*.blob')).write_text('{}')
    if fault=='symlink':
        p=next((tmp_path/'operator').glob('*.blob'));raw=p.read_bytes();p.unlink()
        target=tmp_path/'synthetic-link-target';target.write_bytes(raw);p.symlink_to(target)
    with pytest.raises((ValueError,KeyError)):supervisor(tmp_path,sha,cmd,**kw)()


def test_process_must_still_match_after_chain_scan(tmp_path):
    sha,cmd,_=setup_operator(tmp_path);calls=[]
    def command(pid):
        calls.append(pid)
        return cmd if len(calls)==1 else []
    with pytest.raises(ValueError):supervisor(tmp_path,sha,cmd,command=command)()
    assert calls==[123,123]


def test_bounded_cli_preserves_partial_output_and_kills_timeout():
    with pytest.raises(live.CallUnknown) as exc:
        live.bounded_cli([sys.executable,'-c','import time; print("partial",flush=True); time.sleep(10)'],os.environ,timeout=.2)
    assert exc.value.reason=='TIMEOUT' and exc.value.stdout==b'partial\n'


@pytest.mark.parametrize('stream',['stdout','stderr'])
def test_bounded_cli_limits_output(stream):
    with pytest.raises(live.CallUnknown) as exc:
        live.bounded_cli([sys.executable,'-c',f'import sys; sys.{stream}.write("x"*10000)'],os.environ,limit=100)
    assert exc.value.reason=='OUTPUT_LIMIT'
    assert len(getattr(exc.value,stream))==100


def test_bounded_cli_normal_response():
    r=live.bounded_cli([sys.executable,'-c','print("ok")'],os.environ)
    assert r.returncode==0 and r.stdout==b'ok\n'


def test_recorded_api_requests_persist_before_call_and_timeouts_never_retry(tmp_path):
    tmp_path.chmod(0o700);calls=[]
    def runner(argv,env):
        calls.append(argv)
        assert list(tmp_path.glob('*-request.json'))
        assert env['AWS_MAX_ATTEMPTS']=='1'
        assert argv[argv.index('--endpoint-url')+1]=='https://sts.eu-north-1.amazonaws.com'
        raise live.CallUnknown('TIMEOUT',b'partial',b'')
    api=live.RecordedAPI(tmp_path,runner)
    with pytest.raises(live.CallUnknown):api.call('sts','get-caller-identity',{})
    assert len(calls)==1 and list(tmp_path.glob('*-unknown.json'))
    assert next(tmp_path.glob('*-stdout.json')).read_bytes()==b'partial'


def test_no_request_on_failed_durable_log_and_no_unlisted_operation(tmp_path,monkeypatch):
    tmp_path.chmod(0o700);calls=[]
    api=live.RecordedAPI(tmp_path,lambda *a:calls.append(a))
    with pytest.raises(ValueError):api.call('ec2','start-instances',{})
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr(live,'save',fail)
    with pytest.raises(OSError):api.call('sts','get-caller-identity',{})
    assert not calls


def test_live_entry_uses_real_guard_and_adapter_with_synthetic_cli_responses(tmp_path,monkeypatch):
    sha,cmd,_=setup_operator(tmp_path);fake=helper.FakeAPI();calls=[]
    def runner(argv,env):
        assert env['AWS_MAX_ATTEMPTS']=='1' and argv[:1]==['/opt/homebrew/bin/aws']
        operation=argv[2];calls.append(operation)
        payload=json.loads(argv[argv.index('--cli-input-json')+1])
        response=fake.call(argv[1],operation,payload)
        return subprocess.CompletedProcess(argv,0,json.dumps(response).encode(),b'')
    recorded=live.RecordedAPI(tmp_path,runner)
    checker=supervisor(tmp_path,sha,cmd)
    monkeypatch.setattr(live,'RecordedAPI',lambda root:recorded)
    monkeypatch.setattr(live,'Supervisor',lambda *a:checker)
    real=live.control.execute_once
    monkeypatch.setattr(live.control,'execute_once',lambda *a,**k:real(*a,**k,now_ms=lambda:NOW))
    args=['--evidence-root',str(tmp_path),'--contract-sha256',sha,'--operator-pid','123',
          '--change-set-id',CID,'--stop-expression',STOP,'--execute-approved']
    assert live.main(args)==0
    assert calls[-2:]==['get-stack-policy','execute-change-set']
    assert len(fake.executions())==1
    response=json.loads((tmp_path/'medium-v2-execute-response.json').read_bytes())
    assert response['resize_complete'] is False
    with pytest.raises(ValueError):live.main(args)
    assert len(fake.executions())==1


def test_api_execute_requires_matching_preexisting_guard_intent(tmp_path):
    tmp_path.chmod(0o700);calls=[]
    api=live.RecordedAPI(tmp_path,lambda *a:calls.append(a))
    with pytest.raises(FileNotFoundError):api.call('cloudformation','execute-change-set',{})
    live.save(tmp_path,'medium-v2-execute-intent.json',{'request':{'different':True}})
    with pytest.raises(ValueError):api.call('cloudformation','execute-change-set',{})
    assert not calls
