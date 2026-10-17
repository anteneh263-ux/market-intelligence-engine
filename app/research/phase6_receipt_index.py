"""Conditional immutable version-index entries; not append acknowledgements."""
import json
import os
import re
import threading
from app.research.phase6_anchor_validation import _snapshot, _canonical, _hash, _hex, _integer
from app.research.phase6_receipt_request import validate_append_request


class IndexRefused(RuntimeError):
    pass


def _request(request):
    result = validate_append_request(request)
    if result['status'] != 'VALID':
        raise IndexRefused('Invalid request')
    return result['request']


def _record(request, reference):
    q, v = _request(request), _snapshot(reference)
    fields = {'schema_version', 'bucket', 'key', 'version_id', 'receipt_hash',
              'retention_until_ms', 'runtime_accepted'}
    key = 'receipts/' + q['run_id'] + '/' + str(q['anchor_seq']).zfill(20) + '.json'
    if (type(v) is not dict or set(v) != fields or v['schema_version'] != 'p6.s3_retained_version.v1'
            or type(v['bucket']) is not str or not re.fullmatch('[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', v['bucket'])
            or v['key'] != key or type(v['version_id']) is not str or not v['version_id'] or v['version_id'] == 'null'
            or not _hex(v['receipt_hash']) or not _integer(v['retention_until_ms'])
            or v['retention_until_ms'] == 0 or v['runtime_accepted'] is not False):
        raise IndexRefused('Invalid version reference')
    return dict(schema_version='p6.receipt_index_entry.v1', request=q, reference=v)


def _entry_id(q):
    return q['run_id'] + ':' + str(q['anchor_seq']).zfill(20)


class ReceiptVersionIndex:
    """Service-owned table, partition key entry_id(String), no mutation/delete API.

    A found entry might be orphaned by a failed head commit. Never return it as an
    append success without committed-state, signature and retention verification.
    """
    def __init__(self, client, *, table_arn, bucket, service_identity):
        if type(table_arn) is not str or not re.fullmatch(
                r'arn:aws:dynamodb:[a-z0-9-]+:[0-9]{12}:table/[A-Za-z0-9_.-]{3,255}', table_arn):
            raise ValueError('Explicit table ARN required')
        attempts = client.meta.config.retries.get('total_max_attempts')
        if type(attempts) is not int or attempts != 1:
            raise ValueError('Disable SDK retries')
        self.client, self.table, self.bucket, self.service = client, table_arn, bucket, service_identity
        self.pid, self.lock, self.used = os.getpid(), threading.Lock(), False

    def publish(self, request, reference):
        record = _record(request, reference)
        if record['request']['service_identity'] != self.service or record['reference']['bucket'] != self.bucket:
            raise IndexRefused('Configured identity mismatch')
        if os.getpid() != self.pid:
            raise IndexRefused('Inherited index writer')
        with self.lock:
            if self.used:
                raise IndexRefused('Writer consumed; no retry')
            self.used = True
        entry = _entry_id(record['request'])
        try:
            self.client.put_item(TableName=self.table,
                Item={'entry_id': {'S': entry}, 'payload': {'S': _canonical(record).decode()},
                      'payload_hash': {'S': _hash(record)}},
                ConditionExpression='attribute_not_exists(#entry)', ExpressionAttributeNames={'#entry':'entry_id'})
        except Exception as exc:
            raise IndexRefused('Index publication uncertain; do not commit head or retry') from exc
        return dict(schema_version='p6.receipt_index_publication.v1', entry_id=entry,
                    record_hash=_hash(record), runtime_accepted=False)

    def lookup(self, request):
        q = _request(request)
        if q['service_identity'] != self.service:
            raise IndexRefused('Configured identity mismatch')
        result = self.read_entry(q['run_id'], q['anchor_seq'])
        if result['record'] is not None and result['record']['request'] != q:
            raise IndexRefused('Conflicting request for sequence')
        return result

    def read_entry(self, run_id, anchor_seq):
        if not _hex(run_id) or not _integer(anchor_seq) or anchor_seq == 0:
            raise IndexRefused('Invalid index identity')
        entry_id = run_id + ':' + str(anchor_seq).zfill(20)
        try:
            response = self.client.get_item(TableName=self.table,
                Key={'entry_id': {'S': entry_id}}, ConsistentRead=True)
            if 'Item' not in response:
                return dict(status='NOT_FOUND', record=None, runtime_accepted=False)
            item = response['Item']
            if set(item) != {'entry_id', 'payload', 'payload_hash'} or item['entry_id'] != {'S': entry_id}:
                raise ValueError('Index identity mismatch')
            raw = item['payload']['S']
            if type(raw) is not str or len(raw) > 8192 or not raw.isascii():
                raise ValueError('Invalid stored payload')
            stored = _snapshot(json.loads(raw))
            if type(stored) is not dict or set(stored) != {'schema_version','request','reference'}:
                raise ValueError('Invalid stored record')
            record = _record(stored['request'], stored['reference'])
            if (_canonical(record).decode() != raw or item['payload_hash'] != {'S': _hash(record)}
                    or record['request']['run_id'] != run_id or record['request']['anchor_seq'] != anchor_seq
                    or record['request']['service_identity'] != self.service
                    or record['reference']['bucket'] != self.bucket):
                raise ValueError('Conflicting or corrupt index entry')
        except Exception as exc:
            raise IndexRefused('Exact index lookup refused') from exc
        return dict(status='FOUND_UNCONFIRMED', record=record, runtime_accepted=False)
