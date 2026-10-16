"""Single-attempt S3 receipt retention adapter. No sequence/head authority."""
import base64
import hashlib
import re
import os
import threading
from datetime import datetime, timedelta, timezone

from app.research.phase6_anchor_validation import _canonical, _snapshot, _hex, _integer
from app.research.phase6_receipt_request import validate_append_request


class RetentionUncertain(RuntimeError):
    """Do not retry a write: its durable outcome may be unknown."""


class S3ReceiptRetention:
    """One write attempt per instance; the injected client must disable retries.

    Bucket provisioning, authenticated caller/clock, sequence serialization and
    committed-head publication belong to the service coordinator, not this adapter.
    """
    def __init__(self, client, *, bucket, owner, service_identity, public_key_hex):
        if not (type(bucket) is str and re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket)
                and type(owner) is str and re.fullmatch(r'[0-9]{12}', owner)
                and _hex(public_key_hex)):
            raise ValueError('Invalid retention configuration')
        attempts = client.meta.config.retries.get('total_max_attempts')
        if type(attempts) is not int or attempts != 1:
            raise ValueError('S3 client retries must be disabled')
        self.client, self.bucket, self.owner = client, bucket, owner
        self.service_identity, self.public_key_hex = service_identity, public_key_hex
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._used = False

    def retain(self, receipt, *, retain_until_utc_ms):
        if os.getpid() != self._pid:
            raise RetentionUncertain('Inherited adapter cannot write from another process')
        # Validate/detach before any external call; errors here are not writes.
        q = _snapshot(receipt)
        fields = {'schema_version', 'run_id', 'anchor_seq', 'subject_kind',
                  'subject_hash', 'previous_anchor_hash', 'retained_utc_ms',
                  'service_identity', 'signature'}
        if type(q) is not dict or set(q) != fields or q['schema_version'] != 'p6.anchor_receipt.v1':
            raise ValueError('Invalid receipt')
        request = {k: v for k, v in q.items() if k not in ('signature', 'retained_utc_ms')}
        request['schema_version'] = 'p6.anchor_append_request.v1'
        if validate_append_request(request)['status'] != 'VALID':
            raise ValueError('Invalid receipt identity')
        if (q['service_identity'] != self.service_identity or not _hex(q['signature'], 128)
                or not _integer(q['retained_utc_ms']) or not _integer(retain_until_utc_ms)
                or retain_until_utc_ms < q['retained_utc_ms'] + 30 * 86400000):
            raise ValueError('Invalid receipt service or retention interval')
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        unsigned = {k: v for k, v in q.items() if k != 'signature'}
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(self.public_key_hex)).verify(
            bytes.fromhex(q['signature']), _canonical(unsigned))
        until = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=retain_until_utc_ms)
        body = _canonical(q)
        digest = hashlib.sha256(body).hexdigest()
        key = 'receipts/' + q['run_id'] + '/' + str(q['anchor_seq']).zfill(20) + '.json'
        with self._lock:
            if self._used:
                raise RetentionUncertain('Adapter already consumed; no second write permitted')
            self._used = True
        common = dict(Bucket=self.bucket, Key=key, ExpectedBucketOwner=self.owner)
        try:
            put = self.client.put_object(**common, Body=body, IfNoneMatch='*',
                ContentType='application/json', ChecksumAlgorithm='SHA256',
                ChecksumSHA256=base64.b64encode(hashlib.sha256(body).digest()).decode('ascii'),
                ObjectLockMode='COMPLIANCE', ObjectLockRetainUntilDate=until)
            version = put.get('VersionId')
            if type(version) is not str or not version or version == 'null':
                raise ValueError('Missing immutable version')
            read = self.client.get_object(**common, VersionId=version)
            stream = read['Body']
            try:
                actual = stream.read(len(body) + 1)
            finally:
                stream.close()
            if read.get('VersionId') != version or actual != body:
                raise ValueError('Retained version content mismatch')
            retention = self.client.get_object_retention(**common, VersionId=version)['Retention']
            end = retention.get('RetainUntilDate')
            if (retention.get('Mode') != 'COMPLIANCE' or type(end) is not datetime
                    or end.tzinfo is None or end < until):
                raise ValueError('Retention not established')
        except Exception as exc:
            raise RetentionUncertain('Write outcome or retention unproven; stop and inspect read-only') from exc
        return dict(schema_version='p6.s3_retained_version.v1', bucket=self.bucket,
                    key=key, version_id=version, receipt_hash=digest,
                    retention_until_ms=retain_until_utc_ms, runtime_accepted=False)
