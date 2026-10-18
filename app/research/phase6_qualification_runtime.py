"""Explicit synthetic receipt qualification entry point; no provisioning/pilot."""
import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import socket
import ssl
import stat
import sys
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app.research.phase6_anchor_validation import _hash, _hex, _trust_schema
from app.research.phase6_clockbound import ClockBoundClock
from app.research.phase6_object_store import ObjectWriter, inspect_objects, read_verified_object, _root, _read
from app.research.phase6_receipt_client import VerifiedReceiptClient
from app.research.phase6_receipt_host import ClockBoundReceiptHost
from app.research.phase6_receipt_http import _pairs, _reject_number
from app.research.phase6_source_intake import encoded

ACCOUNT = '989879505232'
REGION = 'eu-north-1'
QUALIFICATION = 'qualification-001'
PREFIX = 'mie-p6-qualification-001'
HOSTNAME = 'mie-receipt.internal'
PORT = 8443


def require(value):
    if not value:
        raise ValueError('QUALIFICATION_REFUSED')


def snapshot(value, depth=0, _budget=None):
    budget = [1024, 65536] if _budget is None else _budget
    budget[0] -= 1
    require(depth <= 8 and budget[0] >= 0 and budget[1] >= 0)
    if type(value) is dict:
        require(len(value) <= 64 and all(type(k) is str and k.isascii() and len(k) <= 128 for k in value))
        budget[1] -= sum(len(k) for k in value)
        require(budget[1] >= 0)
        return {k: snapshot(v, depth + 1, budget) for k, v in value.items()}
    if type(value) is str:
        budget[1] -= len(value)
        require(value.isascii() and len(value) <= 16384 and budget[1] >= 0)
        return value
    require(type(value) is int and 0 <= value < 2**63)
    return value


def parse(raw):
    require(type(raw) is bytes and len(raw) <= 65536)
    return snapshot(json.loads(raw, object_pairs_hook=_pairs,
                                parse_float=_reject_number, parse_constant=_reject_number))


def validate_config(value):
    c = snapshot(value)
    require(len(encoded(c)) <= 65536)
    require(type(c) is dict and set(c) == {
        'schema_version', 'qualification_id', 'account_id', 'region', 'trust',
        'ca_pem', 'ca_sha256', 'library_path', 'library_sha256', 'source', 'receipt'})
    require(c['schema_version'] == 'p6.aws_receipt_qualification.v1'
            and c['qualification_id'] == QUALIFICATION
            and c['account_id'] == ACCOUNT and c['region'] == REGION)
    _trust_schema(c['trust'])
    t = c['trust']
    require(t['service_identity'] == PREFIX + '-receipt'
            and t['endpoint_id'] == PREFIX + '-ssm-mtls-8443'
            and type(t['max_clock_skew_ms']) is int and 1 <= t['max_clock_skew_ms'] <= 100
            and 0 < t['max_head_age_ms'] <= 5000
            and 0 < t['not_after_utc_ms'] - t['not_before_utc_ms'] <= 3600000)
    require(type(c['library_path']) is str and c['library_path'].startswith('/opt/mie/')
            and os.path.normpath(c['library_path']) == c['library_path']
            and _hex(c['library_sha256']) and _hex(c['ca_sha256']))
    require(type(c['ca_pem']) is str and len(c['ca_pem']) <= 16384)
    paths = []
    for role, expected in [('source', {'clock', 'client', 'result'}), ('receipt', {'clock', 'result'})]:
        r = c[role]
        require(type(r) is dict and set(r) == {'role_arn', 'instance_id', 'secret_arn',
            'secret_version_id', 'certificate_sha256', 'roots'})
        require(type(r['role_arn']) is str and re.fullmatch(
            'arn:aws:iam::' + ACCOUNT + ':role/' + PREFIX + '-[A-Za-z0-9_-]{1,128}', r['role_arn']))
        require(type(r['instance_id']) is str and re.fullmatch('i-[0-9a-f]{17}', r['instance_id']))
        require(type(r['secret_arn']) is str and re.fullmatch(
            'arn:aws:secretsmanager:' + REGION + ':' + ACCOUNT + ':secret:' + PREFIX
            + '/' + role + '-identity-[A-Za-z0-9]{6}', r['secret_arn']))
        require(type(r['secret_version_id']) is str and re.fullmatch('[A-Za-z0-9-]{32,64}', r['secret_version_id']))
        require(_hex(r['certificate_sha256']) and type(r['roots']) is dict and set(r['roots']) == expected)
        for path in r['roots'].values():
            require(type(path) is str and path.startswith('/') and path != '/'
                    and os.path.normpath(path) == path and not path.startswith('//')
                    and not re.search(r'(?i)(?:^|[^a-z0-9])h[-_ ]?0?25(?:[^0-9]|$)', path))
            paths.append(path)
    require(c['source']['role_arn'] != c['receipt']['role_arn']
            and c['source']['instance_id'] != c['receipt']['instance_id']
            and c['source']['certificate_sha256'] != c['receipt']['certificate_sha256'])
    require(len(set(paths)) == len(paths)
            and not any(a != b and a.startswith(b + '/') for a in paths for b in paths))
    return c


