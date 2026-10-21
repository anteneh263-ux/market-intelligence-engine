"""Local request/clock/ownership boundaries; never calls AWS or runs systemd."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import pytest

REPO = Path(__file__).resolve().parents[1]
def load(name, filename):
    s = importlib.util.spec_from_file_location(name, REPO / 'deployment/phase6' / filename)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m

timer = load('recovery_timer', 'receipt-recovery-timer.py')
delivery = load('recovery_delivery', 'host-preparation-command.py')
NOW = 1792330000000
STOP = NOW + 2400000
MONO = 200000000
NEXT = MONO + 2370000000


def bus_fake(monkeypatch, changes=None):
    # get-property emits a typed value; call emits a typed message with positional data.
    # Matches systemd v252 src/busctl/busctl.c json_transform_one vs _message.
    values = {'ExecStart': ('a(sasbttttuii)', [['/sbin/shutdown',
        ['/sbin/shutdown', '-h', 'now'], False, 0, 0, 0, 0, 0, 0, 0]]),
        'LoadState': ('s', 'loaded'), 'User': ('s', ''), 'SubState': ('s', 'waiting'),
        'Unit': ('s', 'mie-qualification-stop.service'), 'NextElapseUSecMonotonic': ('t', NEXT),
        'AccuracyUSec': ('t', 1000000), 'RandomizedDelayUSec': ('t', 0)}
    values.update(changes or {})
    calls = []
    def command(args):
        calls.append(args)
        if args[0] == '/usr/bin/systemd-run':
            return 'Running timer as unit mie-qualification-stop.timer.'
        assert args[:3] == ['/usr/bin/busctl', '--system', '--json=short']
        if args[3] == 'call':
            return json.dumps(dict(type='o', data=['/org/freedesktop/systemd1/unit/' +
                ('timer' if args[-1].endswith('.timer') else 'service')]))
        assert args[3] == 'get-property'
        if args[-1] == 'ActiveState':
            value = 'active' if args[5].endswith('/timer') else 'inactive'
            signature = 's'
        else:
            signature, value = values[args[-1]]
        return json.dumps(dict(type=signature, data=value))
    monkeypatch.setattr(timer, 'command', command)
    return calls


def test_numeric_timer_readback_uses_busctl_actual_value_shape(monkeypatch):
    calls = bus_fake(monkeypatch)
    r = timer.verify(STOP, NEXT, wall=lambda: NOW, monotonic=lambda: MONO)
    assert r['remaining_us'] == 2370000000 and r['timer']['next_us'] == NEXT
    assert all(c[0] == '/usr/bin/busctl' for c in calls)


@pytest.mark.parametrize('changes', [
    {'NextElapseUSecMonotonic': ('t', True)}, {'NextElapseUSecMonotonic': ('t', 0)},
    {'NextElapseUSecMonotonic': ('t', NEXT + 60000000)},
    {'NextElapseUSecMonotonic': ('t', NEXT - 60000000)},
    {'NextElapseUSecMonotonic': ('s', str(NEXT))},
    {'AccuracyUSec': ('t', 60000000)}, {'RandomizedDelayUSec': ('t', 1000000)},
    {'Unit': ('s', 'wrong.service')}, {'SubState': ('s', 'elapsed')},
    {'User': ('s', 'nonroot')}, {'ExecStart': ('a(sasbttttuii)', [])},
    {'ExecStart': ('a(sasbttttuii)', [['/bin/true', ['/bin/true'], False, 0, 0, 0, 0, 0, 0, 0]])}])
def test_wrong_deadline_action_or_timer_properties_refused(monkeypatch, changes):
    calls = bus_fake(monkeypatch, changes)
    with pytest.raises(ValueError):
        timer.verify(STOP, NEXT, wall=lambda: NOW, monotonic=lambda: MONO)
    assert all(c[0] == '/usr/bin/busctl' for c in calls)


def test_replaced_timer_and_insufficient_remaining_window_refused(monkeypatch):
    bus_fake(monkeypatch)
    for expected, now, mono in [(NEXT + 1, NOW, MONO), (NEXT, NOW + 400000, MONO + 400000000)]:
        with pytest.raises(ValueError):
            timer.verify(STOP, expected, wall=lambda: now, monotonic=lambda: mono)


def test_one_timer_creation_after_identity_and_verified_readback(monkeypatch):
    calls = bus_fake(monkeypatch)
    monkeypatch.setattr(timer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(timer, 'identity', lambda: calls.append(['identity']))
    monkeypatch.setattr(timer.time, 'clock_gettime_ns', lambda *a: MONO * 1000)
    result = timer.arm(STOP, wall=lambda: NOW)
    assert result['status'] == 'GUEST_STOP_VERIFIED'
    assert calls[0] == ['identity']
    assert sum(c[0] == '/usr/bin/systemd-run' for c in calls) == 1
    assert '--on-active=2370s' in calls[1]
    assert not any('restart' in c or 'reset-failed' in c for c in calls)


@pytest.mark.parametrize('failure', ['identity', 'existing_or_unknown_timer'])
def test_unknown_creation_or_wrong_host_never_retried(monkeypatch, failure):
    calls = []
    def command(args):
        calls.append(args)
        raise OSError('unknown outcome')
    def identity():
        if failure == 'identity':
            raise ValueError('wrong host')
    monkeypatch.setattr(timer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(timer, 'identity', identity)
    monkeypatch.setattr(timer, 'command', command)
    with pytest.raises((ValueError, OSError)):
        timer.arm(STOP, wall=lambda: NOW)
    assert len(calls) == (0 if failure == 'identity' else 1)


@pytest.mark.parametrize('stop', [NOW, NOW+2000000, NOW+3600000, True])
def test_stale_or_overlong_interval_never_arms(monkeypatch, stop):
    monkeypatch.setattr(timer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(timer, 'identity', lambda: None)
    calls = []
    monkeypatch.setattr(timer, 'command', calls.append)
    with pytest.raises(ValueError):
        timer.arm(stop, wall=lambda: NOW)
    assert calls == []


def test_metadata_is_exact_receipt_only_and_never_reads_credentials(monkeypatch):
    calls = []
    document = dict(instanceId='i-0c5a2a75ca70db228', accountId='989879505232',
        region='eu-north-1', imageId='ami-04e0c7320ad3a3a48', architecture='arm64', instanceType='t4g.small')
    profile = 'arn:aws:iam::989879505232:instance-profile/mie-p6-qualification-001-ReceiptRoleProfile-9F2A3mVypDyp'
    class Metadata:
        def open(self, request, timeout):
            calls.append(request.full_url)
            if request.method == 'PUT':return io.BytesIO(b'token')
            if request.full_url.endswith('/document'):return io.BytesIO(json.dumps(document).encode())
            assert request.full_url.endswith('/iam/info')
            return io.BytesIO(json.dumps(dict(Code='Success', InstanceProfileArn=profile)).encode())
    monkeypatch.setattr(timer.urllib.request, 'build_opener', lambda *a: Metadata())
    assert timer.identity() == document
    document['instanceId'] = 'i-08e89ad2e3feedb8a'
    with pytest.raises(ValueError):timer.identity()
    assert all('/security-credentials' not in url for url in calls)


def test_delivery_uses_new_hash_and_path_preserving_original_and_checks_timer_first(tmp_path):
    r = delivery.request('receipt', 'i-0c5a2a75ca70db228', 'test-version',
        attempt='receipt-002', stop_utc_ms=STOP, expected_timer_us=NEXT)
    script = r['Parameters']['commands'][0]
    assert r['DocumentVersion'] == '1'
    assert 'ea831b59505cfdd04e4c6f78bff4de63fc3fa71045e5279d5d7fe6b7d02c5c69' in script
    assert 'host-preparation-delivery-v2' in script and 'host-preparation-delivery-v1' not in script
    assert script.index('--expected-monotonic-us') < script.index('mkdir -m 0700')
    assert script.rindex('--expected-monotonic-us') > script.index('tar --extract')
    assert script.rindex('--expected-monotonic-us') < script.index('/usr/bin/timeout')
    p = tmp_path/'delivery.sh';p.write_text(script)
    subprocess.run(['/bin/bash', '-n', str(p)], check=True)
    old = delivery.request('receipt', 'i-0c5a2a75ca70db228', 'old-version')['Parameters']['commands'][0]
    assert delivery.KIT_SHA in old and 'host-preparation-delivery-v1' in old
    assert 'MIE_TIMER_PY' not in old


@pytest.mark.parametrize('changes', [dict(role='source'),dict(instance_id='i-08e89ad2e3feedb8a'),
    dict(attempt='other'),dict(expected_timer_us=None),dict(stop_utc_ms=None),dict(version_id='null')])
def test_recovery_refuses_missing_pins_and_wrong_targets(changes):
    args = dict(role='receipt', instance_id='i-0c5a2a75ca70db228', version_id='v2',
        attempt='receipt-002', stop_utc_ms=STOP, expected_timer_us=NEXT)
    args.update(changes)
    with pytest.raises(ValueError):delivery.request(**args)


def test_watch_existing_pair_never_starts_or_updates_and_stops_only_running_receipt(tmp_path):
    from app.research import phase6_qualification_operator as op
    from app.research.phase6_object_store import ObjectWriter
    from app.research.phase6_anchor_validation import _hash
    now = [0]
    c = dict(schema_version='p6.qualification_launch.v1',account_id=op.ACCOUNT,region=op.REGION,
        stack_id='arn:aws:cloudformation:eu-north-1:989879505232:stack/mie-p6-qualification-001/c0769b00-caf6-11f1-8e71-0affdc797e93',
        operator_arn='arn:aws:iam::989879505232:root',not_before_utc_ms=NOW,stop_utc_ms=NOW+30000,
        template_sha256='a'*64,runtime_bundle_sha256='b'*64)
    states={'i-0c5a2a75ca70db228':'stopped','i-08e89ad2e3feedb8a':'stopped'}
    calls=[]
    class API:
        def call(self, service, operation, payload):
            calls.append((service,operation,copy.deepcopy(payload)))
            if operation=='get-caller-identity':return dict(Account=op.ACCOUNT,Arn=c['operator_arn'])
            if operation=='describe-stacks':return {'Stacks':[dict(StackId=c['stack_id'],StackStatus='UPDATE_COMPLETE')]}
            if operation=='describe-instances':
                if now[0]==5000:states['i-0c5a2a75ca70db228']='running'
                ids=payload.get('InstanceIds',list(states))
                return {'Reservations':[dict(OwnerId=op.ACCOUNT,Instances=[dict(InstanceId=i,
                    State={'Name':states[i]},Tags=[{'Key':'aws:cloudformation:stack-id','Value':c['stack_id']},
                    {'Key':'Purpose','Value':'phase6-qualification-only'},
                    {'Key':'aws:cloudformation:logical-id','Value':'ReceiptInstance' if i==timer.INSTANCE else 'SourceInstance'}]) for i in ids])]}
            assert operation=='stop-instances' and payload=={'InstanceIds':[timer.INSTANCE]}
            states[timer.INSTANCE]='stopped';return {}
    root=tmp_path/'watch';root.mkdir();writer=ObjectWriter(str(root),owner=op.ACCOUNT,run_id=_hash(c),max_bytes=1000000)
    result=op.supervise(c,api=API(),writer=writer,utc_ms=lambda:NOW+now[0],
        elapsed_ns=lambda:now[0]*1000000,sleep=lambda s:now.__setitem__(0,now[0]+int(s*1000)))
    assert result['status']=='STOP_CONFIRMED'
    assert sum(c[1]=='stop-instances' for c in calls)==1
    assert all(c[1] not in ('update-stack','start-instances') for c in calls)
