"""Exact-version read-only inspection, not committed-head or signature authority."""
import hashlib
import re
from datetime import datetime, timezone, timedelta
from app.research.phase6_anchor_validation import _snapshot, _hex, _integer


class LookupRefused(RuntimeError):
    pass


def inspect_retained_version(client, reference, *, owner, now_utc_ms):
    """Inspect a trusted expected reference; never write or retry an append.

    The reference must come from approved service state, not an untrusted caller.
    Signature/chain/current-head verification remains a separate requirement.
    """
    q = _snapshot(reference)
    fields = {'schema_version', 'bucket', 'key', 'version_id', 'receipt_hash',
              'retention_until_ms', 'runtime_accepted'}
    if (type(q) is not dict or set(q) != fields
            or q['schema_version'] != 'p6.s3_retained_version.v1'
            or type(owner) is not str or not re.fullmatch('[0-9]{12}', owner)
            or type(q['bucket']) is not str or not re.fullmatch('[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', q['bucket'])
            or type(q['key']) is not str or not re.fullmatch(r'receipts/[0-9a-f]{64}/[0-9]{20}\.json', q['key'])
            or type(q['version_id']) is not str or not q['version_id'] or q['version_id'] == 'null'
            or not _hex(q['receipt_hash']) or not _integer(q['retention_until_ms'])
            or not _integer(now_utc_ms) or q['retention_until_ms'] <= now_utc_ms
            or q['runtime_accepted'] is not False):
        raise LookupRefused('Invalid or expired expected reference')
    common = dict(Bucket=q['bucket'], Key=q['key'], VersionId=q['version_id'], ExpectedBucketOwner=owner)
    try:
        response = client.get_object(**common)
        stream = response['Body']
        try:
            body = stream.read(65537)
        finally:
            stream.close()
        if (response.get('VersionId') != q['version_id'] or type(body) is not bytes
                or not 0 < len(body) <= 65536 or hashlib.sha256(body).hexdigest() != q['receipt_hash']):
            raise ValueError('Exact retained content mismatch')
        retention = client.get_object_retention(**common)['Retention']
        end = retention.get('RetainUntilDate')
        required = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=q['retention_until_ms'])
        if (retention.get('Mode') != 'COMPLIANCE' or type(end) is not datetime
                or end.tzinfo is None or end < required):
            raise ValueError('Retention not established')
    except Exception as exc:
        raise LookupRefused('Read-only inspection failed; no recovery write authorized') from exc
    return dict(schema_version='p6.receipt_lookup.v1', body=body, reference=q,
                runtime_accepted=False, signature_verified=False, current_head_verified=False)
