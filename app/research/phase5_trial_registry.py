"""WP5-0A2-R2 · P1A · trial registry initialization.

ROUND            : WP5-0A2-R2 (rebuild), package P1A, GREEN round
BASELINE COMMIT  : affd12544b564fa10a7c68cd95a373f27bee8250
GOVERNING DESIGN : WP5_0A2_R2_P1A_INITIALIZATION_CONTROL_MATRIX_R2_FOR_CHATGPT.md
                     sha256 9e27be17638b5f5932852860eca98190e99ca2355bf1dc4a0ed1b823f9be75d0
                   WP5_0A2_R2_P1A_INITIALIZATION_CONTROL_MATRIX_R3A_FOR_CHATGPT.md
                     sha256 9c15de1995821eabf578650acd85236242ee949806b4e0278f3ec3038676dbeb
GOLDEN SCHEMA    : PHASE5_WP5_0A2_BM2_GOLDEN_SCHEMA_R2.md
                     sha256 f5221f78cc8ebecb5d3bb27dee5db868ac8a54efd0b4298fea61df102f12322e
FROZEN TESTS     : tests/test_phase5_trial_registry.py
                     sha256 c5c562af4867cae7238997ed00375929fd14d28c1c15ac1da6f13ae9c17888b3

SCOPE.  Initialization only.  No validation mode, no ledger operation, no
crash-boundary handling.  Nothing here reads market, performance or holdout
data, and nothing here authorises execution.

OUTCOME VOCABULARY.  Exactly three observable outcomes, and no fourth:

  COMPLETED               returns ``registry_path`` unchanged
  INITIALIZATION_REFUSED  raises ``RegistryInitializationRefused``
  BUSY_TIMEOUT            raises the original ``sqlite3.OperationalError``
                          when ``sqlite_errorcode & 0xFF in (5, 6)``

``REGISTRY_BUSY`` and ``INTERNAL_STRUCTURAL_FAULT`` are emitted nowhere.  The
refusal message is NOT contractual and carries no reason code.

MODULE-LEVEL ``sqlite3`` AND ``os`` ARE PART OF THE CONTRACT.  The frozen tests
install a passive observation seam by rebinding these two module attributes, so
every database call and every anchor filesystem call must be routed through the
module-global names rather than through locally bound aliases.

IMPORTS.  Five permitted stdlib modules only.  No third-party dependency.
"""

import hashlib
import json
import os
import sqlite3

__all__ = [
    "RegistryInitializationRefused",
    "initialize_registry",
]


# ---------------------------------------------------------------------------
# Frozen literals
# ---------------------------------------------------------------------------

_REGISTRY_SCHEMA_VERSION = "wp5_0a2.registry.v1"
_ANCHOR_SCHEMA_VERSION = "wp5_0a2.anchor.v1"

_MUTEX_SENTINEL_KEY = "wp5_0a2.mutex.v1"
_MUTEX_SENTINEL_VALUE = "SENTINEL"

_INIT_STATE_KEY = "init_state"
_INIT_STATE_PENDING = "PENDING_ANCHOR_CREATE"
_INIT_STATE_COMPLETE = "COMPLETE"

_META_KEY_SCHEMA_VERSION = "registry_schema_version"
_META_KEY_IDENTITY = "registry_identity"

_IF_NOT_EXISTS_TOKEN = "IF NOT EXISTS "

# Reviewer ruling, WP5-0A2-R2 P1A GREEN authorisation.  Internal only: this
# value is deliberately NOT a parameter of the public initializer API.
_INITIALIZER_BUSY_TIMEOUT_MS = 1000

_SQLITE_BUSY_PRIMARY_CODES = (5, 6)

_MUTEX_SUFFIX = ".lock"
_ANCHOR_TEMP_SUFFIX = ".tmp"

_SCHEMA_QUERY = (
    "SELECT type, name, sql FROM sqlite_master"
    " WHERE type IN ('table','trigger') ORDER BY name"
)

