"""WP5-1 pure half-open purge and separate embargo; no data access."""
import hashlib
import json
import re

__all__ = ['purge_embargo']
_CHECKS = ('SAMPLE_SCHEMA', 'INTERVAL_VALIDITY', 'PURGE_OVERLAP',
           'EMBARGO_WINDOW', 'PARTITION_DISJOINTNESS', 'PURGE_HASH')


def _integer(value):
    return type(value) is int


def _overlaps(start, end, test_start, test_end):
    return start < test_end and test_start < end


def _embargoed(feature, test_end, duration):
    return test_end <= feature < test_end + duration


def purge_embargo(request):
    """Return a deterministic input-bound partition or a closed refusal.

    Input ordering is retained. Timestamps and duration are integer milliseconds;
    signed timestamps are permitted, embargo duration must be nonnegative.
    All absent partitions are null on failure, never a usable partial partition.
    """
    result = dict(purged_sample_ids=None, embargoed_sample_ids=None, retained_sample_ids=None,
                  purge_count=None, embargo_count=None, ambiguity=[], purge_status='INVALID',
                  checks=[dict(check_id=k, status='NOT_EVALUATED') for k in _CHECKS],
                  reason_code=None, purge_report_hash=None)

    def mark(index, valid, reason):
        result['checks'][index]['status'] = 'PASS' if valid else 'FAIL'
        if not valid:
            result['reason_code'] = reason
        return valid

    valid = (type(request) is dict and set(request) == {
        'sample_index', 'test_window', 'embargo_ms', 'experiment_id'})
    if valid:
        samples = request['sample_index']
        valid = (type(samples) is list and type(request['experiment_id']) is str and
                 re.fullmatch('[0-9a-f]{64}', request['experiment_id']) is not None and
                 type(request['test_window']) is list and len(request['test_window']) == 2)
    if valid:
        seen = set()
        for row in samples:
            if not (type(row) is dict and set(row) == {
                'sample_id', 'feature_time_ms', 'label_start_ms', 'label_end_ms'} and
                type(row['sample_id']) is str and row['sample_id'] and row['sample_id'] not in seen):
                valid = False
                break
            seen.add(row['sample_id'])
    if not mark(0, valid, 'SAMPLE_SCHEMA_INVALID'):
        return result
    start, end = request['test_window']
    valid = _integer(start) and _integer(end) and start < end
    for row in samples:
        if not (all(_integer(row[k]) for k in ('feature_time_ms', 'label_start_ms', 'label_end_ms'))
                and row['label_start_ms'] < row['label_end_ms']):
            result['ambiguity'].append(row['sample_id'])
            valid = False
    if not mark(1, valid, 'INTERVAL_AMBIGUOUS'):
        return result
    purged = [row['sample_id'] for row in samples if _overlaps(
        row['label_start_ms'], row['label_end_ms'], start, end)]
    mark(2, True, 'PURGE_OVERLAP_INVALID')
    duration = request['embargo_ms']
    if not mark(3, _integer(duration) and duration >= 0, 'EMBARGO_WINDOW_INVALID'):
        return result
    excluded = set(purged)
    embargoed = [row['sample_id'] for row in samples if row['sample_id'] not in excluded
                 and _embargoed(row['feature_time_ms'], end, duration)]
    excluded.update(embargoed)
    retained = [row['sample_id'] for row in samples if row['sample_id'] not in excluded]
    partition = purged + embargoed + retained
    if not mark(4, len(partition) == len(seen) and set(partition) == seen, 'PARTITION_NOT_DISJOINT'):
        return result
    mark(5, True, 'PURGE_HASH_MISMATCH')
    result.update(purge_status='VALID', purged_sample_ids=purged, embargoed_sample_ids=embargoed,
                  retained_sample_ids=retained, purge_count=len(purged), embargo_count=len(embargoed))
    body = {'input': request, 'report': {k: v for k, v in result.items() if k != 'purge_report_hash'}}
    try:
        raw = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, UnicodeError):
        result.update(purge_status='INVALID', purged_sample_ids=None, embargoed_sample_ids=None,
                      retained_sample_ids=None, purge_count=None, embargo_count=None)
        mark(5, False, 'PURGE_HASH_MISMATCH')
        return result
    result['purge_report_hash'] = hashlib.sha256(raw).hexdigest()
    return result
