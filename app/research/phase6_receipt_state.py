"""DynamoDB conditional pending/head state. Not a deployed receipt service."""
import os
import re
import threading
from app.research.phase6_anchor_validation import _canonical, _hex, _snapshot, _integer
from app.research.phase6_receipt_request import validate_append_request


class StateUncertain(RuntimeError):
    pass


class ReceiptStateAttempt:
    """Single-process attempt. No retry, takeover, expiry or pending reset API.

    Caller must supply an approved service-side DynamoDB client/table and pass
    only the actual S3 adapter result to commit. This class cannot authenticate
    a supplied retention dictionary or establish physical retention itself.
    """
    def __init__(self, client, *, table_arn, bucket, service_identity):
        if type(table_arn) is not str or not re.fullmatch(
                r'arn:aws:dynamodb:[a-z0-9-]+:[0-9]{12}:table/[A-Za-z0-9_.-]{3,255}', table_arn):
            raise ValueError('Explicit table ARN required')
        attempts = client.meta.config.retries.get('total_max_attempts')
        if type(attempts) is not int or attempts != 1:
            raise ValueError('Disable SDK retries')
        self.client, self.table, self.bucket, self.service = client, table_arn, bucket, service_identity
        self.pid, self.lock, self.state = os.getpid(), threading.Lock(), 'NEW'
        self.pending = None

    def _enter(self, expected):
        if self.pid != os.getpid():
            raise StateUncertain('Inherited attempt')
        with self.lock:
            if self.state != expected:
                raise StateUncertain('Attempt unavailable; no retry')
            self.state = 'UNCERTAIN'

    def claim(self, request, *, attempt_id, receipt_hash):
        q = validate_append_request(request)
        if q['status'] != 'VALID' or not _hex(attempt_id) or not _hex(receipt_hash):
            raise ValueError('Invalid claim')
        q = q['request']
        if q['service_identity'] != self.service:
            raise ValueError('Service mismatch')
        self._enter('NEW')
        values = {':pending': {'S': 'PENDING'}, ':request': {'S': _canonical(q).decode()},
                  ':attempt': {'S': attempt_id}, ':expected': {'S': receipt_hash},
                  ':service': {'S': self.service}}
        names = {'#state': 'state', '#request': 'pending_request', '#attempt': 'attempt_id',
                 '#expected': 'expected_receipt_hash', '#service': 'service_identity'}
        update = 'SET #state=:pending, #request=:request, #attempt=:attempt, #expected=:expected, #service=:service'
        if q['anchor_seq'] == 1:
            condition = 'attribute_not_exists(#run)'
            names.update({'#run': 'run_id', '#seq': 'head_seq', '#hash': 'head_hash', '#until': 'prefix_until_ms'})
            values.update({':zero': {'N': '0'}, ':zero_hash': {'S': '0'*64}})
            update += ', #seq=:zero, #hash=:zero_hash, #until=:zero'
        else:
            names.update({'#seq': 'head_seq', '#hash': 'head_hash'})
            values.update({':committed': {'S': 'COMMITTED'}, ':previous': {'N': str(q['anchor_seq']-1)},
                           ':previous_hash': {'S': q['previous_anchor_hash']}})
            condition = '#state=:committed AND #seq=:previous AND #hash=:previous_hash AND #service=:service'
        try:
            response = self.client.update_item(TableName=self.table, Key={'run_id': {'S': q['run_id']}},
                UpdateExpression=update, ConditionExpression=condition,
                ExpressionAttributeNames=names, ExpressionAttributeValues=values, ReturnValues='ALL_NEW')
            attrs = response['Attributes']
            # The existing prefix horizon is retained atomically with the claim.
            raw_until = attrs['prefix_until_ms']['N']
            if type(raw_until) is not str or not re.fullmatch(r'0|[1-9][0-9]{0,18}', raw_until):
                raise ValueError('Invalid prefix horizon')
            prefix = int(raw_until)
            if not _integer(prefix) or (q['anchor_seq'] > 1 and prefix == 0):
                raise ValueError('Invalid prefix horizon')
            for key, value in [('run_id', q['run_id']), ('state', 'PENDING'),
                               ('pending_request', _canonical(q).decode()), ('attempt_id', attempt_id),
                               ('expected_receipt_hash', receipt_hash), ('service_identity', self.service),
                               ('head_hash', q['previous_anchor_hash'])]:
                if attrs[key] != {'S': value}:
                    raise ValueError('Claim response mismatch')
            if attrs['head_seq'] != {'N': str(q['anchor_seq']-1)}:
                raise ValueError('Claim predecessor mismatch')
        except Exception as exc:
            raise StateUncertain('Claim unproven; no storage write or retry allowed') from exc
        self.pending = (q, attempt_id, receipt_hash, prefix)
        self.state = 'CLAIMED'
        return {'schema_version': 'p6.receipt_claim.v1', 'run_id': q['run_id'],
                'anchor_seq': q['anchor_seq'], 'attempt_id': attempt_id, 'runtime_accepted': False}

    def commit(self, retained):
        self._enter('CLAIMED')
        try:
            q, attempt, receipt_hash, prefix = self.pending
            v = _snapshot(retained)
            fields = {'schema_version', 'bucket', 'key', 'version_id', 'receipt_hash',
                      'retention_until_ms', 'runtime_accepted'}
            key = 'receipts/' + q['run_id'] + '/' + str(q['anchor_seq']).zfill(20) + '.json'
            if (type(v) is not dict or set(v) != fields or v['schema_version'] != 'p6.s3_retained_version.v1'
                    or v['bucket'] != self.bucket or v['key'] != key or v['receipt_hash'] != receipt_hash
                    or type(v['version_id']) is not str or not v['version_id'] or v['version_id'] == 'null'
                    or not _integer(v['retention_until_ms']) or v['retention_until_ms'] == 0
                    or v['runtime_accepted'] is not False):
                raise ValueError('Retention binding mismatch')
            until = min(prefix, v['retention_until_ms']) if prefix else v['retention_until_ms']
            values = {':pending': {'S': 'PENDING'}, ':committed': {'S': 'COMMITTED'},
                ':request': {'S': _canonical(q).decode()}, ':attempt': {'S': attempt},
                ':hash': {'S': receipt_hash}, ':seq': {'N': str(q['anchor_seq'])},
                ':until': {'N': str(until)}, ':version': {'S': v['version_id']}, ':key': {'S': key}}
            self.client.update_item(TableName=self.table, Key={'run_id': {'S': q['run_id']}},
                UpdateExpression='SET #state=:committed, head_seq=:seq, head_hash=:hash, prefix_until_ms=:until, version_id=:version, object_key=:key',
                ConditionExpression='#state=:pending AND pending_request=:request AND attempt_id=:attempt AND expected_receipt_hash=:hash',
                ExpressionAttributeNames={'#state': 'state'}, ExpressionAttributeValues=values)
        except Exception as exc:
            raise StateUncertain('Commit unproven; preserve pending state and inspect read-only') from exc
        self.state = 'COMMITTED'
        return {'schema_version': 'p6.receipt_commit.v1', 'run_id': q['run_id'],
                'anchor_seq': q['anchor_seq'], 'receipt_hash': receipt_hash,
                'prefix_until_ms': until, 'runtime_accepted': False}