_BUSY_TIMEOUT_STATEMENT = "PRAGMA busy_timeout = %d" % (_INITIALIZER_BUSY_TIMEOUT_MS,)

_INTEGRITY_CHECK_STATEMENT = "PRAGMA integrity_check"
_INTEGRITY_OK = "ok"


# ---------------------------------------------------------------------------
# Golden Schema R2 -- the eight frozen statements, in stored form
# ---------------------------------------------------------------------------
#
# Each statement is held here EXACTLY as it is executed, which is exactly the
# golden literal with the fourteen-character token ``IF NOT EXISTS `` inserted
# once after ``CREATE TABLE `` / ``CREATE TRIGGER ``.  Canonicalization removes
# that token once per statement and changes nothing else.

_DDL_REGISTRY_META = (
    "CREATE TABLE IF NOT EXISTS registry_meta ( meta_key TEXT PRIMARY KEY NOT NULL,"
    " meta_value TEXT NOT NULL)"
)
_DDL_REGISTRY_STATE = (
    "CREATE TABLE IF NOT EXISTS registry_state ( state_key TEXT PRIMARY KEY NOT NULL,"
    " state_value TEXT NOT NULL)"
)
_DDL_TRIAL_LEDGER = (
    "CREATE TABLE IF NOT EXISTS trial_ledger ( research_program_id TEXT NOT NULL,"
    " chain_index INTEGER NOT NULL, row_json TEXT NOT NULL,"
    " PRIMARY KEY (research_program_id, chain_index))"
)

_DDL_REGISTRY_META_NO_DELETE = (
    "CREATE TRIGGER IF NOT EXISTS registry_meta_no_delete BEFORE DELETE ON registry_meta"
    " BEGIN SELECT RAISE(ABORT, 'registry_meta is immutable'); END"
)
_DDL_REGISTRY_META_NO_UPDATE = (
    "CREATE TRIGGER IF NOT EXISTS registry_meta_no_update BEFORE UPDATE ON registry_meta"
    " BEGIN SELECT RAISE(ABORT, 'registry_meta is immutable'); END"
)
_DDL_TRIAL_LEDGER_NO_DELETE = (
    "CREATE TRIGGER IF NOT EXISTS trial_ledger_no_delete BEFORE DELETE ON trial_ledger"
    " BEGIN SELECT RAISE(ABORT, 'trial_ledger is append-only'); END"
)
_DDL_TRIAL_LEDGER_NO_UPDATE = (
    "CREATE TRIGGER IF NOT EXISTS trial_ledger_no_update BEFORE UPDATE ON trial_ledger"
    " BEGIN SELECT RAISE(ABORT, 'trial_ledger is append-only'); END"
)

_DDL_REGISTRY_MUTEX = (
    "CREATE TABLE IF NOT EXISTS registry_mutex ( sentinel_key TEXT PRIMARY KEY NOT NULL,"
    " sentinel_value TEXT NOT NULL)"
)

# Ordered.  The order is part of the deterministic initialization trace.
_REGISTRY_DDL = (
    _DDL_REGISTRY_META,
    _DDL_REGISTRY_STATE,
    _DDL_TRIAL_LEDGER,
    _DDL_REGISTRY_META_NO_DELETE,
    _DDL_REGISTRY_META_NO_UPDATE,
    _DDL_TRIAL_LEDGER_NO_DELETE,
    _DDL_TRIAL_LEDGER_NO_UPDATE,
)

_EXPECTED_REGISTRY_TABLES = ("registry_meta", "registry_state", "trial_ledger")
_EXPECTED_REGISTRY_TRIGGERS = (
    "registry_meta_no_delete",
    "registry_meta_no_update",
    "trial_ledger_no_delete",
    "trial_ledger_no_update",
)


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class RegistryInitializationRefused(RuntimeError):
    """A deterministic FAIL_CLOSED initializer outcome.

    This type represents a deterministic refusal and nothing else.  It is not a
    carrier for a reason code: the initializer has no reason-code vocabulary and
    the message is not contractual.  Real lock contention is NOT represented by
    this type -- it propagates as the original ``sqlite3.OperationalError``.
    """


