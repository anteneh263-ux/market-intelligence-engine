"""V4 lookup, health and protective SIGINT using one process identity verifier.

No discovery by command substring, no AWS calls and no new roots. A signal is
best-effort, not atomic with identity reads and never proof of stopped instances.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import time

HERE = Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE/file)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


binding = load('control_binding_v4', 'binding.py')
live = load('control_supervisor_v4', 'execute-live.py')


def lookup(root, sha, *, inspect=binding.identity.snapshot, protective=False):
    return binding.verified(root, sha, protective=protective, inspect=inspect)['pid']


def children_idle(pid, expected_argv):
    """Read only pid/ppid inventory; inspect only this operator's children."""
    rows = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid='], text=True, timeout=3)
    native = binding.identity.Darwin()
    for line in rows.splitlines():
        child, parent = map(int, line.split())
        if parent != pid:
            continue
        birth = native.birth(child)
        path = native.executable(child)
        argv = native.argv(child)
        binding.require(native.birth(child) == birth)
        if not (birth['uid'] == os.getuid() and path == '/usr/bin/caffeinate' and
                argv == ['/usr/bin/caffeinate', '-i', '-t', '2880', *expected_argv]):
            return False
    return True


def idle_observation(root):
    folder = Path(root)/'operator'
    files = list(folder.glob('*.blob'))
    binding.require(bool(files))
    latest = max(files, key=lambda p: p.stat().st_mtime_ns)
    raw = binding.raw_file(latest)
    binding.require(hashlib.sha256(raw).hexdigest() == latest.stem)
    event = json.loads(raw)
    age = time.time()-latest.stat().st_mtime
    if event['kind'] != 'observation' or not .25 <= age <= 1.8:
        return None
    binding.require(event['schema_version'] == live.progress.EVENT_SCHEMA)
    return dict(observation_hash=latest.stem, observation_sequence=event['sequence'], age_seconds=age)


def interrupt(root, sha, *, inspect=binding.identity.snapshot, idle=children_idle,
              observation=idle_observation, send_signal=os.kill, sleep=time.sleep,
              monotonic=time.monotonic):
    root = binding.root_check(root)
    request = root/'protective-interrupt-request-v4.json'
    binding.require(not request.exists() and not request.is_symlink())
    want = binding.expected(root, sha, protective=True)
    bound = binding.read(root/'process-identity.json')
    pid = want['pid']
    start = monotonic()
    while monotonic()-start < 25:
        binding.identity.verify(bound, want, inspect=inspect, protective=True)
        event = observation(root)
        if event and idle(pid, want['argv']):
            binding.save(root, request.name, dict(pid=pid, signal='SIGINT',
                contract_sha256=sha, identity_sha256=binding.identity.digest(bound),
                process_control_version=binding.VERSION, signal_attempts_at_most=1,
                signal_is_not_stop_confirmation=True, **event))
            # Final check immediately before signal; no process-name fallback.
            binding.identity.verify(bound, want, inspect=inspect, protective=True)
            send_signal(pid, signal.SIGINT)
            return dict(status='PROTECTIVE_SIGINT_REQUESTED', pid=pid, stopped_confirmed=False)
        sleep(.1)
    raise ValueError('NO_IDLE_WINDOW_NO_SIGNAL_SENT')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['lookup', 'health', 'interrupt'])
    p.add_argument('--evidence-root', required=True)
    p.add_argument('--contract-sha256', required=True)
    p.add_argument('--min-seconds', type=int, default=1)
    a = p.parse_args(argv)
    if a.operation == 'interrupt':
        answer = interrupt(a.evidence_root, a.contract_sha256)
    else:
        pid = lookup(a.evidence_root, a.contract_sha256)
        answer = dict(pid=pid) if a.operation == 'lookup' else live.Supervisor(
            a.evidence_root, a.contract_sha256, pid).observe(
                min_seconds=a.min_seconds, settled=True, receipt_transition=True)
    print(json.dumps(answer, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
