"""Verify mixed ledger chains and anchor counters within one locked snapshot."""
import sqlite3

from .registry_inspection import _hash, _hex, _json, _inspect_registry, _Refused, _require

__all__ = ['verify_registry_ledger']
_GENESIS = '0' * 64


def _empty():
    return dict(N_total=0, outcome_count=0, chain_high_water=0,
                ledger_chain_hash=_GENESIS, last_trial_id=None)


def _verify(connection, anchor, meta, program):
    states = {}
    trials = {}
    ended = set()
    for owner, index, raw in connection.execute(
            'SELECT research_program_id, chain_index, row_json FROM trial_ledger '
            'ORDER BY research_program_id, chain_index'):
        row = _json(raw)
        state = states.setdefault(owner, _empty())
        _require(index == state['chain_high_water'], 'LEDGER_CHAIN_BROKEN')
        _require(row['previous_row_hash'] == state['ledger_chain_hash'] and
                 row['row_hash'] == _hash({k: v for k, v in row.items() if k != 'row_hash'}),
                 'LEDGER_CHAIN_BROKEN')
        key = (owner, row['trial_id'])
        if 'reservation_sequence' in row:
            _require(row['reservation_sequence'] == state['N_total'] + 1, 'COUNTER_NONMONOTONIC')
            identity = {k: row[k] for k in ('research_program_id', 'experiment_id',
                        'attempt_fingerprint', 'reservation_sequence', 'reservation_utc_ms')}
            _require(row['trial_id'] == _hash(identity) and key not in trials, 'LEDGER_CHAIN_BROKEN')
            trials[key] = row
            state['N_total'] += 1
            state['last_trial_id'] = row['trial_id']
        else:
            _require(row['outcome_sequence'] == state['outcome_count'] + 1, 'COUNTER_NONMONOTONIC')
            _require(key in trials and key not in ended, 'LEDGER_CHAIN_BROKEN')
            ended.add(key)
            state['outcome_count'] += 1
        state['chain_high_water'] += 1
        state['ledger_chain_hash'] = row['row_hash']
    # Establish chain integrity before classifying pending/anchor state.
    _require('pending_anchor_update' not in meta, 'COUNTER_NONMONOTONIC')
    _require(set(states) == set(anchor['programs']), 'COUNTER_NONMONOTONIC')
    for owner, state in states.items():
        entry = anchor['programs'][owner]
        _require(entry['reservation_high_water'] == state['N_total'] and
                 entry['chain_high_water'] == state['chain_high_water'] and
                 entry['head_row_hash'] == state['ledger_chain_hash'], 'COUNTER_NONMONOTONIC')
    return dict(verification_status='VERIFIED', reason_code=None,
                registry_identity=anchor['registry_identity'], **states.get(program, _empty()))


def _checked_verify(connection, anchor, meta, program):
    try:
        return _verify(connection, anchor, meta, program)
    except sqlite3.Error as exc:
        busy = getattr(exc, 'sqlite_errorcode', 0) & 0xff in (5, 6)
        raise _Refused('REGISTRY_BUSY' if busy else 'INTERNAL_STRUCTURAL_FAULT') from None


def verify_registry_ledger(registry_path, anchor_path, *, research_program_id):
    """Read-only verification of all programs; expose only the requested counts.

    This is a closed helper, not a reservation or the public ten-key envelope.
    A failed check never publishes authoritative counters or a chain head.
    """
    if not _hex(research_program_id):
        reason = 'WRONG_RESEARCH_PROGRAM_ID'
    else:
        result = _inspect_registry(registry_path, anchor_path,
            lambda connection, anchor, meta: _checked_verify(connection, anchor, meta, research_program_id))
        if result.get('verification_status') == 'VERIFIED':
            return result
        reason = result['reason_code']
    return dict(verification_status='REFUSED', reason_code=reason,
                registry_identity=None, N_total=None, outcome_count=None,
                chain_high_water=None, ledger_chain_hash=None, last_trial_id=None)
