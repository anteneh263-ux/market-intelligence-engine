"""Supplied checkpoint/signature chain bindings; no external retention authority."""
from app.research.phase6_start_validation import _snapshot, _shape, _need, _Refused, verify_start_bindings
from app.research.phase6_checkpoint_validation import verify_checkpoint, exact
from app.research.phase6_anchor_validation import verify_anchors
from app.research.phase6_validation import canonical_hash as H
from app.paper.session import shadow_reducer_v1 as reducer


def _check(q):
    _shape(q, 'start checkpoints anchor')
    start, bundles, anchor = q['start'], q['checkpoints'], q['anchor']
    _need(type(bundles) is list and 1 <= len(bundles) <= 64)
    initial = verify_start_bindings(start)
    _need(initial['binding_status'] == 'VALID', 'START_INVALID')
    _shape(anchor, 'trust receipts head challenge now_utc_ms')
    _need(anchor['head'] is not None and anchor['challenge'] is not None, 'ANCHOR_INVALID')
    _need(type(anchor['receipts']) is list and len(anchor['receipts']) == len(bundles) + 2, 'SEQUENCE_INVALID')
    p, run = start['plan'], initial['run_id']
    subjects = [dict(subject_kind='RESERVATION', subject_hash=run),
                dict(subject_kind='GENESIS', subject_hash=initial['genesis_hash'])]
    for bundle in bundles:
        checked = verify_checkpoint(bundle)
        _need(checked['binding_status'] == 'VALID', 'CHECKPOINT_INVALID')
        subjects.append(dict(subject_kind='CHECKPOINT', subject_hash=checked['checkpoint_hash']))
    checked = verify_anchors(dict(**anchor, expected_trust_hash=p['anchor_trust_hash'],
                                  expected_run_id=run, expected_subjects=subjects))
    _need(checked['cryptographic_status'] == 'VALID' and checked['head_status'] == 'MATCHED', 'ANCHOR_INVALID')
    receipts = anchor['receipts']
    _need(exact(receipts[:2], start['anchor']['receipts']), 'START_PREFIX_MISMATCH')
    now = anchor['now_utc_ms']
    horizon = p['end_ms'] + p['max_lateness_ms'] + p['max_decision_delay_ms'] + p['max_quote_delay_ms']
    _need(now >= start['anchor']['now_utc_ms'] and anchor['head']['retention_until_ms'] > horizon, 'CLOCK_INVALID')
    previous_hash = initial['genesis_hash']
    previous_bundle = None
    config = {k:p[k] for k in reducer.CONFIG_DEC + reducer.CONFIG_INT}
    for index, bundle in enumerate(bundles, 1):
        m, k, event = bundle['manifest'], bundle['checkpoint'], bundle['event']
        previous_anchor, receipt = receipts[index], receipts[index + 1]
        _need(m['run_id'] == run and m['plan_hash'] == H(p)
              and m['params_hash'] == p['params_hash'] and m['strategy_code_hash'] == p['strategy_code_hash']
              and m['prior_checkpoint_hash'] == previous_hash
              and m['prior_anchor_hash'] == H(previous_anchor), 'IDENTITY_MISMATCH')
        _need(k['segment_seq'] == index, 'SEQUENCE_INVALID')
        _need(exact(bundle['state']['config'], config), 'STATE_INVALID')
        _need(p['start_ms'] <= m['persisted_utc_ms'] and previous_anchor['retained_utc_ms'] <= m['persisted_utc_ms']
              and event['utc_ms'] <= receipt['retained_utc_ms'] <= now, 'CLOCK_INVALID')
        if previous_bundle is not None:
            pm, pk, ps = previous_bundle['manifest'], previous_bundle['checkpoint'], previous_bundle['state']
            _need(not pk['stopped'] and ps['pending'] is None, 'STATE_INVALID')
            _need(all(m[key] == pm[key] for key in ('engine_version', 'engine_code_hash')), 'IDENTITY_MISMATCH')
            _need(k['last_receipt_seq'] > pk['last_receipt_seq'], 'SEQUENCE_INVALID')
            _need(bundle['state']['clock_session_id'] == ps['clock_session_id']
                  and event['monotonic_ns'] > previous_bundle['event']['monotonic_ns'], 'CLOCK_INVALID')
        previous_hash, previous_bundle = H(k), bundle
    return previous_hash, checked['last_anchor_hash'], len(bundles)


def verify_checkpoint_anchors(request):
    """Check supplied signatures and local bindings, never service contact or admission."""
    result = dict(schema_version='p6.checkpoint_anchor_validation.v1', binding_status='INVALID',
                  reason_code=None, checkpoint_hash=None, last_anchor_hash=None, checkpoint_count=0,
                  runtime_accepted=False, independent_retention_verified=False,
                  full_execution_verified=False, external_authority_verification='UPSTREAM_REQUIRED')
    try:
        checkpoint, anchor, count = _check(_snapshot(request))
    except _Refused as exc:
        result['reason_code'] = exc.reason
    except (ValueError, TypeError, KeyError, RuntimeError, IndexError, OverflowError, RecursionError):
        result['reason_code'] = 'SCHEMA_INVALID'
    else:
        result.update(binding_status='VALID', checkpoint_hash=checkpoint,
                      last_anchor_hash=anchor, checkpoint_count=count)
    return result
