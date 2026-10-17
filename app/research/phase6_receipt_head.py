"""Read-only challenged head assembly; requires trusted service configuration."""
import json
import re
from app.research.phase6_anchor_validation import _snapshot, _hex, _integer, _hash, _canonical, verify_anchors
from app.research.phase6_receipt_lookup import inspect_retained_version


class HeadRefused(RuntimeError):
    pass


def challenged_head(*, dynamodb, s3, table_arn, bucket, owner, run_id,
                    references, trust, expected_trust_hash, signing_key, nonce, clock):
    """Inspect the full supplied version index, then sign its current committed head.

    Version index, SDK clients, pinned trust and key belong to the service. This
    function does not provide authentication, HTTP transport or a durable index.
    """
    try:
        refs, t = _snapshot(references), _snapshot(trust)
        if (not _hex(run_id) or not _hex(nonce) or not _hex(expected_trust_hash)
                or type(refs) is not list or not 1 <= len(refs) <= 10000
                or type(table_arn) is not str or not re.fullmatch(
                    r'arn:aws:dynamodb:[a-z0-9-]+:[0-9]{12}:table/[A-Za-z0-9_.-]{3,255}', table_arn)):
            raise ValueError('Invalid head request')
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        if (not isinstance(signing_key, Ed25519PrivateKey) or _hash(t) != expected_trust_hash
                or signing_key.public_key().public_bytes_raw().hex() != t['public_key_hex']):
            raise ValueError('Signing trust mismatch')
        started = clock()
        if not _integer(started):
            raise ValueError('Invalid service clock')
        def read_state():
            return dynamodb.get_item(TableName=table_arn, Key={'run_id': {'S': run_id}},
                                     ConsistentRead=True)['Item']
        state = read_state()
        if (state['run_id'] != {'S': run_id} or state['state'] != {'S': 'COMMITTED'}
                or state['service_identity'] != {'S': t['service_identity']}
                or state['head_seq'] != {'N': str(len(refs))}):
            raise ValueError('State incomplete or prefix truncated')
        receipts = []
        horizons = []
        for seq, ref in enumerate(refs, 1):
            key = 'receipts/' + run_id + '/' + str(seq).zfill(20) + '.json'
            if ref['bucket'] != bucket or ref['key'] != key:
                raise ValueError('Version index identity mismatch')
            found = inspect_retained_version(s3, ref, owner=owner, now_utc_ms=started)
            doc = json.loads(found['body'])
            if _canonical(doc) != found['body']:
                raise ValueError('Noncanonical receipt')
            receipts.append(doc)
            horizons.append(ref['retention_until_ms'])
        last = refs[-1]
        if (state['head_hash'] != {'S': _hash(receipts[-1])}
                or state['version_id'] != {'S': last['version_id']}
                or state['object_key'] != {'S': last['key']}):
            raise ValueError('Committed version mismatch')
        raw_horizon = state['prefix_until_ms']['N']
        if type(raw_horizon) is not str or not re.fullmatch(r'[1-9][0-9]{0,18}', raw_horizon):
            raise ValueError('Invalid prefix horizon')
        until = int(raw_horizon)
        if not _integer(until) or until > min(horizons):
            raise ValueError('Prefix retention unproven')
        # An append/pending transition during inspection invalidates this snapshot.
        if read_state() != state:
            raise ValueError('State changed during inspection')
        now = clock()
        if (not _integer(now) or now < started or now-started > t['max_head_age_ms']
                or until <= now):
            raise ValueError('Clock or retention expired during inspection')
        subjects = [dict(subject_kind=x['subject_kind'], subject_hash=x['subject_hash']) for x in receipts]
        if receipts[0]['subject_hash'] != run_id:
            raise ValueError('Reservation identity mismatch')
        validation = verify_anchors(dict(trust=t, expected_trust_hash=expected_trust_hash,
            expected_run_id=run_id, expected_subjects=subjects, receipts=receipts,
            head=None, challenge=None, now_utc_ms=now))
        if validation['cryptographic_status'] != 'VALID' or now < receipts[-1]['retained_utc_ms']:
            raise ValueError('Receipt chain invalid')
        head = dict(schema_version='p6.anchor_head.v1', run_id=run_id, anchor_seq=len(receipts),
            anchor_hash=_hash(receipts[-1]), service_identity=t['service_identity'], nonce=nonce,
            observed_utc_ms=now, retention_until_ms=until)
        head['signature'] = signing_key.sign(_canonical(head)).hex()
        return dict(schema_version='p6.challenged_head_result.v1', head=head, runtime_accepted=False)
    except Exception as exc:
        raise HeadRefused('Current retained prefix not established; no signed head returned') from exc


def indexed_challenged_head(*, dynamodb, s3, table_arn, index_table_arn, bucket,
                           owner, run_id, trust, expected_trust_hash, signing_key, nonce, clock):
    """Resolve the service's immutable index before full challenged-head inspection."""
    from app.research.phase6_receipt_index import ReceiptVersionIndex
    try:
        if not _hex(run_id) or table_arn == index_table_arn:
            raise ValueError('Invalid table/run configuration')
        t = _snapshot(trust)
        index = ReceiptVersionIndex(dynamodb, table_arn=index_table_arn,
                                    bucket=bucket, service_identity=t['service_identity'])
        state = dynamodb.get_item(TableName=table_arn, Key={'run_id': {'S': run_id}},
                                  ConsistentRead=True)['Item']
        raw = state['head_seq']['N']
        if (state['state'] != {'S': 'COMMITTED'} or type(raw) is not str
                or not re.fullmatch(r'[1-9][0-9]{0,4}', raw) or int(raw) > 10000):
            raise ValueError('Committed bounded prefix required')
        refs = []
        for seq in range(1, int(raw)+1):
            found = index.read_entry(run_id, seq)
            if found['status'] != 'FOUND_UNCONFIRMED':
                raise ValueError('Missing indexed prefix entry')
            refs.append(found['record']['reference'])
        return challenged_head(dynamodb=dynamodb, s3=s3, table_arn=table_arn, bucket=bucket,
            owner=owner, run_id=run_id, references=refs, trust=t, expected_trust_hash=expected_trust_hash,
            signing_key=signing_key, nonce=nonce, clock=clock)
    except Exception as exc:
        raise HeadRefused('Indexed current head not established') from exc