# ---------------------------------------------------------------------------
# Canonicalization -- frozen
# ---------------------------------------------------------------------------

def _canonical_bytes(obj):
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _canonicalize_ddl(statement):
    """Remove the fourteen-character token once, and change nothing else."""
    return statement.replace(_IF_NOT_EXISTS_TOKEN, "", 1)


def _golden_canonical(statement):
    return _canonicalize_ddl(statement)


# ---------------------------------------------------------------------------
# Refusal and busy classification
# ---------------------------------------------------------------------------

def _is_busy(exc):
    """True only for genuine SQLite lock contention.

    Classified by the masked primary result code, never by string matching.
    """
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if code is None:
        return False
    return (code & 0xFF) in _SQLITE_BUSY_PRIMARY_CODES


def _refuse(detail):
    """Raise the sole deterministic refusal, with no cause attached.

    ``from None`` is what makes the "no underlying exception escapes" rule
    mechanically checkable: ``__cause__`` is None on every refusal.
    """
    raise RegistryInitializationRefused(detail) from None


# ---------------------------------------------------------------------------
# Identity and anchor document -- frozen derivations
# ---------------------------------------------------------------------------

def _registry_identity(registry_path, anchor_path):
    return _sha256_hex(_canonical_bytes({
        "registry_schema_version": _REGISTRY_SCHEMA_VERSION,
        "registry_path": os.path.abspath(registry_path),
        "anchor_path": os.path.abspath(anchor_path),
        "anchor_schema_version": _ANCHOR_SCHEMA_VERSION,
    }))


def _fresh_anchor_document(registry_identity):
    body = {
        "schema_version": _ANCHOR_SCHEMA_VERSION,
        "registry_identity": registry_identity,
        "document_sequence": 1,
        "programs": {},
    }
    document = dict(body)
    document["document_hash"] = _sha256_hex(_canonical_bytes(body))
    return document


def _fresh_anchor_bytes(registry_identity):
    return _canonical_bytes(_fresh_anchor_document(registry_identity))


def _mutex_path_for(anchor_path):
    return os.fspath(anchor_path) + _MUTEX_SUFFIX


# ---------------------------------------------------------------------------
# Durable anchor write -- M5-C7
# ---------------------------------------------------------------------------

def _write_anchor_durably(anchor_path, payload):
    """temp write -> file fsync -> os.replace -> DIRECTORY fsync.

    The directory fsync is not optional: without it the directory entry created
    by the rename is not durable, and a crash after ``COMPLETE`` could leave a
    registry claiming an anchor that does not exist.
    """
    target = os.fspath(anchor_path)
    temp = target + _ANCHOR_TEMP_SUFFIX
    directory = os.path.dirname(target) or "."

    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        written = 0
        while written < len(payload):
            written += os.write(handle, payload[written:])
        os.fsync(handle)
    finally:
        os.close(handle)

    os.replace(temp, target)

    directory_handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_handle)
    finally:
        os.close(directory_handle)


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

def _connect(path):
    """Autocommit connection.  Every transaction here is explicit.

    ``isolation_level=None`` is required: an implicit BEGIN would collide with
    the explicit BEGIN EXCLUSIVE / BEGIN IMMEDIATE the contract mandates.
    """
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute(_BUSY_TIMEOUT_STATEMENT)
    return connection


def _declared_objects(connection):
    declared = {}
    for kind, name, statement in connection.execute(_SCHEMA_QUERY).fetchall():
        if name.startswith("sqlite_"):
            continue
        declared[name] = (kind, statement)
    return declared


# ---------------------------------------------------------------------------
# Mutex -- created and validated BEFORE the registry (M5-C1)
# ---------------------------------------------------------------------------

