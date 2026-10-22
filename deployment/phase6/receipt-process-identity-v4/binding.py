"""V4 runtime binding for an EXISTING, separately approved root.

Does not create roots, generate approvals, start processes or call AWS.
The next attempt's procedural package must publish process-launch.json before
exec and pin this package, interpreter-pin.json and its source manifest.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
spec = importlib.util.spec_from_file_location('mie_process_identity_v4', HERE/'identity.py')
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)
require = identity.require
VERSION = identity.VERSION
REQUIRED = ('identity.py', 'binding.py', 'watch-operator.py', 'execute-live.py',
            'process-control.py', 'probe.py', 'local-preflight.py', 'macos-python-pin.json')


def root_check(path):
    p = Path(path)
    require(p.is_absolute() and p.resolve(strict=True) == p)
    s = p.lstat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid == os.getuid() and stat.S_IMODE(s.st_mode) == 0o700)
    return p


def raw_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and s.st_size <= 1024*1024)
        raw = os.read(fd, 1024*1024+1)
        require(len(raw) == s.st_size and not (s.st_mode & 0o022))
        return raw
    finally:
        os.close(fd)


def read(path):
    def pairs(values):
        out = {}
        for key, value in values:
            require(key not in out)
            out[key] = value
        return out
    return json.loads(raw_file(path), object_pairs_hook=pairs)


def save(root, name, data):
    root_check(root)
    require(Path(name).name == name)
    raw = (json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()
    fd = os.open(root/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def source_pins(pins):
    require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip() == pins['commit'])
    require(not subprocess.check_output(['git', 'status', '--porcelain'], cwd=REPO).strip())
    for path, sha in pins['files'].items():
        require(type(path) is str and not path.startswith('/') and '..' not in Path(path).parts)
        require(identity.file_hash(REPO/path) == sha)


def expected(root, contract_sha, pid=None, *, protective=False):
    """Bind caller PID to a previously persisted launch; never discover by name."""
    root = root_check(root)
    require(identity.hexhash(contract_sha))
    require(hashlib.sha256(raw_file(root/'launch-contract.json')).hexdigest() == contract_sha)
    auth = read(root/'authorization.json')
    require(auth['approved'] is True and auth['owner'] == 'Anteneh Hailu'
            and auth['evidence_root'] == str(root) and auth['control_version'] == VERSION)
    for name, field in [('approved-source-pins.json', 'source_pins_sha256'),
                        ('EXECUTION-REVIEW.md', 'review_sha256'),
                        ('interpreter-pin.json', 'interpreter_pin_sha256')]:
        require(hashlib.sha256(raw_file(root/name)).hexdigest() == auth[field])
    pins = read(root/'approved-source-pins.json')
    require(pins['control_version'] == VERSION and pins['evidence_root'] == str(root))
    prefix = HERE.relative_to(REPO).as_posix()+'/'
    require(all(prefix+name in pins['files'] for name in REQUIRED))
    if not protective:
        source_pins(pins)
    pin = read(root/'interpreter-pin.json')
    identity.validate_pin(pin)
    require(hashlib.sha256(raw_file(root/'interpreter-pin.json')).hexdigest()
            == pins['files'][prefix+'macos-python-pin.json'])
    launch = read(root/'process-launch.json')
    require(set(launch) == {'initial_pid', 'uid', 'argv', 'contract_sha256', 'control_version'})
    require(launch['control_version'] == VERSION and launch['contract_sha256'] == contract_sha)
    require(launch['uid'] == os.getuid())
    require(pid is None or launch['initial_pid'] == pid)
    argv = [pin['launcher'], '-u', str(HERE/'watch-operator.py'), '--contract',
        str(root/'launch-contract.json'), '--sha256', contract_sha, '--evidence-root',
        str(root/'operator'), '--execute-approved']
    require(launch['argv'] == argv)
    return identity.expectation(pid=launch['initial_pid'], uid=launch['uid'], argv=argv,
        script_sha256=pins['files'][prefix+'watch-operator.py'], root=root,
        contract_sha256=contract_sha, pin=pin)


def verified(root, sha, pid=None, *, protective=False, inspect=identity.snapshot):
    want = expected(root, sha, pid, protective=protective)
    bound = read(Path(root)/'process-identity.json')
    return identity.verify(bound, want, inspect=inspect, protective=protective)
