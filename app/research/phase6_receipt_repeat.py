"""Read-only original-receipt return after indexed full-prefix verification."""
import json
from app.research.phase6_anchor_validation import _canonical, _hash, _snapshot
from app.research.phase6_receipt_request import validate_append_request
from app.research.phase6_receipt_index import ReceiptVersionIndex
from app.research.phase6_receipt_lookup import inspect_retained_version
from app.research.phase6_receipt_head import indexed_challenged_head


class RepeatRefused(RuntimeError):
    pass


def original_receipt(*, request, dynamodb, s3, table_arn, index_table_arn, bucket,
                     owner, trust, expected_trust_hash, signing_key, nonce, clock):
    """Never append or replace. Missing/pending/conflicting evidence refuses.

    Caller must independently establish authorized, still-live process/clock
    continuity for recovery. A read-only result does not permit a pilot restart.
    """
    try:
        checked = validate_append_request(request)
        if checked['status'] != 'VALID':
            raise ValueError('Invalid request')
        q, t = checked['request'], _snapshot(trust)
        if q['service_identity'] != t['service_identity']:
            raise ValueError('Service identity mismatch')
        index = ReceiptVersionIndex(dynamodb, table_arn=index_table_arn,
                                    bucket=bucket, service_identity=t['service_identity'])
        found = index.lookup(q)
        if found['status'] != 'FOUND_UNCONFIRMED':
            raise ValueError('Original receipt absent')
        reference = found['record']['reference']
        # Verify the requested object before obtaining the challenged snapshot.
        started = clock()
        read = inspect_retained_version(s3, reference, owner=owner, now_utc_ms=started)
        receipt = json.loads(read['body'])
        if _canonical(receipt) != read['body']:
            raise ValueError('Noncanonical original receipt')
        expected_fields = set(q) | {'signature', 'retained_utc_ms'}
        if set(receipt) != expected_fields or receipt['schema_version'] != 'p6.anchor_receipt.v1':
            raise ValueError('Wrong receipt schema')
        for name, value in q.items():
            if name != 'schema_version' and receipt[name] != value:
                raise ValueError('Original receipt does not bind exact request')
        result = indexed_challenged_head(dynamodb=dynamodb, s3=s3, table_arn=table_arn,
            index_table_arn=index_table_arn, bucket=bucket, owner=owner, run_id=q['run_id'],
            trust=t, expected_trust_hash=expected_trust_hash, signing_key=signing_key,
            nonce=nonce, clock=clock)
        head = result['head']
        # Immutable index is checked again: the head must cover the exact object read.
        if index.lookup(q) != found or head['anchor_seq'] < q['anchor_seq']:
            raise ValueError('Original receipt not in verified prefix')
        now = clock()
        if (type(now) is not int or type(started) is not int or not started <= head['observed_utc_ms'] <= now
                or now-started > t['max_head_age_ms'] or now > t['not_after_utc_ms']
                or now >= head['retention_until_ms'] or _hash(receipt) != reference['receipt_hash']):
            raise ValueError('Lookup completion validity failed')
        return dict(schema_version='p6.original_receipt_result.v1', receipt=receipt,
                    head=head, runtime_accepted=False, recovery_authorized=False)
    except Exception as exc:
        raise RepeatRefused('Original committed receipt not established; no write or retry authorized') from exc
