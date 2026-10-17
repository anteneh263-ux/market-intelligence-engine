"""Bind the HTTP boundary to actual append/read-only receipt implementations."""
import secrets
from app.research.phase6_receipt_index import ReceiptVersionIndex
from app.research.phase6_anchor_validation import _snapshot, _hash
from app.research.phase6_receipt_append import ReceiptAppendAttempt
from app.research.phase6_receipt_head import indexed_challenged_head
from app.research.phase6_receipt_repeat import original_receipt
from app.research.phase6_receipt_http import ReceiptHttpBoundary


def build_receipt_service(*, dynamodb, s3, table_arn, index_table_arn, bucket, owner,
                          trust, expected_trust_hash, signing_key, clock, domain_name, principal_arns):
    t = _snapshot(trust)
    if _hash(t) != expected_trust_hash or signing_key.public_key().public_bytes_raw().hex() != t['public_key_hex']:
        raise ValueError('Pinned service trust mismatch')
    common = dict(dynamodb=dynamodb, s3=s3, table_arn=table_arn, index_table_arn=index_table_arn,
                  bucket=bucket, owner=owner, trust=t, expected_trust_hash=expected_trust_hash,
                  signing_key=signing_key, clock=clock)

    def append(request):
        minimum_time = None
        if request['anchor_seq'] > 1:
            verified = indexed_challenged_head(run_id=request['run_id'],
                nonce=secrets.token_hex(32), **common)['head']
            if (verified['anchor_seq'] != request['anchor_seq'] - 1
                    or verified['anchor_hash'] != request['previous_anchor_hash']):
                raise ValueError('Predecessor mismatch')
            index = ReceiptVersionIndex(dynamodb, table_arn=index_table_arn,
                bucket=bucket, service_identity=t['service_identity'])
            for seq in range(1, request['anchor_seq']):
                entry = index.read_entry(request['run_id'], seq)
                if (entry['record'] is None or
                        entry['record']['request']['subject_hash'] == request['subject_hash']):
                    raise ValueError('Missing predecessor or duplicate subject')
            minimum_time = verified['observed_utc_ms']

        def append_clock():
            now = clock()
            if minimum_time is not None and (type(now) is not int or now < minimum_time):
                raise ValueError('Clock moved behind verified predecessor')
            return now

        # A new instance per invocation does not bypass the persistent pending CAS.
        return ReceiptAppendAttempt(dynamodb=dynamodb, s3=s3, table_arn=table_arn,
            index_table_arn=index_table_arn, bucket=bucket, owner=owner,
            service_identity=t['service_identity'], signing_key=signing_key, clock=append_clock,
            not_before_ms=t['not_before_utc_ms'], not_after_ms=t['not_after_utc_ms']).append(request)

    def lookup(payload):
        return original_receipt(request=payload['request'], nonce=payload['nonce'], **common)

    def head(payload):
        return indexed_challenged_head(run_id=payload['run_id'], nonce=payload['nonce'], **common)

    return ReceiptHttpBoundary(account_id=owner, domain_name=domain_name,
        principal_arns=principal_arns, append=append, lookup=lookup, head=head)