def _open_and_hold_mutex(mutex_path):
    """Acquire the initialization mutex, bootstrapping or validating it.

    BEGIN EXCLUSIVE is taken FIRST, before any inspection, so that a contender
    can never observe a half-built mutex.  A contender blocked here receives the
    original busy ``sqlite3.OperationalError`` after the internal timeout.
    """
    connection = _connect(mutex_path)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        declared = _declared_objects(connection)

        if not declared:
            # S1 / S2: absent or zero-length.  A zero-length file carries no
            # state, so recreating it loses nothing.
            connection.execute(_DDL_REGISTRY_MUTEX)
            connection.execute(
                "INSERT INTO registry_mutex(sentinel_key, sentinel_value)"
                " VALUES('%s', '%s')" % (_MUTEX_SENTINEL_KEY, _MUTEX_SENTINEL_VALUE)
            )
            return connection

        # A mutex that already exists is validated, never repaired.
        if sorted(declared) != ["registry_mutex"]:
            _refuse("mutex carries unexpected objects")
        kind, statement = declared["registry_mutex"]
        if kind != "table":
            _refuse("registry_mutex is not a table")
        if statement is None or \
                _canonicalize_ddl(statement) != _golden_canonical(_DDL_REGISTRY_MUTEX):
            _refuse("mutex schema diverges from the golden definition")

        rows = connection.execute(
            "SELECT sentinel_key, sentinel_value FROM registry_mutex"
        ).fetchall()
        if [tuple(row) for row in rows] != [
            (_MUTEX_SENTINEL_KEY, _MUTEX_SENTINEL_VALUE)
        ]:
            _refuse("mutex sentinel is absent or divergent")
        return connection
    except BaseException:
        _close_quietly(connection)
        raise


def _close_quietly(connection):
    try:
        connection.close()
    except BaseException:
        pass


# ---------------------------------------------------------------------------
# Registry state classification
# ---------------------------------------------------------------------------

def _assert_registry_integrity(connection):
    """R2 section 3.1.  Structural integrity, before anything is believed.

    Schema equality is not a substitute for this.  A registry can carry a
    byte-perfect sqlite_master and still be structurally corrupt -- a lost page,
    a broken index, a truncated freelist -- and every schema, metadata, state and
    ledger read below would then be reading from a damaged file.  So this runs
    FIRST, and nothing is repaired: the only permitted result is exactly one row
    reading "ok", and any other result is a deterministic FAIL_CLOSED outcome.

    A genuine busy OperationalError raised here is NOT caught, and propagates
    unchanged through the caller's classifier.
    """
    rows = connection.execute(_INTEGRITY_CHECK_STATEMENT).fetchall()
    if [tuple(row) for row in rows] != [(_INTEGRITY_OK,)]:
        _refuse("registry failed PRAGMA integrity_check")


def _registry_state_value(connection):
    rows = connection.execute(
        "SELECT state_key, state_value FROM registry_state"
    ).fetchall()
    state = dict(rows)
    if sorted(state) != [_INIT_STATE_KEY]:
        _refuse("registry_state must hold exactly one init_state row")
    return state[_INIT_STATE_KEY]


def _validate_registry_schema(connection):
    declared = _declared_objects(connection)
    tables = sorted(n for n, (k, _s) in declared.items() if k == "table")
    triggers = sorted(n for n, (k, _s) in declared.items() if k == "trigger")
    if tables != sorted(_EXPECTED_REGISTRY_TABLES):
        _refuse("registry tables diverge from the golden definition")
    if triggers != sorted(_EXPECTED_REGISTRY_TRIGGERS):
        _refuse("registry triggers diverge from the golden definition")
    for statement in _REGISTRY_DDL:
        name = _statement_object_name(statement)
        stored = declared[name][1]
        if stored is None or \
                _canonicalize_ddl(stored) != _golden_canonical(statement):
            _refuse("registry object %s diverges from the golden definition" % (name,))