def read_config(path, digest):
    require(_hex(digest) and type(path) is str and path.startswith('/')
            and os.path.normpath(path) == path)
    parent, name = os.path.split(path)
    fd = _root(parent)
    try:
        s = os.stat(name, dir_fd=fd, follow_symlinks=False)
        require(stat.S_ISREG(s.st_mode) and s.st_size <= 65536)
        raw = _read(fd, name, s.st_size)
    finally:
        os.close(fd)
    require(hashlib.sha256(raw).hexdigest() == digest)
    return validate_config(parse(raw))


def check_identity(c, role, sts):
    response = sts.get_caller_identity()
    r = c[role]
    arn = 'arn:aws:sts::' + ACCOUNT + ':assumed-role/' + r['role_arn'].rsplit('/', 1)[1] + '/' + r['instance_id']
    require(response.get('Account') == ACCOUNT and response.get('Arn') == arn)
    return arn


def secret_material(c, role, secrets_client):
    r = c[role]
    response = secrets_client.get_secret_value(SecretId=r['secret_arn'], VersionId=r['secret_version_id'])
    require(response.get('ARN') == r['secret_arn'] and response.get('VersionId') == r['secret_version_id']
            and type(response.get('SecretString')) is str and 'SecretBinary' not in response)
    material = parse(response['SecretString'].encode('utf-8'))
    keys = {'certificate_pem', 'private_key_pem'} | ({'signing_private_key_hex'} if role == 'receipt' else set())
    require(type(material) is dict and set(material) == keys
            and all(type(v) is str and len(v) <= 16384 for v in material.values()))
    ca = x509.load_pem_x509_certificate(c['ca_pem'].encode('ascii'))
    cert = x509.load_pem_x509_certificate(material['certificate_pem'].encode('ascii'))
    require(c['ca_pem'] == ca.public_bytes(serialization.Encoding.PEM).decode('ascii')
            and material['certificate_pem'] == cert.public_bytes(serialization.Encoding.PEM).decode('ascii'))
    der = serialization.Encoding.DER
    require(hashlib.sha256(ca.public_bytes(der)).hexdigest() == c['ca_sha256']
            and ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            and hashlib.sha256(cert.public_bytes(der)).hexdigest() == r['certificate_sha256'])
    cert.verify_directly_issued_by(ca)
    key = serialization.load_pem_private_key(material['private_key_pem'].encode('ascii'), password=None)
    require(material['private_key_pem'] == key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode('ascii'))
    def public(k):
        return k.public_bytes(der, serialization.PublicFormat.SubjectPublicKeyInfo)
    require(public(key.public_key()) == public(cert.public_key()))
    for certificate in (cert, ca):
        require(int(certificate.not_valid_before_utc.timestamp() * 1000) <= c['trust']['not_before_utc_ms']
                and int(certificate.not_valid_after_utc.timestamp() * 1000) >= c['trust']['not_after_utc_ms'])
    if role == 'receipt':
        require(_hex(material['signing_private_key_hex']))
        signing = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(material['signing_private_key_hex']))
        require(signing.public_key().public_bytes_raw().hex() == c['trust']['public_key_hex'])
    else:
        signing = None
    return material, signing


