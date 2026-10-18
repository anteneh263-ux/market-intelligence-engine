"""Fault/boundary tests for the preparation candidate; never runs cloud/host setup."""
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import time

import pytest

REPO = Path(__file__).resolve().parents[1]


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, REPO / 'deployment/phase6' / file)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


host = module('host_preparation', 'prepare-qualification-host.py')
probe = module('host_readiness', 'host-clock-readiness.py')


def archive(rows):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.PAX_FORMAT) as tar:
        for name, raw, kind in rows:
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = '/etc/passwd'
            if name == 'pax':
                info.pax_headers = {'comment': 'unexpected'}
            tar.addfile(info, io.BytesIO(raw))
    return gzip.compress(out.getvalue(), mtime=0)


def simple(rows=None):
    return archive(rows or [('a/b.py', b'text', tarfile.REGTYPE)])


def bundle(manifest_change=None, file_change=None):
    content = b'code'
    manifest = dict(schema_version='p6.qualification_bundle.v1',
        purpose='SYNTHETIC_RECEIPT_QUALIFICATION_CANDIDATE_ONLY',
        target='CPython 3.11 Linux aarch64 glibc >= 2.28', native_clockbound_included=False,
        identities_included=False, files={'code/app/x.py': {'bytes': 4, 'sha256': host.digest(content)}})
    if manifest_change:
        manifest_change(manifest)
    rows = [('code/app/x.py', content, tarfile.REGTYPE),
            ('manifest.json', json.dumps(manifest).encode(), tarfile.REGTYPE)]
    if file_change:
        file_change(rows)
    return archive(rows)


def test_hash_checked_before_parsing():
    with pytest.raises(ValueError):
        host.unpack(b'not gzip', '0' * 64)


@pytest.mark.parametrize('name,kind', [('../escape', tarfile.REGTYPE), ('/escape', tarfile.REGTYPE),
    ('x/../escape', tarfile.REGTYPE), ('a//b', tarfile.REGTYPE), ('a/./b', tarfile.REGTYPE),
    ('x', tarfile.SYMTYPE), ('x', tarfile.LNKTYPE), ('x', tarfile.FIFOTYPE),
    ('x', tarfile.DIRTYPE), ('pax', tarfile.REGTYPE)])
def test_unsafe_members_refused(name, kind):
    raw = simple([(name, b'', kind)])
    with pytest.raises(ValueError):
        host.unpack(raw, host.digest(raw))


@pytest.mark.parametrize('names', [('same', 'same'), ('a', 'a/b')])
def test_duplicate_or_conflicting_member_refused(names):
    raw = simple([(n, b'', tarfile.REGTYPE) for n in names])
    with pytest.raises(ValueError):
        host.unpack(raw, host.digest(raw))


def test_expansion_bound(monkeypatch):
    raw = simple([('a', b'0' * 30000, tarfile.REGTYPE)])
    monkeypatch.setattr(host, 'MAX_EXPANDED', 20000)
    with pytest.raises(ValueError):
        host.unpack(raw, host.digest(raw))


def test_manifest_and_file_inventory():
    raw = bundle()
    assert host.bundle_files(raw, host.digest(raw)) == {'code/app/x.py': b'code'}


@pytest.mark.parametrize('change', [lambda m: m['files']['code/app/x.py'].update(sha256='0' * 64),
    lambda m: m['files']['code/app/x.py'].update(bytes=True),
    lambda m: m.update(identities_included=True), lambda m: m.update(unexpected='field'),
    lambda m: m['files'].clear()])
def test_manifest_lies_refused(change):
    raw = bundle(change)
    with pytest.raises(ValueError):
        host.bundle_files(raw, host.digest(raw))


def test_unlisted_member_refused():
    raw = bundle(file_change=lambda rows: rows.append(('extra', b'a', tarfile.REGTYPE)))
    with pytest.raises(ValueError):
        host.bundle_files(raw, host.digest(raw))


def test_duplicate_json_key_refused():
    with pytest.raises(ValueError):
        host.parse(b'{"x":1,"x":2}')


