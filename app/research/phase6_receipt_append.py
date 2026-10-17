"""Service-side append assembly; no HTTP endpoint or deployed service authority."""
import os
import secrets
import threading
from app.research.phase6_anchor_validation import _canonical, _hash, _integer
from app.research.phase6_receipt_request import validate_append_request
from app.research.phase6_receipt_index import ReceiptVersionIndex
from app.research.phase6_receipt_state import ReceiptStateAttempt
from app.research.phase6_s3_retention import S3ReceiptRetention


class AppendUncertain(RuntimeError):
    pass


class ReceiptAppendAttempt:
    """Own signing -> pending claim -> retained version -> committed head.

    Injected clients/key/clock are trusted service configuration, never HTTP
    request fields. One invocation per instance; recovery is read-only upstream.
    """
    def __init__(self, *, dynamodb, s3, table_arn, index_table_arn, bucket, owner,
                 service_identity, signing_key, clock, not_before_ms, not_after_ms):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        if not isinstance(signing_key, Ed25519PrivateKey):
            raise ValueError('Ed25519 service key required')
        if not (_integer(not_before_ms) and _integer(not_after_ms) and not_before_ms < not_after_ms):
            raise ValueError('Invalid signing validity interval')
        if table_arn == index_table_arn:
            raise ValueError('State and index tables must be distinct')
        self._index = ReceiptVersionIndex(dynamodb, table_arn=index_table_arn,
                                         bucket=bucket, service_identity=service_identity)
        self._state = ReceiptStateAttempt(dynamodb, table_arn=table_arn,
                                         bucket=bucket, service_identity=service_identity)
        self._retention = S3ReceiptRetention(s3, bucket=bucket, owner=owner,
            service_identity=service_identity,
            public_key_hex=signing_key.public_key().public_bytes_raw().hex())
        self._key, self._clock, self._service = signing_key, clock, service_identity
        self._start, self._end = not_before_ms, not_after_ms
        self._pid, self._lock, self._used = os.getpid(), threading.Lock(), False

    def append(self, request):
        if os.getpid() != self._pid:
            raise AppendUncertain('Inherited append attempt')
        preflight = validate_append_request(request)
        if preflight['status'] != 'VALID' or preflight['request']['service_identity'] != self._service:
            raise ValueError('Invalid append request')
        q = preflight['request']
        with self._lock:
            if self._used:
                raise AppendUncertain('Append attempt consumed; no retry')
            self._used = True
        try:
            now = self._clock()
            until = now + 30 * 86400000 if _integer(now) else None
            if not _integer(until) or not self._start <= now <= self._end:
                raise ValueError('Service signing clock outside validity')
            receipt = dict(q, schema_version='p6.anchor_receipt.v1', retained_utc_ms=now)
            receipt['signature'] = self._key.sign(_canonical(receipt)).hex()
            digest = _hash(receipt)
            self._state.claim(q, attempt_id=secrets.token_hex(32), receipt_hash=digest)
            # An expired preceding prefix cannot be extended by a new receipt.
            prefix = self._state.pending[3]
            if prefix and prefix <= now:
                raise ValueError('Previous retention horizon expired')
            retained = self._retention.retain(receipt, retain_until_utc_ms=until)
            self._index.publish(q, retained)
            before_commit = self._clock()
            if not _integer(before_commit) or not now <= before_commit <= self._end:
                raise ValueError('Clock moved backwards or signing validity expired')
            if before_commit >= (min(prefix, until) if prefix else until):
                raise ValueError('Retention expired before commit')
            committed = self._state.commit(retained)
            completed = self._clock()
            if (not _integer(completed) or not before_commit <= completed <= self._end
                    or completed >= committed['prefix_until_ms']):
                raise ValueError('Append completion outside clock/retention validity')
        except Exception as exc:
            raise AppendUncertain('Append incomplete or uncertain; no retry or acknowledgement') from exc
        return dict(schema_version='p6.receipt_append_result.v1', receipt=receipt,
                    retained_version=retained, committed=committed, runtime_accepted=False)
