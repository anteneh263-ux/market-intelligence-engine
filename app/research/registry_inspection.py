"""Closed read-only structural inspection, not ledger verification or recovery."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from urllib.parse import quote

__all__ = ['inspect_registry']

# Golden Schema R2 exact stored DDL; never derived from the inspected database.
_TABLES = {
    'registry_meta': 'CREATE TABLE registry_meta ( meta_key TEXT PRIMARY KEY NOT NULL, meta_value TEXT NOT NULL)',
    'registry_state': 'CREATE TABLE registry_state ( state_key TEXT PRIMARY KEY NOT NULL, state_value TEXT NOT NULL)',
    'trial_ledger': 'CREATE TABLE trial_ledger ( research_program_id TEXT NOT NULL, chain_index INTEGER NOT NULL, row_json TEXT NOT NULL, PRIMARY KEY (research_program_id, chain_index))',
}
_TRIGGERS = {
    f'{table}_no_{event.lower()}': f"CREATE TRIGGER {table}_no_{event.lower()} BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT, '{message}'); END"
    for table, message in (('registry_meta', 'registry_meta is immutable'), ('trial_ledger', 'trial_ledger is append-only'))
    for event in ('DELETE', 'UPDATE')
}
_MUTEX_DDL = 'CREATE TABLE registry_mutex ( sentinel_key TEXT PRIMARY KEY NOT NULL, sentinel_value TEXT NOT NULL)'
_QUERY = "SELECT type, name, sql FROM sqlite_master WHERE type IN ('table','trigger') ORDER BY name"
_SENTINEL = [('wp5_0a2.mutex.v1', 'SENTINEL')]
_RESERVATION_KEYS = frozenset(('research_program_id', 'trial_id', 'attempt_fingerprint',
    'reservation_sequence', 'reservation_utc_ms', 'intended_dataset_zone',
    'ordered_input_hashes_hash', 'method_hash', 'previous_row_hash', 'row_hash'))
# The frozen reservation has eleven fields, including experiment_id.
_RESERVATION_KEYS = _RESERVATION_KEYS | {'experiment_id'}
_OUTCOME_KEYS = frozenset(('research_program_id', 'trial_id', 'outcome_sequence',
    'outcome_utc_ms', 'trial_outcome', 'result_hash', 'previous_row_hash', 'row_type', 'row_hash'))


class _Refused(Exception):
    pass


def _require(condition, reason):
    if not condition:
        raise _Refused(reason)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _hex(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _integer(value, minimum):
    return type(value) is int and value >= minimum


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError('nonfinite JSON')


def _json(raw):
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)


def _path(value):
    _require(type(value) is str or isinstance(value, Path), 'REGISTRY_CORRUPT')
    path = os.fspath(value)
    _require(os.path.isabs(path) and os.path.normpath(path) == path, 'REGISTRY_CORRUPT')
    _require(not any(re.search(r'(?:^|[^a-z0-9])h[-_ ]?0?25(?:[^0-9]|$)', p, re.I)
                     for p in Path(path).parts), 'REGISTRY_CORRUPT')
    return path


def _regular(path, reason):
    try:
        for parent in reversed(Path(path).parents):
            _require(stat.S_ISDIR(os.lstat(parent).st_mode), reason)
        value = os.lstat(path)
        _require(stat.S_ISREG(value.st_mode) and value.st_size > 0, reason)
        return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    except OSError:
        raise _Refused(reason) from None


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        _require(stat.S_ISREG(os.fstat(handle.fileno()).st_mode), 'REGISTRY_CORRUPT')
        return handle.read()


def _db_preflight(path):
    for suffix in ('-journal', '-wal', '-shm'):
        _require(not os.path.lexists(path + suffix), 'REGISTRY_CORRUPT')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        header = handle.read(20)
    # Avoid SQLite sidecar creation/recovery on WAL databases even without sidecars.
    _require(not (len(header) >= 20 and (header[18] == 2 or header[19] == 2)), 'REGISTRY_CORRUPT')


def _connect(path, mode):
    return sqlite3.connect('file:' + quote(path, safe='/') + '?mode=' + mode,
                           uri=True, isolation_level=None, timeout=0)


def _objects(connection):
    return {(kind, name): (sql.replace('IF NOT EXISTS ', '', 1) if sql else sql)
            for kind, name, sql in connection.execute(_QUERY).fetchall()
            if not name.startswith('sqlite_')}


def _anchor(raw, identity):
    reason = 'REGISTRY_SCHEMA_UNSUPPORTED'
    try:
        document = _json(raw)
        _require(type(document) is dict and set(document) == {
            'schema_version', 'registry_identity', 'document_sequence', 'programs', 'document_hash'}, reason)
        _require(document['schema_version'] == 'wp5_0a2.anchor.v1'
                 and document['registry_identity'] == identity
                 and _integer(document['document_sequence'], 1)
                 and type(document['programs']) is dict, reason)
        _require(_hex(document['document_hash']) and document['document_hash'] == _hash(
            {k: v for k, v in document.items() if k != 'document_hash'}), reason)
        for program, entry in document['programs'].items():
            _require(type(program) is str and bool(program) and type(entry) is dict and set(entry) == {
                'reservation_high_water', 'chain_high_water', 'head_row_hash', 'binding_hash'}, reason)
            _require(_integer(entry['reservation_high_water'], 1) and _integer(entry['chain_high_water'], 1)
                     and _hex(entry['head_row_hash']) and _hex(entry['binding_hash']), reason)
            body = dict(research_program_id=program, registry_identity=identity,
                        **{k: v for k, v in entry.items() if k != 'binding_hash'})
            _require(entry['binding_hash'] == _hash(body), reason)
        _require(raw == _canonical(document), reason)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise _Refused(reason) from None


def _rows(connection):
    reason = 'REGISTRY_CORRUPT'
    for program, index, raw in connection.execute('SELECT research_program_id, chain_index, row_json FROM trial_ledger'):
        _require(type(program) is str and bool(program) and _integer(index, 0), reason)
        try:
            row = _json(raw)
        except (ValueError, TypeError, RecursionError):
            raise _Refused(reason) from None
        _require(type(row) is dict and row.get('research_program_id') == program, reason)
        if set(row) == _RESERVATION_KEYS:
            _require(all(_hex(row[k]) for k in ('research_program_id', 'experiment_id', 'trial_id',
                'attempt_fingerprint', 'ordered_input_hashes_hash', 'method_hash', 'previous_row_hash', 'row_hash')),
                reason)
            _require(_integer(row['reservation_sequence'], 1) and _integer(row['reservation_utc_ms'], 0)
                     and row['intended_dataset_zone'] in ('TRAIN', 'VAL', 'HOLDOUT'), reason)
        elif set(row) == _OUTCOME_KEYS:
            _require(all(_hex(row[k]) for k in ('research_program_id', 'trial_id', 'result_hash',
                                              'previous_row_hash', 'row_hash')), reason)
            _require(_integer(row['outcome_sequence'], 1) and _integer(row['outcome_utc_ms'], 0)
                     and row['row_type'] == 'TRIAL_OUTCOME'
                     and row['trial_outcome'] in ('COMPLETED_VALID', 'COMPLETED_INVALID', 'CRASHED'), reason)
        else:
            raise _Refused(reason)


def _result(status, reason=None, identity=None, pending=None):
    return dict(inspection_status=status, reason_code=reason, registry_identity=identity,
                pending_detected=pending, ledger_verification='NOT_PERFORMED')


def _inspect_registry(registry_path, anchor_path, ledger_check=None):
    """Inspect a quiescent synthetic/authorized existing store; never recover it."""
    mutex = registry = None
    try:
        r, a = _path(registry_path), _path(anchor_path)
        m = a + '.lock'
        r_before = _regular(r, 'REGISTRY_CORRUPT')                         # O1
        _db_preflight(r)
        m_before = _regular(m, 'REGISTRY_CORRUPT')
        _require(r_before[:2] != m_before[:2], 'REGISTRY_CORRUPT')
        _db_preflight(m)
        mutex = _connect(m, 'rw')                                        # O2
        mutex.execute('PRAGMA query_only=ON')
        _require(_objects(mutex) == {('table', 'registry_mutex'): _MUTEX_DDL}, 'REGISTRY_CORRUPT')
        _require(mutex.execute('SELECT sentinel_key, sentinel_value FROM registry_mutex').fetchall()
                 == _SENTINEL, 'REGISTRY_CORRUPT')
        try:
            mutex.execute('BEGIN DEFERRED')                              # O3
            _require(mutex.execute('SELECT sentinel_key, sentinel_value FROM registry_mutex').fetchall()
                     == _SENTINEL, 'REGISTRY_CORRUPT')
        except sqlite3.OperationalError as exc:
            if getattr(exc, 'sqlite_errorcode', 0) & 0xff in (5, 6):
                raise _Refused('RESERVATION_RACE') from None
            raise
        registry = _connect(r, 'ro')                                     # O4
        registry.execute('PRAGMA query_only=ON')
        registry.execute('BEGIN DEFERRED')
        _require(registry.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'REGISTRY_CORRUPT')  # O5
        expected = {('table', k): v for k, v in _TABLES.items()}
        expected.update({('trigger', k): v for k, v in _TRIGGERS.items()})
        _require(_objects(registry) == expected, 'REGISTRY_SCHEMA_UNSUPPORTED')  # O6
        meta = dict(registry.execute('SELECT meta_key, meta_value FROM registry_meta').fetchall())
        keys = set(meta)
        _require(keys in ({'registry_schema_version', 'registry_identity'},
                         {'registry_schema_version', 'registry_identity', 'pending_anchor_update'})
                 and meta.get('registry_schema_version') == 'wp5_0a2.registry.v1', 'REGISTRY_SCHEMA_UNSUPPORTED')  # O7
        identity = _hash(dict(registry_schema_version='wp5_0a2.registry.v1', registry_path=r,
                              anchor_path=a, anchor_schema_version='wp5_0a2.anchor.v1'))
        _require(meta['registry_identity'] == identity, 'REGISTRY_SCHEMA_UNSUPPORTED')  # O8
        _require(registry.execute('SELECT state_key, state_value FROM registry_state').fetchall()
                 == [('init_state', 'COMPLETE')], 'REGISTRY_SCHEMA_UNSUPPORTED')  # O9
        a_before = _regular(a, 'REGISTRY_SCHEMA_UNSUPPORTED')
        _require(a_before[:2] not in (r_before[:2], m_before[:2]), 'REGISTRY_SCHEMA_UNSUPPORTED')
        try:
            raw = _read(a)
        except OSError:
            raise _Refused('REGISTRY_SCHEMA_UNSUPPORTED') from None
        _anchor(raw, identity)                                           # O10
        _rows(registry)
        detail = ledger_check(registry, _json(raw), meta) if ledger_check else None
        _require(_regular(r, 'REGISTRY_CORRUPT') == r_before and
                 _regular(m, 'REGISTRY_CORRUPT') == m_before and
                 _regular(a, 'REGISTRY_SCHEMA_UNSUPPORTED') == a_before, 'REGISTRY_CORRUPT')
        pending = 'pending_anchor_update' in meta
        if detail is not None:
            return detail
        return _result('RECOVERY_REQUIRED' if pending else 'STRUCTURE_VERIFIED', identity=identity, pending=pending)
    except _Refused as exc:
        return _result('REFUSED', str(exc))
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError, RecursionError):
        return _result('REFUSED', 'REGISTRY_CORRUPT')
    finally:
        if registry is not None:
            registry.close()
        if mutex is not None:
            mutex.close()


def inspect_registry(registry_path, anchor_path):
    """Closed structural inspection; ledger verification is not performed."""
    return _inspect_registry(registry_path, anchor_path)