def _statement_object_name(statement):
    for head in ("CREATE TABLE " + _IF_NOT_EXISTS_TOKEN,
                 "CREATE TRIGGER " + _IF_NOT_EXISTS_TOKEN):
        if statement.startswith(head):
            return statement[len(head):].split(" ", 1)[0]
    _refuse("unrecognised golden statement head")


def _ledger_is_empty(connection):
    rows = connection.execute("SELECT COUNT(*) FROM trial_ledger").fetchall()
    return int(rows[0][0]) == 0


def _stored_identity(connection):
    rows = connection.execute(
        "SELECT meta_key, meta_value FROM registry_meta"
    ).fetchall()
    meta = dict(rows)
    if sorted(meta) != sorted([_META_KEY_SCHEMA_VERSION, _META_KEY_IDENTITY]):
        _refuse("registry_meta must hold exactly two rows")
    if meta[_META_KEY_SCHEMA_VERSION] != _REGISTRY_SCHEMA_VERSION:
        _refuse("registry_schema_version diverges")
    return meta[_META_KEY_IDENTITY]


# ---------------------------------------------------------------------------
# Anchor inspection
# ---------------------------------------------------------------------------

def _anchor_matches(anchor_path, expected_bytes):
    try:
        with open(anchor_path, "rb") as handle:
            return handle.read() == expected_bytes
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Initialization steps
# ---------------------------------------------------------------------------

def _build_registry(connection, identity):
    """I3: the complete schema and both metadata rows in ONE transaction.

    The PENDING marker is written inside the same transaction, so a crash before
    COMMIT leaves nothing at all rather than a half-built registry.
    """
    connection.execute("BEGIN EXCLUSIVE")
    for statement in _REGISTRY_DDL:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO registry_meta(meta_key, meta_value)"
        " VALUES('%s', '%s')" % (_META_KEY_SCHEMA_VERSION, _REGISTRY_SCHEMA_VERSION)
    )
    # The identity is BOUND, never interpolated.  It is the one initialization
    # value derived from the caller's paths, so interpolating it would make the
    # observed trace path-dependent and t36's determinism claim unfalsifiable.
    connection.execute(
        "INSERT INTO registry_meta(meta_key, meta_value) VALUES('%s', ?)"
        % (_META_KEY_IDENTITY,),
        (identity,),
    )
    connection.execute(
        "INSERT INTO registry_state(state_key, state_value)"
        " VALUES('%s', '%s')" % (_INIT_STATE_KEY, _INIT_STATE_PENDING)
    )
    connection.execute("COMMIT")


def _mark_complete(connection):
    """I5: the single one-way transition, after the anchor is durable."""
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "UPDATE registry_state SET state_value = '%s' WHERE state_key = '%s'"
        % (_INIT_STATE_COMPLETE, _INIT_STATE_KEY)
    )
    connection.execute("COMMIT")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def initialize_registry(registry_path, anchor_path):
    """Create, recover or confirm the trial registry.  Both arguments mandatory.

    ``anchor_path`` is never derived from ``registry_path``.  The mutex is
    located at ``os.fspath(anchor_path) + ".lock"`` and nowhere else.

    Returns ``registry_path`` unchanged on COMPLETED.
    Raises ``RegistryInitializationRefused`` on a deterministic refusal.
    Propagates the original busy ``sqlite3.OperationalError`` on contention.
    """
    try:
        return _initialize(registry_path, anchor_path)
    except RegistryInitializationRefused:
        raise
    except BaseException as exc:
        if _is_busy(exc):
            # Contention is not a refusal.  Propagate unchanged, untranslated.
            raise
        _refuse("initialization refused: %s" % (type(exc).__name__,))


