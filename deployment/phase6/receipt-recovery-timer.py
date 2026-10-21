"""Attempt002 guest stop only: one creation or read-only verification; no retries.

This file is embedded verbatim in a frozen SSM request, not installed as a service.
The independent operator and external schedule must be armed before the host starts.
"""
import argparse
import json
import os
import subprocess
import time
import urllib.request

INSTANCE = 'i-0c5a2a75ca70db228'
PROFILE = 'arn:aws:iam::989879505232:instance-profile/mie-p6-qualification-001-ReceiptRoleProfile-9F2A3mVypDyp'
NAME = 'mie-qualification-stop'
SERVICE = 'org.freedesktop.systemd1'
ENV = dict(PATH='/usr/bin:/usr/sbin:/bin:/sbin', LC_ALL='C')


def require(ok):
    if not ok:
        raise ValueError('RECOVERY_TIMER_REFUSED')


def command(args):
    p = subprocess.run(args, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=10, check=False)
    require(p.returncode == 0 and len(p.stdout) <= 65536)
    return p.stdout.decode('utf-8')


def identity():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def get(path, token=None):
        headers = {'X-aws-ec2-metadata-token': token} if token else {
            'X-aws-ec2-metadata-token-ttl-seconds': '60'}
        req = urllib.request.Request('http://169.254.169.254/latest/' + path,
            method='GET' if token else 'PUT', headers=headers)
        with opener.open(req, timeout=3) as response:
            raw = response.read(16385)
        require(len(raw) <= 16384)
        return raw
    token = get('api/token').decode('ascii')
    d = json.loads(get('dynamic/instance-identity/document', token))
    p = json.loads(get('meta-data/iam/info', token))
    require(d['instanceId'] == INSTANCE and d['accountId'] == '989879505232'
        and d['region'] == 'eu-north-1' and d['imageId'] == 'ami-04e0c7320ad3a3a48'
        and d['architecture'] == 'arm64' and d['instanceType'] == 't4g.small'
        and p['Code'] == 'Success' and p['InstanceProfileArn'] == PROFILE)
    return d


def bus(args, signature):
    v = json.loads(command(['/usr/bin/busctl', '--system', '--json=short'] + args))
    require(set(v) == {'type', 'data'} and v['type'] == signature)
    # Method replies are positional message arrays. A property is one typed value.
    if args[0] == 'call':
        require(type(v['data']) is list and len(v['data']) == 1)
        return v['data'][0]
    require(args[0] == 'get-property')
    return v['data']


def unit_path(name):
    p = bus(['call', SERVICE, '/org/freedesktop/systemd1', SERVICE + '.Manager',
             'GetUnit', 's', name], 'o')
    require(type(p) is str and p.startswith('/org/freedesktop/systemd1/unit/'))
    return p


def prop(path, interface, name, signature):
    return bus(['get-property', SERVICE, path, SERVICE + '.' + interface, name], signature)


def read_timer():
    timer = unit_path(NAME + '.timer')
    service = unit_path(NAME + '.service')
    execute = prop(service, 'Service', 'ExecStart', 'a(sasbttttuii)')
    require(type(execute) is list and len(execute) == 1 and type(execute[0]) is list
        and len(execute[0]) == 10 and execute[0][0] == '/sbin/shutdown'
        and execute[0][1] == ['/sbin/shutdown', '-h', 'now'] and execute[0][2] is False)
    require(prop(service, 'Unit', 'LoadState', 's') == 'loaded'
        and prop(service, 'Service', 'User', 's') in ('', 'root')
        and prop(service, 'Unit', 'ActiveState', 's') == 'inactive')
    v = dict(active=prop(timer, 'Unit', 'ActiveState', 's'),
        substate=prop(timer, 'Unit', 'SubState', 's'),
        unit=prop(timer, 'Timer', 'Unit', 's'),
        next_us=prop(timer, 'Timer', 'NextElapseUSecMonotonic', 't'),
        accuracy_us=prop(timer, 'Timer', 'AccuracyUSec', 't'),
        randomized_us=prop(timer, 'Timer', 'RandomizedDelayUSec', 't'))
    require(v['active'] == 'active' and v['substate'] == 'waiting'
        and v['unit'] == NAME + '.service' and type(v['next_us']) is int and v['next_us'] > 0
        and type(v['accuracy_us']) is int and v['accuracy_us'] == 1000000
        and type(v['randomized_us']) is int and v['randomized_us'] == 0)
    return v


def verify(stop_utc_ms, expected_us=None, *, wall=None, monotonic=None):
    wall = wall or (lambda: time.time_ns() // 1000000)
    monotonic = monotonic or (lambda: time.clock_gettime_ns(time.CLOCK_MONOTONIC) // 1000)
    require(type(stop_utc_ms) is int and stop_utc_ms % 1000 == 0)
    v = read_timer()
    now_ms, mono_us = wall(), monotonic()
    wall_left_us = (stop_utc_ms - now_ms) * 1000
    left_us = v['next_us'] - mono_us
    # 1900s command + collection margin; guest stop is ~30s before external stop.
    require(2000000000 <= left_us <= 2700000000
        and 15000000 <= wall_left_us - left_us <= 45000000)
    if expected_us is not None:
        require(type(expected_us) is int and v['next_us'] == expected_us)
    return dict(status='GUEST_STOP_VERIFIED', instance_id=INSTANCE,
        stop_utc_ms=stop_utc_ms, timer=v, sampled_utc_ms=now_ms,
        sampled_monotonic_us=mono_us, remaining_us=left_us, runtime_accepted=False)


def arm(stop_utc_ms, *, wall=None):
    wall = wall or (lambda: time.time_ns() // 1000000)
    require(os.geteuid() == 0)
    identity()  # No IMDS credentials endpoint or secret values.
    require(type(stop_utc_ms) is int and stop_utc_ms % 1000 == 0)
    seconds = (stop_utc_ms - wall()) // 1000 - 30
    require(2100 <= seconds <= 2670)
    # systemd-run refuses existing unit names; never stop/reset/replace a prior timer.
    command(['/usr/bin/systemd-run', '--unit=' + NAME, '--on-active=' + str(seconds) + 's',
        '--timer-property=AccuracySec=1s', '--timer-property=RandomizedDelaySec=0',
        '/sbin/shutdown', '-h', 'now'])
    return verify(stop_utc_ms, wall=wall)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['arm', 'verify'])
    p.add_argument('--stop-utc-ms', type=int, required=True)
    p.add_argument('--expected-monotonic-us', type=int)
    a = p.parse_args()
    require((a.mode == 'arm') == (a.expected_monotonic_us is None))
    if a.mode == 'verify':
        require(os.geteuid() == 0)
        identity()
    result = arm(a.stop_utc_ms) if a.mode == 'arm' else verify(a.stop_utc_ms, a.expected_monotonic_us)
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
