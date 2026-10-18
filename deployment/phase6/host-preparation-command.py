"""Render one reviewed SSM request; never calls AWS or executes the generated commands.

Operator must separately verify the exact instance's current CloudFormation ownership
and the uploaded object's exact-version readback before sending this request once.
"""
import argparse
import json
import re
import shlex

KIT_SHA = '66d595ca5fd3ca5aa689969b60eeba57c217949bd4f24615206e48978ba08592'
READINESS_SHA = '8ca157bc1ff5909fd2ccfec43c29195ccf21a878b5f831e1ad4ee75e391b39a6'
BUCKET = 'mie-p6-qualification-001-989879505232-artifacts'
DELIVERY = '/var/lib/mie/qualification-001/host-preparation-delivery-v1'


def request(role, instance_id, version_id):
    if (role not in {'source', 'receipt'} or re.fullmatch(r'i-[0-9a-f]{17}', instance_id) is None
            or version_id == 'null' or re.fullmatch(r'[A-Za-z0-9_.+/=-]{1,1024}', version_id) is None):
        raise ValueError('DELIVERY_REQUEST_REFUSED')
    aws = ['/usr/bin/env', '-i', 'HOME=/root', 'PATH=/usr/local/bin:/usr/bin:/bin',
           'AWS_CONFIG_FILE=/dev/null', 'AWS_SHARED_CREDENTIALS_FILE=/dev/null',
           'AWS_MAX_ATTEMPTS=1', 'AWS_RETRY_MODE=standard',
           'aws', 's3api', 'get-object', '--region', 'eu-north-1', '--endpoint-url',
           'https://s3.eu-north-1.amazonaws.com', '--bucket', BUCKET, '--key',
           'code/' + KIT_SHA + '.tar.gz', '--version-id', version_id, '--checksum-mode', 'ENABLED',
           '--cli-connect-timeout', '3', '--cli-read-timeout', '30', '--no-cli-pager',
           '--no-cli-auto-prompt', '--output', 'json', DELIVERY + '/host-kit.tar.gz']
    prepare = ['/usr/bin/python3', DELIVERY + '/prepare-qualification-host.py',
        '--bundle', DELIVERY + '/runtime.tar.gz', '--crate-dir', DELIVERY + '/crates',
        '--readiness', DELIVERY + '/host-clock-readiness.py', '--readiness-sha256', READINESS_SHA,
        '--role', role, '--instance-id', instance_id, '--execute-approved-preparation']
    # This directory is empty in the foundation template's fresh conditional instances.
    script = '\n'.join([
        '#!/bin/bash', 'set -euo pipefail', 'umask 077',
        'test "$(id -u)" = 0',
        'test "$(realpath /var/lib/mie/qualification-001)" = /var/lib/mie/qualification-001',
        'test "$(stat -c %U:%a /var/lib/mie/qualification-001)" = root:700',
        'mkdir -m 0700 ' + shlex.quote(DELIVERY),
        shlex.join(aws) + ' > ' + shlex.quote(DELIVERY + '/download-response.json'),
        'printf \'%s  %s\\n\' ' + shlex.quote(KIT_SHA) + ' ' + shlex.quote(DELIVERY + '/host-kit.tar.gz')
            + ' | sha256sum --check --strict -',
        # Only the exact reviewed hash is extracted. Its eight regular members were verified locally.
        'tar --extract --gzip --no-same-owner --no-same-permissions --file='
            + shlex.quote(DELIVERY + '/host-kit.tar.gz') + ' --directory=' + shlex.quote(DELIVERY),
        shlex.join(['/usr/bin/timeout', '--kill-after=5s', '1900'] + prepare),
        '',
    ])
    return dict(DocumentName='AWS-RunShellScript', InstanceIds=[instance_id], TimeoutSeconds=120,
                Comment='MIE qualification host preparation only; no receipt runtime or pilot',
                Parameters={'commands': [script], 'executionTimeout': ['2100']})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--role', choices=['source', 'receipt'], required=True)
    p.add_argument('--instance-id', required=True)
    p.add_argument('--code-version-id', required=True)
    a = p.parse_args()
    print(json.dumps(request(a.role, a.instance_id, a.code_version_id), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
