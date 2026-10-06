"""Pure TIMING_ROWS check; caller must first bind contract and persisted trial."""
import hashlib
import json

__all__ = ['check_timing_rows']
_FIELDS = frozenset(('bar_open_ms', 'bar_available_ms', 'feature_observed_ms',
                     'signal_decision_ms', 'order_submitted_ms', 'fill_ms'))


def _fail(reason):
    return dict(check_id='TIMING_ROWS', status='FAIL', reason_code=reason, result=None)


def check_timing_rows(rows, contract_record, *, intended_dataset_zone):
    """Validate rows against already-bound contract and persisted zone.

    No trial lookup, contract self-hash verification, calendar access or writes.
    Deferred coverage is explicitly reported; it never implies bar membership.
    """
    if type(rows) is not list or not rows:
        return _fail('TIMING_ROW_INVALID')
    if any(type(r) is not dict or set(r) != _FIELDS or
           any(type(v) is not int for v in r.values()) for r in rows):
        return _fail('TIMING_ROW_INVALID')
    seen = set()
    previous = None
    for row in rows:
        opened = row['bar_open_ms']
        if opened in seen:
            return _fail('TIMING_ROW_DUPLICATE')
        if previous is not None and opened < previous:
            return _fail('TIMING_ROW_OUT_OF_ORDER')
        seen.add(opened)
        previous = opened
    try:
        if type(contract_record) is not dict or intended_dataset_zone not in ('TRAIN', 'VAL', 'HOLDOUT'):
            return _fail('TIMING_CONTRACT_MISMATCH')
        dataset = contract_record['dataset_identity']
        offsets = contract_record['derived_timing_offsets']
        zone = contract_record['boundaries'][intended_dataset_zone.lower()]
        mode = dataset['coverage_mode']
        if mode not in ('CONTINUOUS_24_7', 'SESSION_CALENDAR', 'SPARSE_DECLARED'):
            return _fail('TIMING_CONTRACT_MISMATCH')
        start, end = zone['start_ms'], zone['end_ms']
        if type(start) is not int or type(end) is not int or start >= end:
            return _fail('TIMING_CONTRACT_MISMATCH')
        names = ('bar_available_offset_ms', 'signal_decision_offset_ms',
                 'order_eligibility_offset_ms', 'nominal_earliest_fill_offset_ms')
        values = [offsets[k] for k in names]
        if (any(type(v) is not int for v in values[:3]) or
                (values[3] is not None and type(values[3]) is not int)):
            return _fail('TIMING_CONTRACT_MISMATCH')
        continuous = mode == 'CONTINUOUS_24_7'
        if continuous:
            first, duration, fill_min = dataset['first_open_ms'], dataset['timeframe_ms'], offsets['fill_bar_offset_lower_bound']
            if (any(type(v) is not int for v in [first, duration, fill_min, *values])
                    or duration <= 0 or fill_min < 1):
                return _fail('TIMING_CONTRACT_MISMATCH')
        for row in rows:
            opened, available, feature, decision, submitted, fill = (row[k] for k in
                ('bar_open_ms', 'bar_available_ms', 'feature_observed_ms', 'signal_decision_ms', 'order_submitted_ms', 'fill_ms'))
            valid = start <= opened < end and available <= feature <= decision <= submitted <= fill
            for timestamp, offset, equality in zip((available, decision, submitted, fill), values, (True, False, False, False)):
                if offset is not None:
                    valid = valid and (timestamp == opened + offset if equality else timestamp >= opened + offset)
            if continuous:
                valid = valid and (opened-first) % duration == 0 and (fill-first) % duration == 0
                valid = valid and (fill-first)//duration - (opened-first)//duration >= fill_min
            if not valid:
                return _fail('TIMING_CONTRACT_MISMATCH')
    except (KeyError, TypeError, AttributeError, ValueError):
        return _fail('TIMING_CONTRACT_MISMATCH')
    raw = json.dumps(rows, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    detail = dict(intended_dataset_zone=intended_dataset_zone, timing_rows_status='VALID',
        timing_rows_coverage_status='RUNTIME_ORDER_FULLY_VERIFIED' if continuous else 'BAR_MEMBERSHIP_DEFERRED_TO_COVERAGE_VALIDATOR',
        timing_rows_hash=hashlib.sha256(raw).hexdigest(), first_bar_open_ms=rows[0]['bar_open_ms'],
        last_bar_open_ms=rows[-1]['bar_open_ms'], row_count=len(rows))
    return dict(check_id='TIMING_ROWS', status='PASS', reason_code=None, result=detail)