def tls_context(c, role, material):
    """Linux-only private-key material, sealed before OpenSSL opens the descriptor."""
    require(platform.system() == 'Linux')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if role == 'receipt' else ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = role == 'source'
    context.load_verify_locations(cadata=c['ca_pem'])
    fd = os.memfd_create('mie-qualification-tls', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.fchmod(fd, 0o600)
        pending = memoryview((material['certificate_pem'] + '\n' + material['private_key_pem']).encode('ascii'))
        while pending:
            count = os.write(fd, pending)
            require(count > 0)
            pending = pending[count:]
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        context.load_cert_chain('/proc/self/fd/' + str(fd))
    finally:
        os.close(fd)
    return context


def _close_all(resources):
    failed = sys.exc_info()[0] is not None
    error = None
    for resource in reversed(resources):
        try:
            resource.close()
        except BaseException as exc:
            if error is None:
                error = exc
    if error is not None and not failed:
        raise error


class RecordedClock:
    def __init__(self, c, writer):
        self.writer = writer
        self.native = ClockBoundClock(library_path=c['library_path'], library_sha256=c['library_sha256'],
                                      max_offset_ms=c['trust']['max_clock_skew_ms'])
        self.claim = None
        self.count = 0
        self.head = '0' * 64
        self.trust = c['trust']
        try:
            self.claim = writer.claim()
        except BaseException:
            try:
                self.native.close()
            except BaseException:
                pass
            raise

    def event(self):
        try:
            s = self.native.sample()
            require(self.trust['not_before_utc_ms'] <= s['utc_ms'] <= self.trust['not_after_utc_ms'])
            record = dict(schema_version='p6.qualification_clock.v1', sequence=self.count + 1,
                          previous_hash=self.head, sample=s)
            self.head = self.writer.put(encoded(record), claim=self.claim)
            self.count += 1
            return {k: s[k] for k in ('utc_ms', 'monotonic_ns', 'clock_session_id')}
        except BaseException:
            self.native.stopped = True
            raise

    def close(self):
        failed = sys.exc_info()[0] is not None
        error = None
        try:
            self.native.close()
        except BaseException as exc:
            error = exc
        try:
            self.writer.close(claim=self.claim)
        except BaseException:
            if not failed and error is None:
                raise
        if error is not None and not failed:
            raise error


def subjects(config_hash):
    # Protocol slots only. These bytes are not trial registration/admission evidence.
    reservation = dict(schema_version='p6.receipt_qualification_fixture.v1',
        purpose='SYNTHETIC_PROTOCOL_FIXTURE_ONLY', config_hash=config_hash,
        slot_kind='RESERVATION', sequence=1)
    run_id = _hash(reservation)
    return [reservation] + [dict(schema_version='p6.receipt_qualification_fixture.v1',
        purpose='SYNTHETIC_PROTOCOL_FIXTURE_ONLY', config_hash=config_hash,
        run_id=run_id, slot_kind=kind, sequence=i)
        for i, kind in enumerate(('GENESIS', 'CHECKPOINT'), 2)]


def inspect_writer(writer):
    objects = dict(writer._objects)
    header = writer.header
    result = inspect_objects(writer.root, header=header, objects=objects)
    require(result['structural_status'] == 'VALID')
    return dict(root=writer.root, header=header, objects=objects, inspection=result)


def run(c, role, *, sts, secrets_client, dynamodb=None, s3=None):
    c = validate_config(c)
    require(role in ('source', 'receipt'))
    identity = check_identity(c, role, sts)
    material, signing = secret_material(c, role, secrets_client)
    context = tls_context(c, role, material)
    del material
    config_hash = _hash(c)
    fixtures = subjects(config_hash)
    run_id = _hash(fixtures[0])
    roots = c[role]['roots']
    # Inspect all destinations before publishing any store header. No mkdir/default roots.
    for path in roots.values():
        fd = _root(path)
        try:
            require(not os.listdir(fd))
        finally:
            os.close(fd)
    writers = {}
    owners = []
    result = None
    try:
        for name, path in roots.items():
            writers[name] = ObjectWriter(path, owner=ACCOUNT, run_id=run_id, max_bytes=16 * 1024 * 1024)
        result = writers['result']
        result.put(encoded(dict(schema_version='p6.qualification_start.v1', config=c,
                                config_hash=config_hash, run_id=run_id, role=role, identity=identity,
                                synthetic_subjects=fixtures, prospective_evidence=False)))
        if role == 'receipt':
            require(dynamodb is not None and s3 is not None)
            host = ClockBoundReceiptHost(writers['clock'], library_path=c['library_path'],
                library_sha256=c['library_sha256'], client_runs={c['source']['certificate_sha256']: [run_id]},
                dynamodb=dynamodb, s3=s3,
                table_arn='arn:aws:dynamodb:' + REGION + ':' + ACCOUNT + ':table/' + PREFIX + '-state',
                index_table_arn='arn:aws:dynamodb:' + REGION + ':' + ACCOUNT + ':table/' + PREFIX + '-index',
                bucket=PREFIX + '-' + ACCOUNT + '-receipts', owner=ACCOUNT,
                trust=c['trust'], expected_trust_hash=_hash(c['trust']), signing_key=signing)
            owners.append(host)
            now = host._utc()
            require(c['trust']['not_before_utc_ms'] <= now <= c['trust']['not_after_utc_ms'])
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(('127.0.0.1', PORT))
                listener.listen(1)
                detail = host.serve(listener, context=context, hostname=HOSTNAME, max_requests=6)
            status = 'REQUESTS_SERVED'
        else:
            clock = RecordedClock(c, writers['clock'])
            owners.append(clock)
            clock.event()  # Native admission before a remote request.
            client = VerifiedReceiptClient(writers['client'], run_id=run_id, owner=ACCOUNT,
                trust=c['trust'], expected_trust_hash=_hash(c['trust']), clock=clock.event,
                port=PORT, context=context, hostname=HOSTNAME,
                server_cert_sha256=c['receipt']['certificate_sha256'])
            owners.append(client)
            packages = []
            for fixture in fixtures:
                result.put(encoded(fixture))
                package = client.append(subject_kind=fixture['slot_kind'], subject_hash=_hash(fixture))
                packages.append(result.put(encoded(package)))
            detail = dict(verified_packages=packages, verified_subjects=3)
            status = 'ROUNDTRIP_VERIFIED'
        _close_all(owners)
        owners.clear()
        inventory = {name: inspect_writer(writer) for name, writer in writers.items() if name != 'result'}
        report = dict(schema_version='p6.aws_receipt_qualification_result.v1', config_hash=config_hash,
            run_id=run_id, role=role, status=status, detail=detail, inventories=inventory,
            runtime_accepted=False, prospective_evidence=False, source_independent_retention_verified=False)
        digest = result.put(encoded(report))
        result.close()
        result_inventory = inspect_writer(result)
        require(read_verified_object(result.root, header=result.header, objects=result_inventory['objects'],
                                     digest=digest) == encoded(report))
        return dict(status=status, report_hash=digest, result_inventory=result_inventory,
                    runtime_accepted=False, prospective_evidence=False)
    finally:
        # Owned writers must be closed through their owners, never with an absent claim.
        try:
            _close_all(owners)
        finally:
            _close_all([w for w in writers.values() if w._claim is None])


def aws_clients(region):
    import boto3
    from botocore.config import Config
    require(region == REGION)
    session = boto3.Session(region_name=region)
    require(session.get_credentials().method == 'iam-role')
    config = Config(connect_timeout=3, read_timeout=5, retries={'total_max_attempts': 1},
                    ignore_configured_endpoint_urls=True, signature_version='v4')
    return {name: session.client(name, region_name=region, config=config,
            endpoint_url='https://' + name + '.' + region + '.amazonaws.com')
            for name in ('sts', 'secretsmanager', 'dynamodb', 's3')}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Synthetic receipt qualification; requires approved precreated roots/config.')
    parser.add_argument('--config', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--role', required=True, choices=['source', 'receipt'])
    args = parser.parse_args(argv)
    try:
        require(platform.system() == 'Linux')
        c = read_config(args.config, args.sha256)
        clients = aws_clients(c['region'])
        answer = run(c, args.role, sts=clients['sts'], secrets_client=clients['secretsmanager'],
                     dynamodb=clients['dynamodb'], s3=clients['s3'])
    except BaseException:
        # AWS responses/exception strings may contain secrets. Evidence already written stays intact.
        print(json.dumps(dict(status='INCOMPLETE', retry_allowed=False, runtime_accepted=False)), flush=True)
        return 1
    print(json.dumps(answer, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
