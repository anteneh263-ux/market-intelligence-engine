"""Run harmless direct/caffeinate process checks BEFORE any AWS window.

Prints local diagnostic JSON only. Does not write runtime evidence or authorize
an attempt. Missing permission, PID transition, mismatch or timeout refuses.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import subprocess
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('local_identity_v4', HERE/'identity.py')
identity = importlib.util.module_from_spec(spec); spec.loader.exec_module(identity)


def ready_line(proc):
    data = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        deadline = time.monotonic()+5
        while b'\n' not in data:
            identity.require(time.monotonic() < deadline and proc.poll() is None)
            for _, _mask in selector.select(.1):
                block = os.read(proc.stdout.fileno(), 1024)
                identity.require(bool(block))
                data.extend(block)
                identity.require(len(data) <= 1024)
    return json.loads(data)


def run(pin):
    identity.validate_pin(pin)
    results = []
    for mode in ('direct', 'caffeinate'):
        argv = [pin['launcher'], '-u', str(HERE/'probe.py')]
        launched = argv if mode == 'direct' else ['/usr/bin/caffeinate', '-i', '-t', '2880', *argv]
        proc = subprocess.Popen(launched, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        try:
            ready = ready_line(proc)
            identity.require(ready['status'] == 'LOCAL_PROBE_READY' and ready['pid'] == proc.pid
                             and ready['executable'] == pin['launcher'])
            want = identity.expectation(pid=proc.pid, uid=os.getuid(), argv=argv,
                script_sha256=identity.file_hash(HERE/'probe.py'), root=str(HERE),
                contract_sha256='0'*64, pin=pin)
            bound = identity.bind(want)
            identity.verify(bound, want)
            out, err = proc.communicate(b'finish\n', timeout=5)
            identity.require(proc.returncode == 0 and not out and not err)
            results.append(dict(mode=mode, initial_pid=proc.pid, reported_pid=ready['pid'],
                identity=bound, expectation=want, returncode=proc.returncode))
        finally:
            if proc.poll() is None:
                proc.terminate(); proc.communicate(timeout=5)
    return dict(status='LOCAL_PROCESS_PREFLIGHT_PASS', control_version=identity.VERSION,
                aws_calls=0, window_started=False, runtime_authorized=False, results=results)


if __name__ == '__main__':
    print(json.dumps(run(json.loads((HERE/'macos-python-pin.json').read_bytes())), sort_keys=True))