def _initialize(registry_path, anchor_path):
    if registry_path is None or anchor_path is None:
        _refuse("registry_path and anchor_path are both mandatory")

    mutex_path = _mutex_path_for(anchor_path)
    registry_target = os.fspath(registry_path)
    anchor_target = os.fspath(anchor_path)

    for path in (registry_target, anchor_target, mutex_path):
        parent = os.path.dirname(path) or "."
        if not os.path.isdir(parent):
            _refuse("parent of %s is not an existing directory"
                    % (os.path.basename(path),))

    mutex = _open_and_hold_mutex(mutex_path)
    try:
        result = _initialize_under_mutex(
            registry_path, registry_target, anchor_target
        )
        mutex.execute("COMMIT")
        return result
    finally:
        _close_quietly(mutex)


def _initialize_under_mutex(registry_path, registry_target, anchor_target):
    identity = _registry_identity(registry_target, anchor_target)
    anchor_exists = os.path.exists(anchor_target)

    registry_exists = os.path.exists(registry_target)
    if registry_exists and not os.path.isfile(registry_target):
        _refuse("registry path is not a regular file")
    registry_size = os.path.getsize(registry_target) if registry_exists else 0

    # ---- S1 / S8 / S8b: nothing, or a zero-length registry ----------------
    if (not registry_exists) or registry_size == 0:
        if anchor_exists:
            # S8b.  An anchor beside an empty registry means the registry was
            # lost, not that it was never built.  Rebuilding would silently
            # reset every high-water value the anchor still asserts.
            _refuse("zero-length registry beside a surviving anchor")
        return _fresh_initialization(registry_path, registry_target,
                                     anchor_target, identity)

    # ---- an existing registry: classify, then recover or confirm ----------
    registry = _connect(registry_target)
    try:
        # Structural integrity FIRST.  Nothing below may rely on schema,
        # metadata, state or ledger contents until this has passed.
        _assert_registry_integrity(registry)
        _validate_registry_schema(registry)
        stored = _stored_identity(registry)
        if stored != identity:
            _refuse("stored registry_identity does not match this path pair")

        state = _registry_state_value(registry)

        if state == _INIT_STATE_COMPLETE:
            if not anchor_exists:
                # S12.  Recreating the anchor would reset every high-water
                # value it carried.  Fail closed; recovery is an operator act.
                _refuse("COMPLETE registry with the anchor absent")
            if not _anchor_matches(anchor_target, _fresh_anchor_bytes(identity)):
                _refuse("anchor content diverges from the sealed document")
            # S11: a STRICT no-op.  No anchor rewrite, no state write, no
            # temporary file, no second identity.
            return registry_path

        if state == _INIT_STATE_PENDING:
            if not _ledger_is_empty(registry):
                # S13.  A PENDING registry with ledger rows is not a partial
                # initialization; it is a corrupted one.
                _refuse("PENDING registry with a non-empty trial_ledger")
            expected = _fresh_anchor_bytes(identity)
            if anchor_exists:
                # S10: the anchor survived.  Validate it, then re-execute I4
                # anyway.  Byte equality proves the CONTENT is right; it does
                # not prove the directory entry was durable before the crash,
                # and a crash between os.replace and the directory fsync leaves
                # exactly that state.  The rewrite is byte-identical.
                if not _anchor_matches(anchor_target, expected):
                    _refuse("PENDING registry with a divergent anchor")
            else:
                # S9: the crash landed between I3 and I4.
                pass
            # I4, re-executed for both S9 and S10.  Deterministic recovery, no
            # contention involved.  S11 does NOT reach here and stays a no-op.
            _write_anchor_durably(anchor_target, expected)
            _mark_complete(registry)
            return registry_path

        _refuse("unrecognised init_state")
    finally:
        _close_quietly(registry)


def _fresh_initialization(registry_path, registry_target, anchor_target, identity):
    registry = _connect(registry_target)
    try:
        _build_registry(registry, identity)
        _write_anchor_durably(anchor_target, _fresh_anchor_bytes(identity))
        _mark_complete(registry)
        return registry_path
    finally:
        _close_quietly(registry)
