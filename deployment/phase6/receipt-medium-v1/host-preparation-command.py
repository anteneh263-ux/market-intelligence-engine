"""Render one reviewed SSM request; never calls AWS or executes the generated commands.

Operator must separately verify the exact instance's current CloudFormation ownership
and the uploaded object's exact-version readback before sending this request once.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex

KIT_SHA = 'c3a024b3e09483f3c2341ec87cb10e6843dbd39dc174fa7b2aac660666bf216e'
TIMER_SHA = '6a9a190de2a230a7f2b93a7d8646c783c2298933473dc42daa3810ad4b4f6ca4'
READINESS_SHA = '8ca157bc1ff5909fd2ccfec43c29195ccf21a878b5f831e1ad4ee75e391b39a6'
BUCKET = 'mie-p6-qualification-001-989879505232-artifacts'
INSTANCE = 'i-0c5a2a75ca70db228'
DELIVERY = '/var/lib/mie/qualification-001/host-preparation-delivery-medium-v1'


def timer_script(mode, stop_utc_ms, expected_us=None):
    if (type(stop_utc_ms) is not int or stop_utc_ms <= 0 or stop_utc_ms % 1000
            or mode not in {'arm', 'verify'} or (mode == 'verify' and
            (type(expected_us) is not int or expected_us <= 0))
            or (mode == 'arm' and expected_us is not None)):
        raise ValueError('RECOVERY_TIMER_REQUEST_REFUSED')
    args = ['/usr/bin/python3', '-', mode, '--stop-utc-ms', str(stop_utc_ms)]
    if expected_us is not None:
        args += ['--expected-monotonic-us', str(expected_us)]
    raw = Path(__file__).with_name('receipt-recovery-timer.py').read_bytes()
    if hashlib.sha256(raw).hexdigest() != TIMER_SHA:
        raise ValueError('MEDIUM_TIMER_SOURCE_REFUSED')
    source = raw.decode('utf-8')
    return shlex.join(args) + " <<'MIE_TIMER_PY'\n" + source + '\nMIE_TIMER_PY\n'


def timer_request(stop_utc_ms):
    return dict(DocumentName='AWS-RunShellScript', DocumentVersion='1',
        InstanceIds=[INSTANCE], TimeoutSeconds=60,
        Comment='MIE receipt-medium-v1 guest stop only; one creation, no retry',
        Parameters={'commands': ['set -eu\n' + timer_script('arm', stop_utc_ms)],
                    'executionTimeout': ['120']})


def request(role, instance_id, version_id, *, stop_utc_ms, expected_timer_us):
    if (role != 'receipt' or instance_id != INSTANCE
            or re.fullmatch(r'[0-9a-f]{64}', KIT_SHA) is None
            or type(version_id) is not str or version_id == 'null'
            or re.fullmatch(r'[A-Za-z0-9_.+/=-]{1,1024}', version_id) is None):
        raise ValueError('MEDIUM_DELIVERY_REQUEST_REFUSED')
    kit_sha, delivery = KIT_SHA, DELIVERY
    prefix = [timer_script('verify', stop_utc_ms, expected_timer_us)]
    aws = ['/usr/bin/env', '-i', 'HOME=/root', 'PATH=/usr/local/bin:/usr/bin:/bin',
           'AWS_CONFIG_FILE=/dev/null', 'AWS_SHARED_CREDENTIALS_FILE=/dev/null',
           'AWS_MAX_ATTEMPTS=1', 'AWS_RETRY_MODE=standard',
           'aws', 's3api', 'get-object', '--region', 'eu-north-1', '--endpoint-url',
           'https://s3.eu-north-1.amazonaws.com', '--bucket', BUCKET, '--key',
           'code/' + kit_sha + '.tar.gz', '--version-id', version_id, '--checksum-mode', 'ENABLED',
           '--cli-connect-timeout', '3', '--cli-read-timeout', '30', '--no-cli-pager',
           '--no-cli-auto-prompt', '--output', 'json', delivery + '/host-kit.tar.gz']
    prepare = ['/usr/bin/python3', delivery + '/prepare-qualification-host.py',
        '--bundle', delivery + '/runtime.tar.gz', '--crate-dir', delivery + '/crates',
        '--readiness', delivery + '/host-clock-readiness.py', '--readiness-sha256', READINESS_SHA,
        '--role', role, '--instance-id', instance_id, '--execute-approved-preparation']
    # This directory is empty in the foundation template's fresh conditional instances.
    script = '\n'.join([
        '#!/bin/bash', 'set -euo pipefail', 'umask 077',
        'test "$(id -u)" = 0', *prefix,
        'test "$(realpath /var/lib/mie/qualification-001)" = /var/lib/mie/qualification-001',
        'test "$(stat -c %U:%a /var/lib/mie/qualification-001)" = root:700',
        # The frozen installer sets umask077. Provision its already-approved parent
        # explicitly so the nonroot clock user can traverse it; never reuse surprises.
        'test "$(realpath /opt)" = /opt',
        'if [ ! -e /opt/mie ] && [ ! -L /opt/mie ]; then mkdir -m 0755 /opt/mie; fi',
        'test "$(realpath /opt/mie)" = /opt/mie',
        'test "$(stat -c %U:%a /opt/mie)" = root:755',
        'mkdir -m 0700 ' + shlex.quote(delivery),
        shlex.join(aws) + ' > ' + shlex.quote(delivery + '/download-response.json'),
        'printf \'%s  %s\\n\' ' + shlex.quote(kit_sha) + ' ' + shlex.quote(delivery + '/host-kit.tar.gz')
            + ' | sha256sum --check --strict -',
        # Only the exact reviewed hash is extracted. Its eight regular members were verified locally.
        'tar --extract --gzip --no-same-owner --no-same-permissions --file='
            + shlex.quote(delivery + '/host-kit.tar.gz') + ' --directory=' + shlex.quote(delivery),
        *prefix,  # Recheck the same timer after download/extraction, before preparation.
        shlex.join(['/usr/bin/timeout', '--kill-after=5s', '1900'] + prepare),
        '',
    ])
    return dict(DocumentName='AWS-RunShellScript', DocumentVersion='1', InstanceIds=[instance_id], TimeoutSeconds=120,
                Comment='MIE qualification host preparation only; no receipt runtime or pilot',
                Parameters={'commands': [script], 'executionTimeout': ['2100']})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--role', choices=['receipt'], required=True)
    p.add_argument('--instance-id', required=True)
    p.add_argument('--code-version-id', required=True)
    p.add_argument('--stop-utc-ms', type=int, required=True)
    p.add_argument('--expected-timer-us', type=int, required=True)
    a = p.parse_args()
    print(json.dumps(request(a.role, a.instance_id, a.code_version_id, stop_utc_ms=a.stop_utc_ms, expected_timer_us=a.expected_timer_us), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
