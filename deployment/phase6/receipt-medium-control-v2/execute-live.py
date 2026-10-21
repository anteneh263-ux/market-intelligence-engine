"""Explicit live entry point for v2; importing or --help never calls AWS.

The approved root, fresh change set, installed policy and already-running reviewed
watch-only operator must exist. This command neither creates nor starts them.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CONTROL_SHA = '5263f1cb5a9937cc7f5cc0ac2574760b517f82acf3e312ee63b223749b0281b7'
OPERATOR_SHA = 'b2e59df87c60b3053db6ba65eb9620a5e2cd5ba5cc56fae9f94681f598e44e0a'
KIT_SHA = 'c3a024b3e09483f3c2341ec87cb10e6843dbd39dc174fa7b2aac660666bf216e'
PYTHON = '/Users/antenehhailu/Desktop/market-intelligence-engine/.venv/bin/python'
if hashlib.sha256((HERE/'control.py').read_bytes()).hexdigest() != CONTROL_SHA:
    raise ValueError('CONTROL_HASH_REFUSED')
spec = importlib.util.spec_from_file_location('receipt_medium_control_live', HERE/'control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
if hashlib.sha256((REPO/'app/research/phase6_qualification_operator.py').read_bytes()).hexdigest() != OPERATOR_SHA:
    raise ValueError('OPERATOR_HASH_REFUSED')
sys.path.insert(0, str(REPO))
from app.research import phase6_qualification_operator as operator


def root_check(path):
    p = Path(path)
    control.require(p.is_absolute() and p.resolve(strict=True) == p)
    s = p.lstat()
    control.require(stat.S_ISDIR(s.st_mode) and s.st_uid == os.getuid() and stat.S_IMODE(s.st_mode) == 0o700)
    return p


def read_regular(path, limit=1024*1024):
    s = path.lstat()
    control.require(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and s.st_size <= limit)
    raw = path.read_bytes()
    control.require(len(raw) <= limit)
    return raw


def save(root, name, value):
    raw = value if type(value) is bytes else (control.canonical(value)+'\n').encode()
    fd = os.open(root/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class CallUnknown(RuntimeError):
    def __init__(self, reason, stdout=b'', stderr=b''):
        super().__init__(reason)
        self.reason, self.stdout, self.stderr = reason, stdout, stderr


def bounded_cli(argv, env, *, timeout=30, limit=1024*1024):
    """Bound elapsed time and both captured streams; kill process group on overflow."""
    output = [bytearray(), bytearray()]
    p = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        with selectors.DefaultSelector() as selector:
            for n, stream in enumerate((p.stdout, p.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, n)
            deadline = time.monotonic() + timeout
            while selector.get_map():
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise CallUnknown('TIMEOUT', *map(bytes, output))
                for key, _ in selector.select(min(remaining, .1)):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    target = output[key.data]
                    room = limit-len(target)
                    target.extend(block[:room])
                    if len(block) > room:
                        raise CallUnknown('OUTPUT_LIMIT', *map(bytes, output))
            try:
                code = p.wait(timeout=max(.001, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                raise CallUnknown('TIMEOUT', *map(bytes, output))
            return subprocess.CompletedProcess(argv, code, *map(bytes, output))
    except BaseException:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait(timeout=5)
        raise
    finally:
        p.stdout.close()
        p.stderr.close()


class RecordedAPI:
    """Only read operations and the guarded ExecuteChangeSet call; no SDK retries."""
    ALLOWED = {
        ('sts', 'get-caller-identity'), ('cloudformation', 'describe-stacks'),
        ('cloudformation', 'list-stack-resources'), ('cloudformation', 'get-template'),
        ('cloudformation', 'describe-change-set'), ('cloudformation', 'get-stack-policy'),
        ('cloudformation', 'execute-change-set'), ('ec2', 'describe-instances'),
        ('ec2', 'describe-volumes'), ('ec2', 'describe-instance-credit-specifications'),
        ('scheduler', 'get-schedule'), ('iam', 'get-role-policy'),
    }

    def __init__(self, root, runner=bounded_cli):
        self.root, self.runner, self.sequence = root_check(root), runner, 0

    def call(self, service, operation, payload):
        control.require((service, operation) in self.ALLOWED)
        if operation == 'execute-change-set':
            # The exact durable intent must already have passed execute_once.
            intent = control.parse(read_regular(self.root/'medium-v2-execute-intent.json'))
            control.require(control.canonical(intent['request']) == control.canonical(payload))
        self.sequence += 1
        name = f'medium-v2-api-{self.sequence:03d}-{operation}'
        endpoint = 'https://iam.amazonaws.com' if service == 'iam' else f'https://{service}.eu-north-1.amazonaws.com'
        argv = ['/opt/homebrew/bin/aws', service, operation, '--profile', 'mie-phase6',
                '--region', 'eu-north-1', '--endpoint-url', endpoint, '--output', 'json',
                '--no-cli-pager', '--no-cli-auto-prompt', '--cli-connect-timeout', '3',
                '--cli-read-timeout', '15', '--cli-input-json', control.canonical(payload)]
        env = dict(os.environ, AWS_MAX_ATTEMPTS='1', AWS_RETRY_MODE='standard')
        save(self.root, name+'-request.json', dict(service=service, operation=operation,
             payload=payload, endpoint=endpoint, profile='mie-phase6', requested_utc_ms=time.time_ns()//1000000,
             total_attempts=1, timeout_seconds=30, output_limit_each=1024*1024))
        try:
            response = self.runner(argv, env)
        except BaseException as exc:
            if isinstance(exc, CallUnknown):
                save(self.root, name+'-stdout.json', exc.stdout)
                save(self.root, name+'-stderr.txt', exc.stderr)
            save(self.root, name+'-unknown.json', dict(status='OUTCOME_UNKNOWN', retry_allowed=False,
                 exception_type=type(exc).__name__, reason=exc.reason if isinstance(exc, CallUnknown) else 'CALL_EXCEPTION'))
            raise
        save(self.root, name+'-stdout.json', response.stdout)
        save(self.root, name+'-stderr.txt', response.stderr)
        save(self.root, name+'-result.json', dict(returncode=response.returncode, retry_allowed=False))
        control.require(response.returncode == 0 and len(response.stdout) <= 1024*1024 and len(response.stderr) <= 1024*1024)
        value = control.parse(response.stdout) if response.stdout.strip() else {}
        control.require(type(value) is dict)
        return value


def process_command(pid):
    control.require(type(pid) is int and pid > 0)
    r = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='], capture_output=True, timeout=5)
    control.require(r.returncode == 0 and len(r.stdout) <= 8192)
    return shlex.split(r.stdout.decode().strip())


class Supervisor:
    def __init__(self, root, contract_sha, pid, *, utc_ms=lambda: time.time_ns()//1000000,
                 continuous_ns=operator.continuous_ns, command=process_command):
        self.root, self.sha, self.pid = root_check(root), contract_sha, pid
        self.utc_ms, self.continuous_ns, self.command = utc_ms, continuous_ns, command

    def __call__(self):
        raw = read_regular(self.root/'launch-contract.json', 65536)
        control.require(hashlib.sha256(raw).hexdigest() == self.sha)
        now, continuous = self.utc_ms(), self.continuous_ns()
        control.require(type(continuous) is int and continuous >= 0)
        c = operator.contract(control.parse(raw), now)
        control.require(c['stack_id'] == control.STACK and c['operator_arn'] == f'arn:aws:iam::{control.ACCOUNT}:root'
                        and c['template_sha256'] == control.TEMPLATE_SHA and c['runtime_bundle_sha256'] == KIT_SHA
                        and c['stop_utc_ms']-c['not_before_utc_ms'] == 2700000)
        expected_command = [PYTHON, '-u', '-m', 'app.research.phase6_qualification_operator',
            '--contract', str(self.root/'launch-contract.json'), '--sha256', self.sha,
            '--evidence-root', str(self.root/'operator'), '--execute-approved']
        control.require(self.command(self.pid) == expected_command)
        folder = root_check(self.root/'operator')
        header = control.parse(read_regular(folder/'writer.json', 65536))
        control.require(header == dict(schema_version='p6.object_store.v1', owner=control.ACCOUNT,
                        run_id=hashlib.sha256(control.canonical(c).encode()).hexdigest(), max_bytes=16*1024*1024))
        paths = list(folder.glob('*.blob'))
        control.require(2 <= len(paths) <= 801)
        entries = []
        for path in paths:
            raw = read_regular(path, 65536)
            control.require(hashlib.sha256(raw).hexdigest() == path.stem)
            entries.append((control.parse(raw), path.stem))
        entries.sort(key=lambda item: item[0]['sequence'])
        previous, last_wall, last_elapsed = '0'*64, None, None
        pair = {i:'stopped' for i in control.v1.INSTANCES.values()}
        for index, (event, digest) in enumerate(entries):
            control.require(set(event) == {'schema_version','sequence','previous_hash','kind','data'}
                            and event['schema_version'] == 'p6.qualification_operator_event.v1'
                            and type(event['sequence']) is int and event['sequence'] == index+1
                            and event['previous_hash'] == previous)
            d = event['data']
            if index == 0:
                control.require(event['kind'] == 'armed' and d['contract'] == c
                                and d['observed_instances'] == pair and d['qualification_only'] is True)
                armed_wall, armed_elapsed = d['utc_ms'], d['continuous_ns']
                control.require(type(armed_wall) is int and c['not_before_utc_ms'] <= armed_wall <= now)
            else:
                control.require(event['kind'] == 'observation' and d['clock_available'] is True
                                and d['instances'] == pair and d['stack_status'] in ('UPDATE_COMPLETE','UPDATE_IN_PROGRESS'))
            wall, elapsed = d['utc_ms'], d['continuous_ns']
            control.require(type(wall) is int and type(elapsed) is int and elapsed >= 0)
            if last_wall is not None:
                control.require(wall >= last_wall and elapsed > last_elapsed
                    and abs((wall-last_wall)*1000000-(elapsed-last_elapsed)) <= 2000000000)
            previous, last_wall, last_elapsed = digest, wall, elapsed
        control.require(entries[-1][0]['data']['stack_status'] == 'UPDATE_COMPLETE')
        control.require(0 <= now-last_wall <= 25000 and 0 <= continuous-last_elapsed <= 25000000000)
        control.require(abs((now-armed_wall)*1000000-(continuous-armed_elapsed)) <= 2000000000)
        deadline = armed_elapsed+(c['stop_utc_ms']-armed_wall)*1000000
        control.require(2400000000000 <= deadline-continuous <= 2700000000000)
        # Recheck live process after reading the entire chain, before returning evidence.
        control.require(self.command(self.pid) == expected_command)
        return dict(status='ARMED', stop_utc_ms=c['stop_utc_ms'], stack_id=c['stack_id'],
                    stack_status='UPDATE_COMPLETE', instances=pair, observed_utc_ms=last_wall,
                    operator_pid=self.pid, operator_head=previous, continuous_remaining_ns=deadline-continuous)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence-root', required=True)
    p.add_argument('--contract-sha256', required=True)
    p.add_argument('--operator-pid', required=True, type=int)
    p.add_argument('--change-set-id', required=True)
    p.add_argument('--stop-expression', required=True)
    p.add_argument('--execute-approved', action='store_true', required=True)
    a = p.parse_args(argv)
    root = root_check(a.evidence_root)
    supervisor = Supervisor(root, a.contract_sha256, a.operator_pid)
    answer = control.execute_once(RecordedAPI(root), expected_id=a.change_set_id,
        stop_expression=a.stop_expression, supervisor=supervisor, evidence_dir=str(root))
    save(root, 'medium-v2-execute-response.json', answer)
    print(control.canonical(answer))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
