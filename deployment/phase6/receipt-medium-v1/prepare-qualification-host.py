"""Receipt-medium-v1 preparation only; frozen separately from the original small variant. Run on an explicitly approved fresh qualification host, never a pilot.

The Python wheels install offline. RPM installation and locked Cargo builds use the
network. No runtime evidence roots, identities, tunnel or receipt listener are created.
Compatible with the AL2023 system Python 3.9; runtime uses separate Python 3.11.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import pwd
import re
import selectors
import signal
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request

BASE = Path('/opt/mie/qualification-001-host-v1')
BUNDLE_SHA = '370343f69e8ac0f4264b239c3ed43339cebb4cf4c9d45d2720b8833b1c060034'
ACCOUNT = '989879505232'
AMI = 'ami-04e0c7320ad3a3a48'
REGION = 'eu-north-1'
ROLES = {'source': 'mie-p6-qualification-001-SourceRole-u3M2p6TXADEL',
         'receipt': 'mie-p6-qualification-001-ReceiptRole-6BqOJzmlKtid'}
PROFILES = {'source': 'mie-p6-qualification-001-SourceRoleProfile-zUFLg78C0FsN',
            'receipt': 'mie-p6-qualification-001-ReceiptRoleProfile-9F2A3mVypDyp'}
INSTANCE_TYPES = {'source': 't4g.small', 'receipt': 't4g.medium'}
INSTANCES = {'source': 'i-08e89ad2e3feedb8a', 'receipt': 'i-0c5a2a75ca70db228'}
CRATES = {'clock-bound-d': '57d1b180cf45b60779c85fe5768cde1c399f286721103269268492af6e523f88',
          'clock-bound-ffi': '8905228362818863465fd127b646494ea309e35736c8903555f1fa8fc2d24112'}
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024


def require(ok):
    if not ok:
        raise ValueError('HOST_PREPARATION_REFUSED')


def cleanup(primary, action):
    try:
        action()
    except BaseException as secondary:
        if primary is None:
            raise
        if hasattr(primary, 'add_note'):
            primary.add_note('Secondary cleanup failure: ' + type(secondary).__name__)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pairs(items):
    d = {}
    for k, v in items:
        require(k not in d)
        d[k] = v
    return d


def parse(raw):
    return json.loads(raw, object_pairs_hook=pairs)


def read_file(path, limit=MAX_ARCHIVE):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(str(path))
            and path.resolve(strict=True) == path)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_size <= limit)
        with os.fdopen(fd, 'rb', closefd=False) as f:
            raw = f.read(limit + 1)
        require(len(raw) == s.st_size and len(raw) <= limit)
        return raw
    finally:
        os.close(fd)


def unpack(raw, expected_hash):
    """Validate before any filesystem write; never call extract/extractall."""
    require(digest(raw) == expected_hash and len(raw) <= MAX_ARCHIVE)
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        tar = gz.read(MAX_EXPANDED + 1)
    require(len(tar) <= MAX_EXPANDED)
    files = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(tar), mode='r:') as archive:
        for member in archive:
            name = member.name
            require(len(files) < 4096 and len(name) <= 256 and name.isascii()
                    and re.fullmatch(r'[A-Za-z0-9_.+/-]+', name) is not None
                    and not name.startswith('/') and str(PurePosixPath(name)) == name
                    and '..' not in PurePosixPath(name).parts and name not in files
                    and member.isfile() and (not member.pax_headers or member.pax_headers == {'path': name})
                    and 0 <= member.size <= 32 * 1024 * 1024)
            total += member.size
            require(total <= MAX_ARCHIVE)
            files[name] = archive.extractfile(member).read(member.size + 1)
            require(len(files[name]) == member.size)
    require(files and not any('/'.join(PurePosixPath(n).parts[:i]) in files
            for n in files for i in range(1, len(PurePosixPath(n).parts))))
    return files


def bundle_files(raw, expected_hash=BUNDLE_SHA):
    files = unpack(raw, expected_hash)
    manifest = parse(files.pop('manifest.json'))
    require(set(manifest) == {'schema_version', 'purpose', 'target', 'native_clockbound_included',
                             'identities_included', 'files'}
            and manifest['schema_version'] == 'p6.qualification_bundle.v1'
            and manifest['purpose'] == 'SYNTHETIC_RECEIPT_QUALIFICATION_CANDIDATE_ONLY'
            and manifest['target'] == 'CPython 3.11 Linux aarch64 glibc >= 2.28'
            and manifest['native_clockbound_included'] is False
            and manifest['identities_included'] is False and set(manifest['files']) == set(files))
    for name, content in files.items():
        row = manifest['files'][name]
        require(set(row) == {'bytes', 'sha256'} and type(row['bytes']) is int
                and row['bytes'] == len(content) and row['sha256'] == digest(content))
    return files



def installed_versions(raw, lock):
    rows = parse(raw)
    require(type(rows) is list and len(rows) <= 32)
    installed = {}
    for row in rows:
        require(set(row) == {'name', 'version'})
        name = re.sub(r'[-_.]+', '-', row['name']).lower()
        require(name not in installed)
        installed[name] = row['version']
    expected = {row['name']: row['version'] for row in lock['wheels']}
    require(set(installed) - set(expected) <= {'pip', 'setuptools'}
            and all(installed.get(name) == version for name, version in expected.items()))
    return installed


def mkdir_fresh(path):
    """Only a new leaf under an existing non-redirected directory; no repair."""
    path = Path(path)
    require(path.is_absolute() and path.parent.resolve(strict=True) == path.parent)
    parent = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.mkdir(path.name, 0o700, dir_fd=parent)
    finally:
        os.close(parent)


def write_new(path, raw, mode=0o600):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb', closefd=False) as f:
            f.write(raw)
            f.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    require(read_file(path) == raw)


def publish_tree(path, files):
    mkdir_fresh(path)
    # The fresh tree remains owner-only until all validated files have been written.
    for name, raw in sorted(files.items()):
        child = path / name
        child.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_new(child, raw, 0o644)
    for root, dirs, _ in os.walk(path):
        os.chmod(root, 0o755)


def imds_identity(instance_id, role):
    require(role in INSTANCES and instance_id == INSTANCES[role])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path, method='GET', token=None):
        headers = {'X-aws-ec2-metadata-token-ttl-seconds': '60'} if method == 'PUT' else {
            'X-aws-ec2-metadata-token': token}
        req = urllib.request.Request('http://169.254.169.254/latest/' + path,
                                     method=method, headers=headers)
        with opener.open(req, timeout=3) as response:
            raw = response.read(16385)
        require(len(raw) <= 16384)
        return raw
    token = request('api/token', 'PUT').decode('ascii')
    d = parse(request('dynamic/instance-identity/document', token=token))
    p = parse(request('meta-data/iam/info', token=token))
    require(d['instanceId'] == instance_id and d['accountId'] == ACCOUNT
            and d['region'] == REGION and d['imageId'] == AMI
            and d['architecture'] == 'arm64' and d['instanceType'] == INSTANCE_TYPES[role]
            and p['Code'] == 'Success' and p['InstanceProfileArn'] ==
            'arn:aws:iam::' + ACCOUNT + ':instance-profile/' + PROFILES[role])
    return {'document': d, 'profile_arn': p['InstanceProfileArn']}


class Commands:
    """One attempt per stage, bounded logs/deadlines, terminate the entire process group."""
    def __init__(self, root, elapsed=None):
        self.root = root
        self.elapsed = elapsed or (lambda: time.clock_gettime(time.CLOCK_BOOTTIME))
        self.deadline = self.elapsed() + 1800
        self.seq = 0
        self.trace = (root / 'steps.jsonl').open('xb')
        self.env = dict(PATH='/usr/local/bin:/usr/bin:/usr/sbin:/bin:/sbin', LC_ALL='C',
            HOME=str(BASE / 'build-home'), CARGO_HOME=str(BASE / 'cargo-home'),
            CARGO_TARGET_DIR=str(BASE / 'target'), CARGO_NET_RETRY='0', CARGO_HTTP_TIMEOUT='30',
            PYTHONDONTWRITEBYTECODE='1', PIP_CONFIG_FILE='/dev/null', PIP_DISABLE_PIP_VERSION_CHECK='1')

    def record(self, kind, **data):
        raw = json.dumps(dict(kind=kind, **data), sort_keys=True).encode() + b'\n'
        self.trace.write(raw)
        self.trace.flush()
        os.fsync(self.trace.fileno())

    def run(self, args, seconds=30, cwd=None, env=None, log_limit=8 * 1024 * 1024):
        require(self.elapsed() < self.deadline)
        self.seq += 1
        name = '%03d.log' % self.seq
        self.record('requested', sequence=self.seq, argv=args, log=name)
        deadline = min(self.deadline, self.elapsed() + seconds)
        output = bytearray()
        proc = None
        log = None
        try:
            log = (self.root / name).open('xb')
            proc = subprocess.Popen(args, cwd=cwd, env=env or self.env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            with selectors.DefaultSelector() as sel:
                sel.register(proc.stdout, selectors.EVENT_READ)
                while sel.get_map():
                    require(self.elapsed() < deadline)
                    for key, _ in sel.select(min(.1, max(0, deadline - self.elapsed()))):
                        block = os.read(key.fileobj.fileno(), 65536)
                        if not block:
                            sel.unregister(key.fileobj)
                            break
                        require(len(output) + len(block) <= log_limit)
                        output.extend(block)
                        log.write(block)
                proc.wait(timeout=max(.01, deadline - self.elapsed()))
            log.flush()
            os.fsync(log.fileno())
            require(proc.returncode == 0)
            self.record('completed', sequence=self.seq, exit_code=0, bytes=len(output), sha256=digest(output))
            return bytes(output)
        except BaseException as primary:
            if proc:
                # Descendants can still hold stdout after their direct parent exited.
                def kill_group():
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                cleanup(primary, kill_group)
                cleanup(primary, lambda: proc.wait(timeout=5))
            if log:
                cleanup(primary, log.flush)
                cleanup(primary, lambda: os.fsync(log.fileno()))
            cleanup(primary, lambda: self.record('failed', sequence=self.seq, bytes=len(output), retry_allowed=False))
            raise
        finally:
            primary = sys.exc_info()[1]
            if log:
                cleanup(primary, log.close)
            if proc and proc.stdout:
                cleanup(primary, proc.stdout.close)

    def close(self):
        self.trace.close()


def prepare(bundle, crate_dir, readiness, readiness_sha, role, instance_id):
    require(role == 'receipt' and instance_id == INSTANCES['receipt'])
    require(platform.system() == 'Linux' and platform.machine() == 'aarch64' and os.geteuid() == 0)
    os.umask(0o077)
    identity = imds_identity(instance_id, role)  # Before host mutations; never read IMDS credentials.
    require(Path('/dev/vmclock0').exists() and stat.S_ISCHR(os.stat('/dev/vmclock0').st_mode))
    files = bundle_files(read_file(bundle))
    probe_raw = read_file(readiness, 65536)
    require(re.fullmatch(r'[0-9a-f]{64}', readiness_sha) and digest(probe_raw) == readiness_sha)
    natives = {}
    for name, sha in CRATES.items():
        natives.update(unpack(read_file(Path(crate_dir) / (name + '.crate')), sha))
    try:
        pwd.getpwnam('mie-qualification')
    except KeyError:
        pass
    else:
        raise ValueError('RUNTIME_USER_ALREADY_EXISTS')
    require(Path('/opt').resolve(strict=True) == Path('/opt'))
    if not Path('/opt/mie').exists():
        os.mkdir('/opt/mie', 0o755)
    for parent in (Path('/opt'), Path('/opt/mie')):
        st = parent.lstat()
        require(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not (st.st_mode & 0o022))
    mkdir_fresh(BASE)
    publish_tree(BASE / 'package', files)
    publish_tree(BASE / 'native-source', natives)
    mkdir_fresh(BASE / 'operations')
    write_new(BASE / 'readiness.py', probe_raw, 0o644)
    write_new(BASE / 'operations' / 'identity.json', json.dumps(identity, sort_keys=True).encode())
    commands = Commands(BASE / 'operations')
    try:
        # The independent operator/guest/scheduler stop controls must already be armed.
        commands.run(['/usr/bin/systemctl', 'is-active', 'mie-qualification-stop.timer'])
        commands.run(['/usr/bin/timeout', '240', '/usr/bin/dnf', 'install', '-y',
                      'python3.11', 'python3.11-pip', 'gcc', 'cargo', 'rust', 'chrony'], seconds=250)
        commands.run(['/usr/bin/rpm', '-q', 'python3.11', 'python3.11-pip', 'gcc', 'cargo',
                      'rust', 'chrony', 'glibc', 'amazon-ssm-agent'])
        commands.run(['/usr/bin/systemctl', 'is-active', 'amazon-ssm-agent'])
        commands.run(['/usr/bin/python3.11', '-m', 'venv', str(BASE / 'venv')])
        python = str(BASE / 'venv/bin/python3.11')
        commands.run([python, '-m', 'pip', 'install', '--no-index', '--no-cache-dir', '--require-hashes',
            '--only-binary=:all:', '--find-links', str(BASE / 'package/wheels'), '-r',
            str(BASE / 'package/deployment/phase6/qualification-linux-py311.lock')], seconds=120)
        commands.run([python, '-m', 'pip', 'check'])
        versions = installed_versions(commands.run([python, '-m', 'pip', 'list', '--format=json']),
            parse(files['deployment/phase6/qualification-linux-py311-wheels.json']))
        env = dict(commands.env, PYTHONPATH=str(BASE / 'package/code'))
        for module in ('app.research.phase6_qualification_runtime', 'app.research.phase6_qualification_operator'):
            commands.run([python, '-m', module, '--help'], env=env)
        commands.run(['/usr/bin/rustc', '--version', '--verbose'])
        commands.run(['/usr/bin/cargo', '--version'])
        for name in CRATES:
            commands.run(['/usr/bin/cargo', 'build', '--release', '--locked', '-j', '1'],
                         seconds=600, cwd=str(BASE / 'native-source' / (name + '-2.0.3')))
            lockname = name + '-2.0.3/Cargo.lock'
            require(read_file(BASE / 'native-source' / lockname) == natives[lockname])
        abi = b'''#include <stddef.h>\n#include <stdio.h>\n#include "native-source/clock-bound-ffi-2.0.3/include/clockbound.h"\nint main(void) {printf("%zu %zu %zu %zu %zu\\n", sizeof(struct timespec), sizeof(clockbound_now_result), sizeof(clockbound_err), offsetof(clockbound_now_result, latest), offsetof(clockbound_now_result, clock_status));return !(sizeof(struct timespec)==16 && sizeof(clockbound_now_result)==40 && sizeof(clockbound_err)==16 && offsetof(clockbound_now_result,latest)==16 && offsetof(clockbound_now_result,clock_status)==32);}\n'''
        write_new(BASE / 'abi.c', abi)
        commands.run(['/usr/bin/gcc', str(BASE / 'abi.c'), '-o', str(BASE / 'abi-check')])
        commands.run([str(BASE / 'abi-check')])
        mkdir_fresh(BASE / 'native')
        native_hashes = {}
        for name in ('clockbound', 'libclockbound.so'):
            raw = read_file(BASE / 'target/release' / name, 16 * 1024 * 1024)
            write_new(BASE / 'native' / name, raw, 0o755 if name == 'clockbound' else 0o644)
            native_hashes[name] = digest(raw)
            commands.run(['/usr/bin/ldd', str(BASE / 'native' / name)])
        commands.run(['/usr/sbin/useradd', '--system', '--no-create-home', '--home-dir', '/nonexistent',
                      '--shell', '/sbin/nologin', '--gid', 'chrony', 'mie-qualification'])
        commands.run(['/usr/bin/id', 'mie-qualification'])
        # Root owns all installation bytes. Only these trees become runtime-readable.
        for tree in (BASE / 'venv', BASE / 'native'):
            for parent, dirs, names in os.walk(tree):
                os.chmod(parent, 0o755)
                for name in names:
                    p = Path(parent) / name
                    if not p.is_symlink():
                        os.chmod(p, 0o755 if p.stat().st_mode & 0o100 else 0o644)
        os.chmod(BASE, 0o755)
        old_chrony = read_file(Path('/etc/chrony.conf'), 65536)
        write_new(BASE / 'operations/chrony-before.conf', old_chrony)
        # Preserve prior configuration first. Refuse changed/replaced inputs.
        require(read_file(Path('/etc/chrony.conf'), 65536) == old_chrony)
        chrony = b'server 169.254.169.123 prefer iburst minpoll 4 maxpoll 4\nmaxclockerror 50\ndriftfile /var/lib/chrony/drift\nmakestep 1.0 3\nrtcsync\n'
        write_new(BASE / 'chrony.conf', chrony)
        commands.run(['/usr/bin/install', '-m', '0644', '-o', 'root', '-g', 'root',
                      str(BASE / 'chrony.conf'), '/etc/chrony.conf'])
        require(read_file(Path('/etc/chrony.conf')) == chrony)
        vmclock_stat = os.stat('/dev/vmclock0')
        commands.record('vmclock_permissions_before', uid=vmclock_stat.st_uid,
                        gid=vmclock_stat.st_gid, mode=stat.S_IMODE(vmclock_stat.st_mode))
        # No world-readable VMClock change. chrony alone receives group read permission.
        commands.run(['/usr/bin/chgrp', 'chrony', '/dev/vmclock0'])
        commands.run(['/usr/bin/chmod', '0640', '/dev/vmclock0'])
        commands.run(['/usr/bin/systemctl', 'restart', 'chronyd'])
        commands.run(['/usr/bin/chronyc', 'waitsync', '30', '0.01', '0.0', '1'], seconds=40)
        commands.run(['/usr/bin/systemd-run', '--unit=mie-qualification-clockbound', '--uid=chrony',
            '--property=RuntimeDirectory=clockbound', '--property=RuntimeDirectoryMode=0750',
            '--property=RuntimeDirectoryPreserve=yes', '--property=Restart=no',
            str(BASE / 'native/clockbound'), '--max-drift-rate', '50'])
        commands.run(['/usr/bin/systemctl', 'is-active', 'mie-qualification-clockbound'])
        probe = commands.run(['/usr/sbin/runuser', '-u', 'mie-qualification', '--',
            '/usr/bin/env', 'PYTHONPATH=' + str(BASE / 'package/code'), 'PYTHONDONTWRITEBYTECODE=1',
            python, str(BASE / 'readiness.py'), '--library', str(BASE / 'native/libclockbound.so'),
            '--sha256', native_hashes['libclockbound.so']], seconds=140)
        observation = parse(probe.splitlines()[-1])
        require(observation['status'] == 'READINESS_OBSERVED' and observation['samples'] == 10)
        report = dict(schema_version='p6.host_preparation.receipt_medium.v1',
            deployment_variant='receipt-medium-v1', instance_type=INSTANCE_TYPES[role], status='HOST_PREPARED_PENDING_REVIEW',
            instance_id=instance_id, role=role, runtime_bundle_sha256=BUNDLE_SHA,
            readiness_script_sha256=readiness_sha, native_hashes=native_hashes,
            installed_python_versions=versions, native_source_sha256=CRATES,
            readiness=observation, runtime_accepted=False, prospective_evidence=False,
            identity_values_provisioned=False, receipt_listener_started=False)
        write_new(BASE / 'operations/prepared.json', json.dumps(report, sort_keys=True).encode())
        return report
    except BaseException as primary:
        cleanup(primary, lambda: commands.record('preparation_failed', retry_allowed=False, runtime_accepted=False))
        raise
    finally:
        cleanup(sys.exc_info()[1], commands.close)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', required=True)
    p.add_argument('--crate-dir', required=True)
    p.add_argument('--readiness', required=True)
    p.add_argument('--readiness-sha256', required=True)
    p.add_argument('--role', choices=['receipt'], required=True)
    p.add_argument('--instance-id', required=True)
    p.add_argument('--execute-approved-preparation', action='store_true', required=True)
    args = p.parse_args()
    try:
        answer = prepare(args.bundle, args.crate_dir, args.readiness, args.readiness_sha256,
                         args.role, args.instance_id)
        print(json.dumps(answer, sort_keys=True))
        return 0
    except BaseException:
        print(json.dumps(dict(status='HOST_PREPARATION_INCOMPLETE', retry_allowed=False,
                              runtime_accepted=False)), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
