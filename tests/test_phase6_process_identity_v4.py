"""Local native macOS checks and synthetic integration; AWS execution forbidden."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import signal
import struct
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[1]
BASE = REPO/'deployment/phase6/receipt-process-identity-v4'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


identity = load('identity_v4_tests', BASE/'identity.py')
binding = load('binding_v4_tests', BASE/'binding.py')
live = load('live_v4_tests', BASE/'execute-live.py')
watch = load('watch_v4_tests', BASE/'watch-operator.py')
control = load('control_v4_tests', BASE/'process-control.py')
local = load('local_v4_tests', BASE/'local-preflight.py')
prior = load('prior_v3_helpers_v4', REPO/'tests/test_phase6_update_states_v3.py')
PIN = json.loads((BASE/'macos-python-pin.json').read_bytes())


@pytest.fixture(autouse=True)
def forbid_aws_process(monkeypatch):
    original = subprocess.Popen
    def checked(argv, *args, **kw):
        assert not (isinstance(argv, (list, tuple)) and Path(str(argv[0])).name == 'aws'), 'AWS forbidden in tests'
        return original(argv, *args, **kw)
    monkeypatch.setattr(subprocess, 'Popen', checked)


def setup(root, *, pid=123, script=None):
    script = script or BASE/'watch-operator.py'
    argv = [PIN['launcher'], '-u', str(script), '--contract', str(root/'launch-contract.json'),
            '--sha256', 'a'*64, '--evidence-root', str(root/'operator'), '--execute-approved']
    want = identity.expectation(pid=pid, uid=os.getuid(), argv=argv,
        script_sha256=identity.file_hash(script), root=root, contract_sha256='a'*64, pin=PIN)
    observed = dict(pid=pid, uid=os.getuid(), ruid=os.getuid(), svuid=os.getuid(),
        start_sec=100, start_usec=123456, boot_uuid='00000000-0000-0000-0000-000000000001',
        kernel_executable=PIN['kernel_executable'], executable_sha256=PIN['kernel_sha256'],
        argv=[PIN['kernel_executable'], *argv[1:]])
    bound = identity.bind(want, inspect=lambda _: copy.deepcopy(observed))
    return want, observed, bound


@pytest.mark.parametrize('args', [
    ['/bin/python', '-u', '/tmp/script with spaces.py', '', '--contract', 'literal $()'],
    ['/bin/python'],
])
def test_procargs_exact_arguments_and_no_environment(args):
    raw = struct.pack('=i', len(args))+b'/bin/python\0\0\0'+b'\0'.join(x.encode() for x in args)+b'\0SECRET=do-not-return\0'
    assert identity.parse_procargs(raw) == args
    assert 'SECRET' not in json.dumps(identity.parse_procargs(raw))


@pytest.mark.parametrize('raw', [b'', b'1234', struct.pack('=i', 0)+b'/x\0x\0',
    struct.pack('=i', 129)+b'/x\0x\0', struct.pack('=i', -1)+b'/x\0x\0',
    struct.pack('=i', 1)+b'/x\0unterminated', struct.pack('=i', 2)+b'/x\0x\0',
    struct.pack('=i', 1)+b'/x\0\xff\0', struct.pack('=i', 1)+b'relative\0x\0'])
def test_procargs_refuses_malformed_or_truncated(raw):
    with pytest.raises((ValueError, UnicodeError)):
        identity.parse_procargs(raw)


def test_exact_mapping_accepts_framework_python_not_original_argv0(tmp_path):
    want, observed, bound = setup(tmp_path)
    assert observed['argv'][0] != want['argv'][0]
    assert identity.verify(bound, want, inspect=lambda _: observed) == observed


@pytest.mark.parametrize('field,value', [
    ('pid', 124), ('pid', True), ('uid', 0), ('ruid', 0), ('svuid', 0),
    ('start_sec', 101), ('start_usec', 123457),
    ('boot_uuid', '00000000-0000-0000-0000-000000000002'),
    ('kernel_executable', '/tmp/Python'), ('executable_sha256', 'f'*64),
    ('argv', ['/tmp/Python', '-u', 'wrong']),
])
def test_wrong_or_reused_process_refused(tmp_path, field, value):
    want, observed, bound = setup(tmp_path)
    observed[field] = value
    with pytest.raises(ValueError):
        identity.verify(bound, want, inspect=lambda _: observed)


@pytest.mark.parametrize('index,value', [(0, PIN['launcher']), (1, '-O'), (2, '/tmp/wrong.py'),
    (4, '/tmp/wrong-contract.json'), (6, 'b'*64), (8, '/tmp/wrong-root')])
def test_exact_argv_tail_and_explicit_kernel_argv0_required(tmp_path, index, value):
    want, observed, bound = setup(tmp_path)
    observed['argv'][index] = value
    with pytest.raises(ValueError):
        identity.verify(bound, want, inspect=lambda _: observed)


@pytest.mark.parametrize('fault', ['root', 'contract', 'schema', 'extra', 'birth_schema', 'permission', 'gone'])
def test_binding_refuses_changed_expectation_schema_or_unavailable_process(tmp_path, fault):
    want, observed, bound = setup(tmp_path)
    if fault == 'root': want['root'] = '/tmp/unapproved'
    if fault == 'contract': want['contract_sha256'] = 'b'*64
    if fault == 'schema': bound['schema_version'] = 'p6.process_identity.v3'
    if fault == 'extra': bound['unreviewed'] = True
    if fault == 'birth_schema': bound['birth']['extra'] = True
    def probe(_):
        if fault == 'permission': raise PermissionError('not allowed')
        if fault == 'gone': raise ProcessLookupError('gone')
        return observed
    with pytest.raises((ValueError, OSError)):
        identity.verify(bound, want, inspect=probe)


def test_script_change_blocks_forward_but_not_bound_protective_verification(tmp_path):
    script = tmp_path/'observer.py'; script.write_text('print("original")\n')
    want, observed, bound = setup(tmp_path, script=script)
    script.write_text('print("changed after process launch")\n')
    with pytest.raises(ValueError): identity.verify(bound, want, inspect=lambda _: observed)
    assert identity.verify(bound, want, inspect=lambda _: observed, protective=True) == observed


@pytest.mark.parametrize('fault', ['birth', 'exec', 'argv', 'permission'])
def test_snapshot_brackets_native_reads_and_does_not_hide_permissions(tmp_path, fault):
    want, observed, _ = setup(tmp_path)
    birth = {k:v for k,v in observed.items() if k not in ('argv', 'kernel_executable', 'executable_sha256')}
    class Backend:
        births=0; executables=0; arguments=0
        def birth(self, pid):
            self.births += 1
            if fault == 'permission': raise PermissionError('blocked')
            return dict(birth, start_usec=birth['start_usec']+(fault == 'birth' and self.births > 1))
        def executable(self, pid):
            self.executables += 1
            return '/tmp/wrong' if fault == 'exec' and self.executables > 1 else PIN['kernel_executable']
        def argv(self, pid):
            self.arguments += 1
            return ['wrong'] if fault == 'argv' and self.arguments > 1 else observed['argv']
    with pytest.raises((ValueError, PermissionError)):
        identity.snapshot(123, backend=Backend())


def test_launcher_and_kernel_pins_are_independent_and_enforced():
    assert PIN['launcher_realpath'] != PIN['kernel_executable']
    identity.validate_pin(PIN)
    for field in ('launcher_sha256', 'kernel_sha256'):
        bad = dict(PIN, **{field:'f'*64})
        with pytest.raises(ValueError): identity.validate_pin(bad)


@pytest.mark.skipif(platform.system() != 'Darwin', reason='Native macOS integration')
def test_native_direct_and_caffeinate_preflight():
    result = local.run(PIN)
    assert result['status'] == 'LOCAL_PROCESS_PREFLIGHT_PASS' and result['aws_calls'] == 0
    assert [x['mode'] for x in result['results']] == ['direct', 'caffeinate']
    assert all(x['initial_pid'] == x['reported_pid'] and x['returncode'] == 0 for x in result['results'])


@pytest.mark.skipif(platform.system() != 'Darwin', reason='Native macOS integration')
@pytest.mark.parametrize('mode', ['direct', 'caffeinate'])
def test_native_lookup_and_protective_signal_only_our_harmless_child(tmp_path, monkeypatch, mode):
    argv = [PIN['launcher'], '-u', str(BASE/'probe.py')]
    launched = argv if mode == 'direct' else ['/usr/bin/caffeinate', '-i', '-t', '2880', *argv]
    proc = subprocess.Popen(launched, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert local.ready_line(proc)['pid'] == proc.pid
        want = identity.expectation(pid=proc.pid, uid=os.getuid(), argv=argv,
            script_sha256=identity.file_hash(BASE/'probe.py'), root=tmp_path,
            contract_sha256='a'*64, pin=PIN)
        bound = identity.bind(want)
        tmp_path.chmod(0o700); binding.save(tmp_path, 'process-identity.json', bound)
        monkeypatch.setattr(control.binding, 'expected', lambda *a, **k: want)
        assert control.lookup(tmp_path, 'a'*64) == proc.pid
        assert control.children_idle(proc.pid, argv)
        result = control.interrupt(tmp_path, 'a'*64, observation=lambda _: dict(
            observation_hash='b'*64, observation_sequence=1, age_seconds=.5))
        assert result['stopped_confirmed'] is False
        proc.communicate(timeout=5); assert proc.returncode == 0
        with pytest.raises(ValueError): control.lookup(tmp_path, 'a'*64)
    finally:
        if proc.poll() is None: proc.terminate(); proc.communicate(timeout=5)


@pytest.mark.skipif(platform.system() != 'Darwin', reason='Native macOS integration')
def test_native_idle_refuses_unexpected_caffeinate_child_arguments():
    argv=[PIN['launcher'], '-u', str(BASE/'probe.py')]
    proc=subprocess.Popen(['/usr/bin/caffeinate','-i','-t','15',*argv],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        assert local.ready_line(proc)['pid']==proc.pid
        assert not control.children_idle(proc.pid, argv)
    finally:
        proc.communicate(b'finish\n',timeout=5)
        assert proc.returncode==0


def wire_root(root, monkeypatch, *, states=('UPDATE_COMPLETE',)):
    _, events, _ = prior.setup_history(root, list(states))
    sha = hashlib.sha256((root/'launch-contract.json').read_bytes()).hexdigest()
    want, observed, bound = setup(root)
    want['contract_sha256'] = sha; want['argv'][6] = sha
    observed['argv'][6] = sha
    bound = identity.bind(want, inspect=lambda _: observed)
    binding.save(root, 'process-identity.json', bound)
    monkeypatch.setattr(live.binding, 'expected', lambda *a, **kw: want)
    checker = live.Supervisor(root, sha, 123, utc_ms=lambda:prior.old.NOW,
        continuous_ns=lambda:prior.old.ELAPSED, inspect=lambda _:copy.deepcopy(observed))
    return sha, want, observed, bound, checker, events


def test_supervisor_uses_same_birth_identity_before_and_after_chain(tmp_path, monkeypatch):
    sha, want, observed, _, check, _ = wire_root(tmp_path, monkeypatch)
    assert check()['process_control_version'] == identity.VERSION
    calls=[]
    def reused(pid):
        calls.append(pid)
        return dict(observed, start_usec=observed['start_usec']+(len(calls)>1))
    check.inspect=reused
    with pytest.raises(ValueError): check()
    assert calls == [123, 123]


@pytest.mark.parametrize('fault', ['source_running', 'clock_stale', 'clock_drift', 'chain', 'cleanup', 'wrong_pid'])
def test_supervisor_preserves_source_clock_chain_and_latest_state_guards(tmp_path, monkeypatch, fault):
    _, _, observed, _, check, events = wire_root(tmp_path, monkeypatch)
    if fault == 'source_running': events[-1]['data']['instances'][live.operator.SOURCE] = 'running'
    if fault == 'clock_stale': check.utc_ms = lambda:prior.old.NOW+26000
    if fault == 'clock_drift': events[-1]['data']['continuous_ns'] -= 3000000000
    if fault == 'cleanup': events[-1]['data']['stack_status'] = 'UPDATE_COMPLETE_CLEANUP_IN_PROGRESS'
    if fault == 'wrong_pid': observed['pid'] = 124
    prior.old.write_events(tmp_path, events)
    if fault == 'chain': next((tmp_path/'operator').glob('*.blob')).write_text('{}')
    with pytest.raises(ValueError): check()


@pytest.mark.parametrize('fault', ['birth_change', 'permissions', 'existing_request', 'busy', 'final_change'])
def test_interrupt_refuses_without_signalling_on_identity_or_idle_failure(tmp_path, monkeypatch, fault):
    tmp_path.chmod(0o700)
    want, observed, bound = setup(tmp_path)
    binding.save(tmp_path, 'process-identity.json', bound)
    monkeypatch.setattr(control.binding, 'expected', lambda *a, **k:want)
    if fault == 'existing_request': (tmp_path/'protective-interrupt-request-v4.json').write_text('{}')
    calls=[]; signals=[]; ticks=iter([0,0,26]); reads=[]
    def inspect(pid):
        reads.append(pid)
        if fault == 'permissions': raise PermissionError('blocked')
        return dict(observed, start_usec=observed['start_usec']+(fault == 'birth_change' or
            (fault == 'final_change' and len(reads)>1)))
    with pytest.raises((ValueError, PermissionError)):
        control.interrupt(tmp_path, 'a'*64, inspect=inspect, idle=lambda *a:fault!='busy',
            observation=lambda _:dict(observation_hash='b'*64, observation_sequence=1, age_seconds=.5),
            send_signal=lambda *a:signals.append(a), monotonic=lambda:next(ticks), sleep=lambda _:None)
    assert not signals


@pytest.mark.parametrize('fault', ['publication', 'identity', 'root'])
def test_watch_refuses_before_any_aws_or_writer_creation(tmp_path, monkeypatch, fault):
    tmp_path.chmod(0o700); (tmp_path/'operator').mkdir(mode=0o700)
    want, observed, bound = setup(tmp_path, pid=os.getpid())
    monkeypatch.setattr(watch.binding, 'expected', lambda *a, **k:want)
    monkeypatch.setattr(watch.binding.identity, 'bind', lambda *a:bound)
    calls=[]
    def prohibited(*a, **k): calls.append('AWS_OR_WRITER'); raise AssertionError('must refuse earlier')
    monkeypatch.setattr(watch.operator, 'AwsCli', prohibited)
    monkeypatch.setattr(watch.operator, 'ObjectWriter', prohibited)
    if fault == 'publication':
        monkeypatch.setattr(watch.binding, 'save', lambda *a:(_ for _ in ()).throw(OSError('disk full')))
    if fault == 'identity':
        monkeypatch.setattr(watch.binding.identity, 'verify', lambda *a:(_ for _ in ()).throw(ValueError('identity changed')))
    if fault == 'root': wantroot = tmp_path/'unapproved'
    else: wantroot = tmp_path
    args=['--contract',str(wantroot/'launch-contract.json'),'--sha256','a'*64,
          '--evidence-root',str(tmp_path/'operator'),'--execute-approved']
    assert watch.main(args) == 2 and not calls


def test_v4_live_entry_preserves_exact_once_execution_guard(tmp_path, monkeypatch):
    sha, _, _, _, checker, _ = wire_root(tmp_path, monkeypatch)
    fake=prior.helper.FakeAPI(); calls=[]
    def runner(argv, env):
        calls.append(argv[2]); payload=json.loads(argv[argv.index('--cli-input-json')+1])
        return subprocess.CompletedProcess(argv,0,json.dumps(fake.call(argv[1],argv[2],payload)).encode(),b'')
    api=live.RecordedAPI(tmp_path,runner)
    monkeypatch.setattr(live,'RecordedAPI',lambda root:api)
    monkeypatch.setattr(live,'Supervisor',lambda *args:checker)
    original=live.control.execute_once
    monkeypatch.setattr(live.control,'execute_once',lambda *a,**k:original(*a,**k,now_ms=lambda:prior.old.NOW))
    args=['--evidence-root',str(tmp_path),'--contract-sha256',sha,'--operator-pid','123',
          '--change-set-id',prior.helper.CID,'--stop-expression',prior.helper.STOP,'--execute-approved']
    assert live.main(args)==0 and len(fake.executions())==1
    assert json.loads((tmp_path/'medium-v4-execute-response.json').read_bytes())['resize_complete'] is False
    with pytest.raises(ValueError): live.main(args)
    assert len(fake.executions())==1


def test_v4_entry_refuses_old_authorization_before_process_read(tmp_path):
    tmp_path.chmod(0o700)
    contract=b'{}'; (tmp_path/'launch-contract.json').write_bytes(contract)
    (tmp_path/'authorization.json').write_text(json.dumps(dict(approved=True,owner='Anteneh Hailu',
        evidence_root=str(tmp_path),control_version='receipt-update-states.v3')))
    with pytest.raises(ValueError): binding.expected(tmp_path, hashlib.sha256(contract).hexdigest())


def runtime_fixture(root, *, pid=123, contract=None):
    root.chmod(0o700)
    if contract is None: contract=b'{}'
    (root/'launch-contract.json').write_bytes(contract)
    sha=hashlib.sha256(contract).hexdigest()
    (root/'EXECUTION-REVIEW.md').write_text('Synthetic local fixture; no AWS authority\n')
    (root/'interpreter-pin.json').write_bytes((BASE/'macos-python-pin.json').read_bytes())
    prefix=BASE.relative_to(REPO).as_posix()+'/'
    pins=dict(control_version=identity.VERSION,evidence_root=str(root),commit='synthetic-test-commit',
        files={prefix+name:identity.file_hash(BASE/name) for name in binding.REQUIRED})
    binding.save(root,'approved-source-pins.json',pins)
    auth=dict(approved=True,owner='Anteneh Hailu',evidence_root=str(root),control_version=identity.VERSION,
        source_pins_sha256=hashlib.sha256((root/'approved-source-pins.json').read_bytes()).hexdigest(),
        review_sha256=hashlib.sha256((root/'EXECUTION-REVIEW.md').read_bytes()).hexdigest(),
        interpreter_pin_sha256=hashlib.sha256((root/'interpreter-pin.json').read_bytes()).hexdigest())
    binding.save(root,'authorization.json',auth)
    argv=[PIN['launcher'],'-u',str(BASE/'watch-operator.py'),'--contract',str(root/'launch-contract.json'),
        '--sha256',sha,'--evidence-root',str(root/'operator'),'--execute-approved']
    launch=dict(initial_pid=pid,uid=os.getuid(),argv=argv,contract_sha256=sha,control_version=identity.VERSION)
    binding.save(root,'process-launch.json',launch)
    return sha,auth,pins,launch


def test_runtime_binding_validates_authority_pins_pid_contract_and_argv(tmp_path,monkeypatch):
    sha,auth,pins,launch=runtime_fixture(tmp_path)
    checked=[]
    monkeypatch.setattr(binding,'source_pins',lambda x:checked.append(x))
    want=binding.expected(tmp_path,sha,123)
    assert checked==[pins] and want['argv']==launch['argv'] and want['pid']==123
    assert want['script_sha256']==identity.file_hash(BASE/'watch-operator.py')
    assert binding.expected(tmp_path,sha,123,protective=True)==want
    assert checked==[pins] # Only forward verification calls preparation source pins.


@pytest.mark.parametrize('fault',['approval','owner','version','root','review_hash','pin_hash',
    'source_hash','missing_source','pid','uid','argv','contract','symlink','duplicate_json','dirty_source'])
def test_runtime_binding_refuses_conflicts_before_native_lookup(tmp_path,monkeypatch,fault):
    sha,auth,pins,launch=runtime_fixture(tmp_path)
    monkeypatch.setattr(binding,'source_pins',lambda _:None)
    def put(name,value): (tmp_path/name).write_text(json.dumps(value))
    if fault=='approval': auth['approved']=False
    if fault=='owner': auth['owner']='wrong'
    if fault=='version': auth['control_version']='receipt-update-states.v3'
    if fault=='root': auth['evidence_root']='/tmp/wrong'
    if fault=='review_hash': auth['review_sha256']='f'*64
    if fault=='pin_hash': auth['interpreter_pin_sha256']='f'*64
    if fault=='source_hash': auth['source_pins_sha256']='f'*64
    if fault=='missing_source':
        pins['files'].pop(next(iter(pins['files'])))
        put('approved-source-pins.json',pins)
        auth['source_pins_sha256']=hashlib.sha256((tmp_path/'approved-source-pins.json').read_bytes()).hexdigest()
    if fault=='pid': launch['initial_pid']=124
    if fault=='uid': launch['uid']=0
    if fault=='argv': launch['argv'].append('--extra')
    if fault=='contract': launch['contract_sha256']='f'*64
    put('authorization.json',auth);put('process-launch.json',launch)
    if fault=='symlink':
        original=tmp_path/'EXECUTION-REVIEW.md';target=tmp_path/'review-target';original.rename(target);original.symlink_to(target)
    if fault=='duplicate_json': (tmp_path/'process-launch.json').write_text('{"initial_pid":123,"initial_pid":123}')
    if fault=='dirty_source': monkeypatch.setattr(binding,'source_pins',lambda _:(_ for _ in ()).throw(ValueError('dirty')))
    with pytest.raises((ValueError,OSError)): binding.expected(tmp_path,sha,123)


@pytest.mark.parametrize('fault',['dirty','commit','content','traversal'])
def test_actual_source_pin_checker_refuses_changed_code_or_git_state(monkeypatch,fault):
    relative='deployment/phase6/receipt-process-identity-v4/identity.py'
    pins=dict(commit='expected',files={relative:identity.file_hash(BASE/'identity.py')})
    def git(argv,**kw):
        if argv[1]=='status': return b' M changed' if fault=='dirty' else b''
        return 'wrong' if fault=='commit' else 'expected'
    monkeypatch.setattr(binding.subprocess,'check_output',git)
    if fault=='content':pins['files'][relative]='f'*64
    if fault=='traversal':pins['files']['../outside']='f'*64
    with pytest.raises(ValueError):binding.source_pins(pins)


def test_watch_entry_publishes_verified_identity_before_aws_and_writer(tmp_path,monkeypatch):
    _,_,_,_,_=prior.watch_setup(tmp_path)
    # Remove only synthetic test store created by helper; not a runtime artifact.
    for f in (tmp_path/'operator').iterdir():f.unlink()
    contract=(tmp_path/'launch-contract.json').read_bytes()
    sha,_,_,_=runtime_fixture(tmp_path,pid=os.getpid(),contract=contract)
    monkeypatch.setattr(watch.binding,'source_pins',lambda _:None)
    want=watch.binding.expected(tmp_path,sha,os.getpid())
    _,observed,_=setup(tmp_path,pid=os.getpid())
    observed['argv']=[PIN['kernel_executable'],*want['argv'][1:]]
    real_bind=watch.binding.identity.bind;real_verify=watch.binding.identity.verify
    monkeypatch.setattr(watch.binding.identity,'bind',lambda expected:real_bind(expected,inspect=lambda _:observed))
    monkeypatch.setattr(watch.binding.identity,'verify',lambda bound,expected:real_verify(bound,expected,inspect=lambda _:observed))
    monkeypatch.setattr(watch.time,'time_ns',lambda:prior.old.NOW*1000000)
    ordering=[]
    class Writer:
        def close(self):ordering.append('close')
    def writer(*a,**kw):
        assert json.loads((tmp_path/'process-identity.json').read_bytes())['birth']['pid']==os.getpid()
        ordering.append('writer');return Writer()
    def api():
        assert (tmp_path/'process-identity.json').exists()
        ordering.append('api');return object()
    def supervise(c,**kw):ordering.append('supervise');return dict(status='STOP_CONFIRMED',runtime_accepted=False)
    monkeypatch.setattr(watch.operator,'ObjectWriter',writer)
    monkeypatch.setattr(watch.operator,'AwsCli',api)
    monkeypatch.setattr(watch.operator,'supervise',supervise)
    args=['--contract',str(tmp_path/'launch-contract.json'),'--sha256',sha,
          '--evidence-root',str(tmp_path/'operator'),'--execute-approved']
    assert watch.main(args)==0 and ordering==['writer','api','supervise','close']