def test_fresh_install_readback_permissions_and_repeat_refusal(tmp_path):
    dest = tmp_path / 'package'
    prior = os.umask(0o077)
    try:
        host.publish_tree(dest, {'a/b.py': b'correct'})
    finally:
        os.umask(prior)
    assert (dest / 'a/b.py').read_bytes() == b'correct'
    assert stat.S_IMODE((dest / 'a/b.py').stat().st_mode) == 0o644
    assert stat.S_IMODE((dest / 'a').stat().st_mode) == 0o755
    with pytest.raises(FileExistsError):
        host.publish_tree(dest, {'a/b.py': b'replace'})
    assert (dest / 'a/b.py').read_bytes() == b'correct'


def test_redirected_parent_or_file_refused(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        host.mkdir_fresh(alias / 'dest')
    file = real / 'x'
    file.write_bytes(b'good')
    (real / 'link').symlink_to(file)
    with pytest.raises(ValueError):
        host.read_file(real / 'link')
    with pytest.raises(FileExistsError):
        host.write_new(file, b'bad')
    assert file.read_bytes() == b'good'


def versions(rows):
    return host.installed_versions(json.dumps(rows).encode(), {'wheels': [{'name': 'six', 'version': '1.17.0'}]})


def test_version_inventory_checks_actual_install():
    assert versions([{'name': 'six', 'version': '1.17.0'}, {'name': 'pip', 'version': '24.0'}])['six'] == '1.17.0'
    for rows in ([], [{'name': 'six', 'version': '0.0'}],
                 [{'name': 'six', 'version': '1.17.0'}, {'name': 'other', 'version': '1'}],
                 [{'name': 'six', 'version': '1.17.0'}] * 2):
        with pytest.raises(ValueError):
            versions(rows)


def runner(tmp_path):
    r = host.Commands(tmp_path, elapsed=time.monotonic)
    r.env = dict(os.environ)
    return r


def test_actual_command_trace_and_nonzero_preserves_output(tmp_path):
    r = runner(tmp_path)
    try:
        assert r.run([sys.executable, '-c', 'print("ok")']) == b'ok\n'
        with pytest.raises(ValueError):
            r.run([sys.executable, '-c', 'print("before failure",flush=True);raise SystemExit(4)'])
    finally:
        r.close()
    rows = [json.loads(s) for s in (tmp_path / 'steps.jsonl').read_text().splitlines()]
    assert [r['kind'] for r in rows] == ['requested', 'completed', 'requested', 'failed']
    assert (tmp_path / '002.log').read_bytes() == b'before failure\n'
    assert rows[-1]['retry_allowed'] is False


def test_log_overflow_fails_and_kills(tmp_path):
    r = runner(tmp_path)
    try:
        with pytest.raises(ValueError):
            r.run([sys.executable, '-c', 'print("x"*1000000)'], log_limit=5000)
    finally:
        r.close()
    assert (tmp_path / '001.log').stat().st_size <= 5000


def test_timeout_kills_descendant_after_parent_exit(tmp_path):
    marker = tmp_path / 'should-not-exist'
    child = 'import time,pathlib;time.sleep(.8);pathlib.Path(' + repr(str(marker)) + ').write_text("alive")'
    parent = 'import subprocess,sys;subprocess.Popen([sys.executable,"-c",' + repr(child) + '])'
    r = runner(tmp_path)
    try:
        with pytest.raises(ValueError):
            r.run([sys.executable, '-c', parent], seconds=.2)
    finally:
        r.close()
    time.sleep(.9)
    assert not marker.exists()


def test_expired_overall_budget_never_executes(tmp_path):
    r = runner(tmp_path)
    r.deadline = time.monotonic() - 1
    try:
        with pytest.raises(ValueError):
            r.run([sys.executable, '-c', 'raise SystemExit(0)'])
    finally:
        r.close()
    assert not list(tmp_path.glob('*.log'))


def tracking(ref=0, interval='16', leap='Normal', source='A9FEA97B (169.254.169.123)'):
    return ('Reference ID : ' + source + '\nRef time (UTC) : Sun Oct 18 12:00:%02d 2026\n' % ref
            + 'Update interval : ' + interval + ' seconds\nLeap status : ' + leap + '\n')


class FakeClock:
    def __init__(self, fail=None):
        self.n = 0
        self.closed = False
        self.fail = fail

    def sample(self):
        self.n += 1
        if self.n == self.fail:
            raise ValueError('terminal')
        return dict(clock_session_id='owner-1', monotonic_ns=self.n, utc_ms=self.n)

    def close(self):
        self.closed = True


def observe(rows, clock=None):
    ticks = [0]
    events, created = [], []
    clock = clock or FakeClock()
    rows = iter(rows)
    last = [tracking()]
    def current():
        last[0] = next(rows, last[0])
        return last[0]
    def create():
        created.append(clock)
        return clock
    def sleep(n):
        ticks[0] += n
    try:
        result = probe.observe(create, current, events.append, lambda: ticks[0], sleep)
    except ValueError as exc:
        return str(exc), events, created, clock
    return result, events, created, clock


def test_actual_warmup_needs_two_advancing_regular_references_and_one_owner():
    answer, events, created, clock = observe([tracking(0), tracking(0), tracking(16), tracking(32)])
    assert answer['status'] == 'READINESS_OBSERVED'
    assert answer['runtime_accepted'] is False
    assert len(created) == 1 and clock.closed and clock.n == 10
    assert [e['regular_updates'] for e in events if e['kind'] == 'chrony'] == [0, 0, 1, 2]


@pytest.mark.parametrize('raw', [tracking(interval='NaN'), tracking(interval='Infinity'),
    tracking(interval='1'), tracking(leap='Not synchronised'), tracking(source='other')])
def test_bad_tracking_never_opens_native_owner(raw):
    answer, _, created, _ = observe([raw])
    assert answer == 'CHRONY_READINESS_TIMEOUT' and created == []


def test_invalid_tracking_resets_regular_counter():
    answer, events, _, _ = observe([tracking(0), tracking(16), tracking(16, leap='Not synchronised'),
                                  tracking(32), tracking(48)])
    assert answer['status'] == 'READINESS_OBSERVED'
    assert [e['regular_updates'] for e in events if e['kind'] == 'chrony'] == [0, 1, 0, 1, 2]


def test_regressed_reference_is_terminal_without_native_owner():
    answer, _, created, _ = observe([tracking(16), tracking(0)])
    assert answer == 'CHRONY_REFERENCE_REGRESSED' and created == []


def test_native_refusal_never_reopens_owner_and_closes():
    answer, events, created, clock = observe([tracking(0), tracking(16), tracking(32)], FakeClock(fail=4))
    assert answer == 'terminal' and len(created) == 1 and clock.closed and clock.n == 4
    assert len([e for e in events if e['kind'] == 'native_observation']) == 3


def test_duplicate_tracking_field_refused():
    with pytest.raises(ValueError):
        probe.tracking(tracking() + 'Leap status : Normal\n')


def test_failure_logging_does_not_replace_primary_failure(tmp_path):
    r = runner(tmp_path)
    original = r.record
    def record(kind, **data):
        if kind == 'failed':
            raise OSError('trace unavailable')
        original(kind, **data)
    r.record = record
    try:
        with pytest.raises(ValueError, match='HOST_PREPARATION_REFUSED'):
            r.run([sys.executable, '-c', 'print("preserved",flush=True);raise SystemExit(8)'])
    finally:
        r.close()
    assert (tmp_path / '001.log').read_bytes() == b'preserved\n'


def test_native_close_failure_preserves_primary_refusal():
    clock = FakeClock(fail=3)
    def close():
        raise OSError('close failed')
    clock.close = close
    answer, _, created, _ = observe([tracking(0), tracking(16), tracking(32)], clock)
    assert answer == 'terminal' and len(created) == 1


def test_native_close_failure_prevents_success():
    clock = FakeClock()
    def close():
        raise OSError('close failed')
    clock.close = close
    with pytest.raises(OSError, match='close failed'):
        observe([tracking(0), tracking(16), tracking(32)], clock)


def test_canonical_long_path_header_accepted_and_still_validated():
    name = 'wheels/' + 'a' * 110 + '.whl'
    raw = simple([(name, b'bytes', tarfile.REGTYPE)])
    assert host.unpack(raw, host.digest(raw)) == {name: b'bytes'}


delivery = module('host_preparation_command', 'host-preparation-command.py')


@pytest.mark.parametrize('role,instance,version', [
    ('other', 'i-' + '1' * 17, 'v1'), ('source', 'i-' + '1' * 16, 'v1'),
    ('receipt', 'i-' + '1' * 17, 'null'), ('source', 'i-' + '1' * 17, 'v;touch /tmp/bad'),
    ('source', 'i-' + '1' * 17, '')])
def test_delivery_rejects_unbounded_or_unversioned_target(role, instance, version):
    with pytest.raises(ValueError):
        delivery.request(role, instance, version)


def test_delivery_fixed_version_hash_and_explicit_target_are_shell_parseable(tmp_path):
    import subprocess
    request = delivery.request('source', 'i-' + '1' * 17, 'fake.test+/version=')
    assert request['InstanceIds'] == ['i-' + '1' * 17]
    script = request['Parameters']['commands'][0]
    assert script.index('sha256sum --check') < script.index('tar --extract')
    assert '--version-id fake.test+/version=' in script
    assert '--execute-approved-preparation' in script
    p = tmp_path / 'syntax-only.sh'
    p.write_text(script)
    subprocess.run(['/bin/bash', '-n', str(p)], check=True)


def test_delivery_parent_is_explicitly_traversable_under_restrictive_umask(tmp_path):
    import subprocess
    script = delivery.request('source', 'i-' + '1' * 17, 'version')['Parameters']['commands'][0]
    line = next(line for line in script.splitlines() if line.startswith('if [ ! -e /opt/mie ]'))
    parent = tmp_path / 'mie'
    rewritten = line.replace('/opt/mie', str(parent))
    subprocess.run(['/bin/bash', '-c', 'umask 077; ' + rewritten], check=True)
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    # Existing directories are never silently changed by this preparation prerequisite.
    parent.chmod(0o700)
    subprocess.run(['/bin/bash', '-c', 'umask 077; ' + rewritten], check=True)
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert 'root:755' in script


@pytest.mark.parametrize('role,profile,instance', [
    ('receipt', 'mie-p6-qualification-001-ReceiptRoleProfile-9F2A3mVypDyp', 'i-0c5a2a75ca70db228'),
    ('source', 'mie-p6-qualification-001-SourceRoleProfile-zUFLg78C0FsN', 'i-08e89ad2e3feedb8a')])
def test_imds_accepts_actual_foundation_profile_and_refuses_other_role(monkeypatch, role, profile, instance):
    # Facts from separately read-back foundation instance-profile identities, not host.PROFILES.
    observed = dict(InstanceProfileArn='arn:aws:iam::989879505232:instance-profile/' + profile,
                    Code='Success')
    document = dict(instanceId=instance, accountId='989879505232', region='eu-north-1',
                    imageId='ami-04e0c7320ad3a3a48', architecture='arm64', instanceType='t4g.small')
    calls = []
    class Metadata:
        def open(self, request, timeout):
            calls.append(request.full_url)
            if request.full_url.endswith('/api/token'):
                assert request.method == 'PUT'
                return io.BytesIO(b'test-token')
            if request.full_url.endswith('/dynamic/instance-identity/document'):
                return io.BytesIO(json.dumps(document).encode())
            assert request.full_url.endswith('/meta-data/iam/info')
            return io.BytesIO(json.dumps(observed).encode())
    monkeypatch.setattr(host.urllib.request, 'build_opener', lambda *args: Metadata())
    assert host.imds_identity(instance, role)['profile_arn'] == observed['InstanceProfileArn']
    observed['InstanceProfileArn'] = 'arn:aws:iam::989879505232:instance-profile/unapproved'
    with pytest.raises(ValueError):
        host.imds_identity(instance, role)
    assert all('/security-credentials' not in path for path in calls)
