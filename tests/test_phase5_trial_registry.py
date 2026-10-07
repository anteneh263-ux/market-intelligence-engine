"""WP5-0A2-R2 · P1A · frozen initialization tests.

ROUND            : WP5-0A2-R2 (rebuild), package P1A, RED round
BASELINE COMMIT  : affd12544b564fa10a7c68cd95a373f27bee8250
GOVERNING DESIGN : WP5_0A2_R2_P1A_INITIALIZATION_CONTROL_MATRIX_R2_FOR_CHATGPT.md
                     sha256 9e27be17638b5f5932852860eca98190e99ca2355bf1dc4a0ed1b823f9be75d0
                   WP5_0A2_R2_P1A_INITIALIZATION_CONTROL_MATRIX_R3A_FOR_CHATGPT.md
                     sha256 9c15de1995821eabf578650acd85236242ee949806b4e0278f3ec3038676dbeb
GOLDEN SCHEMA    : PHASE5_WP5_0A2_BM2_GOLDEN_SCHEMA_R2.md
                     sha256 f5221f78cc8ebecb5d3bb27dee5db868ac8a54efd0b4298fea61df102f12322e

CONTINUITY WITH THE LOST BM1/BM2 TEST MODULES IS NOT CLAIMED.  Nothing in this
file is derived from them; they do not exist.

INDEPENDENCE RULE.  Every expected literal below is restated here.  This module
never imports a production constant to build an expected value.  A shared
constant cannot detect drift.  The single value that cannot be a literal --
``registry_identity`` -- is recomputed here from the frozen formula using this
test's own inputs.

SCOPE.  Initialization only.  No validation mode, no ledger operation, no
crash-boundary test.  Crash boundaries are derived in P1B from the measured
trace and independently approved before any such test is written.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading

import pytest

# ---------------------------------------------------------------------------
# Frozen literals -- restated, never imported
# ---------------------------------------------------------------------------

REGISTRY_SCHEMA_VERSION = "wp5_0a2.registry.v1"
ANCHOR_SCHEMA_VERSION = "wp5_0a2.anchor.v1"

MUTEX_SENTINEL_KEY = "wp5_0a2.mutex.v1"
MUTEX_SENTINEL_VALUE = "SENTINEL"

INIT_STATE_KEY = "init_state"
INIT_STATE_PENDING = "PENDING_ANCHOR_CREATE"
INIT_STATE_COMPLETE = "COMPLETE"

META_KEY_SCHEMA_VERSION = "registry_schema_version"
META_KEY_IDENTITY = "registry_identity"

IF_NOT_EXISTS_TOKEN = "IF NOT EXISTS "

SCHEMA_QUERY = (
    "SELECT type, name, sql FROM sqlite_master"
    " WHERE type IN ('table','trigger') ORDER BY name"
)

GOLDEN_REGISTRY_TABLES = {
    "registry_meta":
        "CREATE TABLE registry_meta ( meta_key TEXT PRIMARY KEY NOT NULL,"
        " meta_value TEXT NOT NULL)",
    "registry_state":
        "CREATE TABLE registry_state ( state_key TEXT PRIMARY KEY NOT NULL,"
        " state_value TEXT NOT NULL)",
    "trial_ledger":
        "CREATE TABLE trial_ledger ( research_program_id TEXT NOT NULL,"
        " chain_index INTEGER NOT NULL, row_json TEXT NOT NULL,"
        " PRIMARY KEY (research_program_id, chain_index))",
}

GOLDEN_REGISTRY_TRIGGERS = {
    "registry_meta_no_delete":
        "CREATE TRIGGER registry_meta_no_delete BEFORE DELETE ON registry_meta"
        " BEGIN SELECT RAISE(ABORT, 'registry_meta is immutable'); END",
    "registry_meta_no_update":
        "CREATE TRIGGER registry_meta_no_update BEFORE UPDATE ON registry_meta"
        " BEGIN SELECT RAISE(ABORT, 'registry_meta is immutable'); END",
    "trial_ledger_no_delete":
        "CREATE TRIGGER trial_ledger_no_delete BEFORE DELETE ON trial_ledger"
        " BEGIN SELECT RAISE(ABORT, 'trial_ledger is append-only'); END",
    "trial_ledger_no_update":
        "CREATE TRIGGER trial_ledger_no_update BEFORE UPDATE ON trial_ledger"
        " BEGIN SELECT RAISE(ABORT, 'trial_ledger is append-only'); END",
}

GOLDEN_MUTEX_TABLES = {
    "registry_mutex":
        "CREATE TABLE registry_mutex ( sentinel_key TEXT PRIMARY KEY NOT NULL,"
        " sentinel_value TEXT NOT NULL)",
}

# The eight frozen statements, in the pre-storage form the production constants
# are expected to hold: identical to the golden literal with the token inserted
# after CREATE TABLE / CREATE TRIGGER.
def _with_token(statement):
    for head in ("CREATE TABLE ", "CREATE TRIGGER "):
        if statement.startswith(head):
            return head + IF_NOT_EXISTS_TOKEN + statement[len(head):]
    raise AssertionError("unrecognised statement head: %r" % (statement[:40],))


GOLDEN_EIGHT = dict(GOLDEN_REGISTRY_TABLES)
GOLDEN_EIGHT.update(GOLDEN_REGISTRY_TRIGGERS)
GOLDEN_EIGHT.update(GOLDEN_MUTEX_TABLES)

PROHIBITED_CODES = ("REGISTRY_BUSY", "INTERNAL_STRUCTURAL_FAULT")

SQLITE_BUSY_PRIMARY_CODES = (5, 6)

# Bound on the contender subprocess in t33/t34.  See the disclosure in
# WP5_0A2_R2_P1A_RED_REVIEW_FOR_CHATGPT.md: the frozen initializer signature
# carries no busy_timeout parameter, so the module's internal default governs.
# If the contender has not returned within this bound the test FAILS rather
# than hangs, and the failure is reported as a bounded-wait breach, never
# silently absorbed.
CONTENDER_DEADLINE_S = 60.0


# ---------------------------------------------------------------------------
# Frozen canonicalizer -- restated
# ---------------------------------------------------------------------------

def canonical_bytes(obj):
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def canonicalize_ddl(statement):
    """Remove the fourteen-character token once, and change nothing else."""
    return statement.replace(IF_NOT_EXISTS_TOKEN, "", 1)


def expected_registry_identity(registry_path, anchor_path):
    """The frozen derivation, recomputed independently from this test's inputs."""
    return sha256_hex(canonical_bytes({
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        "registry_path": os.path.abspath(registry_path),
        "anchor_path": os.path.abspath(anchor_path),
        "anchor_schema_version": ANCHOR_SCHEMA_VERSION,
    }))


def expected_fresh_anchor(registry_identity):
    body = {
        "schema_version": ANCHOR_SCHEMA_VERSION,
        "registry_identity": registry_identity,
        "document_sequence": 1,
        "programs": {},
    }
    doc = dict(body)
    doc["document_hash"] = sha256_hex(canonical_bytes(body))
    return doc


def mutex_path_for(anchor_path):
    return os.fspath(anchor_path) + ".lock"


# ---------------------------------------------------------------------------
# Production surface accessor
# ---------------------------------------------------------------------------

PRODUCTION_MODULE = "app.research.phase5_trial_registry"


def production():
    """Import the production module, or fail this test with a precise reason.

    Imported lazily and per test so that an absent production surface produces
    one attributable RED per test rather than a single collection error.
    """
    try:
        import importlib
        return importlib.import_module(PRODUCTION_MODULE)
    except Exception as exc:  # noqa: BLE001 -- the reason is the evidence
        pytest.fail(
            "P1A production surface unavailable: importing %s raised %s: %s"
            % (PRODUCTION_MODULE, type(exc).__name__, exc)
        )


def initializer():
    module = production()
    fn = getattr(module, "initialize_registry", None)
    if fn is None:
        pytest.fail(
            "P1A production surface incomplete: %s defines no initialize_registry"
            % (PRODUCTION_MODULE,)
        )
    return fn


def refusal_type():
    module = production()
    exc = getattr(module, "RegistryInitializationRefused", None)
    if exc is None:
        pytest.fail(
            "P1A production surface incomplete: %s defines no"
            " RegistryInitializationRefused" % (PRODUCTION_MODULE,)
        )
    return exc


# ---------------------------------------------------------------------------
# Workspace observation
# ---------------------------------------------------------------------------

def digest_dir(directory):
    """Sorted filename set plus SHA-256 of every regular file."""
    out = {}
    for name in sorted(os.listdir(directory)):
        full = os.path.join(directory, name)
        if os.path.isfile(full) and not os.path.islink(full):
            with open(full, "rb") as handle:
                out[name] = sha256_hex(handle.read())
        elif os.path.isdir(full):
            out[name] = "<dir>"
        else:
            out[name] = "<other>"
    return out


def file_identity(path):
    st = os.stat(path)
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


def raw_connect(path):
    conn = sqlite3.connect(os.fspath(path), isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def master_mapping(path):
    conn = raw_connect(path)
    try:
        rows = conn.execute(SCHEMA_QUERY).fetchall()
    finally:
        conn.close()
    tables, triggers, skipped = {}, {}, []
    for kind, name, sql in rows:
        if name.startswith("sqlite_"):
            skipped.append(name)
            continue
        (tables if kind == "table" else triggers)[name] = sql
    return {"tables": tables, "triggers": triggers, "skipped": skipped}


def canonical_master_mapping(path):
    observed = master_mapping(path)
    return {
        "tables": {k: canonicalize_ddl(v) for k, v in observed["tables"].items()},
        "triggers": {k: canonicalize_ddl(v) for k, v in observed["triggers"].items()},
    }


def read_kv(path, table, key_col, value_col):
    conn = raw_connect(path)
    try:
        rows = conn.execute(
            "SELECT %s, %s FROM %s ORDER BY %s" % (key_col, value_col, table, key_col)
        ).fetchall()
    finally:
        conn.close()
    return dict(rows)


def is_busy_operational_error(exc):
    """Restated independently; the production classifier is never imported."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if code is None:
        return False
    return (code & 0xFF) in SQLITE_BUSY_PRIMARY_CODES


def classify_outcome(callable_, *args):
    """COMPLETED | INITIALIZATION_REFUSED | BUSY_TIMEOUT | UNCLASSIFIED."""
    refused = refusal_type()
    try:
        value = callable_(*args)
    except BaseException as exc:  # noqa: BLE001 -- exhaustiveness is the point
        if isinstance(exc, refused):
            return ("INITIALIZATION_REFUSED", exc)
        if is_busy_operational_error(exc):
            return ("BUSY_TIMEOUT", exc)
        return ("UNCLASSIFIED", exc)
    return ("COMPLETED", value)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return {
        "root": str(root),
        "registry": str(root / "trial_registry.sqlite3"),
        "anchor": str(root / "trial_registry.anchor"),
        "mutex": mutex_path_for(str(root / "trial_registry.anchor")),
    }


@pytest.fixture()
def initialized(workspace):
    outcome, payload = classify_outcome(
        initializer(), workspace["registry"], workspace["anchor"]
    )
    assert outcome == "COMPLETED", (
        "fixture precondition: a fresh initialization must complete; got %s (%r)"
        % (outcome, payload)
    )
    return workspace


# ===========================================================================
# 5.1  Fresh initialization and schema
# ===========================================================================

def test_p1a_t01_fresh_initialization_creates_exactly_three_artifacts(workspace):
    root = workspace["root"]
    assert digest_dir(root) == {}, "fixture must start empty"

    outcome, payload = classify_outcome(
        initializer(), workspace["registry"], workspace["anchor"]
    )
    assert outcome == "COMPLETED", "expected COMPLETED, got %s (%r)" % (outcome, payload)
    assert os.fspath(payload) == os.fspath(workspace["registry"]), (
        "COMPLETED must return the registry path; returned %r" % (payload,)
    )

    present = sorted(digest_dir(root))
    expected = sorted([
        os.path.basename(workspace["registry"]),
        os.path.basename(workspace["anchor"]),
        os.path.basename(workspace["mutex"]),
    ])
    assert present == expected, (
        "exactly three artifacts expected, found %r" % (present,)
    )

    # S-4: the mutex is at anchor_path + ".lock" and NOWHERE derived from the
    # registry path.  The negative half is what catches a derivation regression.
    assert os.path.exists(workspace["mutex"])
    for bad in (workspace["registry"] + ".anchor",
                workspace["registry"] + ".lock",
                workspace["registry"] + ".anchor.lock"):
        assert not os.path.exists(bad), (
            "no artifact may be derived from registry_path: %r exists" % (bad,)
        )

    for suffix in (".tmp", "-journal", "-wal", "-shm"):
        leftovers = [n for n in present if n.endswith(suffix)]
        assert leftovers == [], "no %s file may survive: %r" % (suffix, leftovers)


def test_p1a_t02_stored_ddl_equals_golden_after_canonicalization(initialized):
    registry = canonical_master_mapping(initialized["registry"])
    assert registry["tables"] == GOLDEN_REGISTRY_TABLES, (
        "registry tables diverge: %r" % (registry["tables"],)
    )
    assert registry["triggers"] == GOLDEN_REGISTRY_TRIGGERS, (
        "registry triggers diverge: %r" % (registry["triggers"],)
    )

    mutex = canonical_master_mapping(initialized["mutex"])
    assert mutex["tables"] == GOLDEN_MUTEX_TABLES, (
        "mutex tables diverge: %r" % (mutex["tables"],)
    )
    assert mutex["triggers"] == {}, "the mutex carries no trigger"

    # The type filter is load-bearing: implicit autoindex rows carry sql IS NULL
    # and must be excluded by the query, not tripped over.
    raw = master_mapping(initialized["registry"])
    assert all(not n.startswith("sqlite_") for n in raw["tables"]), (
        "sqlite_ internal objects must not appear as declared tables"
    )


@pytest.mark.parametrize("name", sorted(GOLDEN_EIGHT))
def test_p1a_t03_canonicalization_token_removed_exactly_once(name):
    golden = GOLDEN_EIGHT[name]
    with_token = _with_token(golden)

    assert with_token.count(IF_NOT_EXISTS_TOKEN) == 1, (
        "%s: the pre-storage form must contain the token exactly once" % (name,)
    )
    assert len(IF_NOT_EXISTS_TOKEN) == 14, "the token is fourteen characters"
    assert canonicalize_ddl(with_token) == golden, (
        "%s: one removal must yield the golden literal" % (name,)
    )
    # A doubled token must not be silently double-stripped by a single removal.
    doubled = with_token.replace(
        IF_NOT_EXISTS_TOKEN, IF_NOT_EXISTS_TOKEN * 2, 1
    )
    assert canonicalize_ddl(doubled) != golden, (
        "%s: a doubled token must be detectable, not silently absorbed" % (name,)
    )


def test_p1a_t04_tier1_structure_holds_independently_of_the_strings(initialized):
    observed = master_mapping(initialized["registry"])
    assert sorted(observed["tables"]) == [
        "registry_meta", "registry_state", "trial_ledger"
    ], "exactly three declared tables"
    assert len(observed["triggers"]) == 4, (
        "exactly four triggers, found %r" % (sorted(observed["triggers"]),)
    )

    conn = raw_connect(initialized["registry"])
    try:
        rows = conn.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
    finally:
        conn.close()

    pairs = set()
    for name, tbl_name, sql in rows:
        assert "BEFORE INSERT" not in sql, (
            "%s: no BEFORE INSERT trigger may exist" % (name,)
        )
        assert tbl_name != "registry_state", (
            "%s: registry_state must carry no trigger" % (name,)
        )
        assert "RAISE(ABORT," in sql, (
            "%s: ABORT is the only permitted conflict action; body was %r"
            % (name, sql)
        )
        for forbidden in ("RAISE(IGNORE", "RAISE(FAIL", "RAISE(ROLLBACK"):
            assert forbidden not in sql, "%s: %s is a divergence" % (name, forbidden)
        event = "BEFORE UPDATE" if "BEFORE UPDATE" in sql else "BEFORE DELETE"
        pairs.add((tbl_name, event))

    assert pairs == {
        ("registry_meta", "BEFORE UPDATE"), ("registry_meta", "BEFORE DELETE"),
        ("trial_ledger", "BEFORE UPDATE"), ("trial_ledger", "BEFORE DELETE"),
    }, "the four triggers are the cross product of two tables with two events"


def test_p1a_t05_mutex_is_created_and_validated_before_the_registry(workspace):
    trace = TraceRecorder()
    with trace.install(production()):
        outcome, payload = classify_outcome(
            initializer(), workspace["registry"], workspace["anchor"]
        )
    assert outcome == "COMPLETED", "expected COMPLETED, got %s (%r)" % (outcome, payload)

    first_mutex = trace.first_index(target="mutex")
    first_registry = trace.first_index(target="registry")
    assert first_mutex is not None, "no mutex operation was traced"
    assert first_registry is not None, "no registry operation was traced"
    assert first_mutex < first_registry, (
        "M5-C1: the mutex must be created and validated before the registry."
        "  first mutex op at %d, first registry op at %d\n%s"
        % (first_mutex, first_registry, trace.render())
    )

    create = trace.first_index(target="mutex", contains="CREATE TABLE")
    sentinel = trace.first_index(target="mutex", contains="registry_mutex(")
    assert create is not None and create < first_registry, (
        "the mutex table creation must precede the first registry operation"
    )
    assert sentinel is not None and sentinel < first_registry, (
        "the sentinel insert must precede the first registry operation"
    )


def test_p1a_t06_mutex_contains_exactly_one_table_and_one_sentinel_row(initialized):
    observed = master_mapping(initialized["mutex"])
    assert sorted(observed["tables"]) == ["registry_mutex"], (
        "exactly one mutex table, found %r" % (sorted(observed["tables"]),)
    )
    assert canonicalize_ddl(observed["tables"]["registry_mutex"]) == \
        GOLDEN_MUTEX_TABLES["registry_mutex"], "mutex DDL diverges"

    conn = raw_connect(initialized["mutex"])
    try:
        rows = conn.execute(
            "SELECT sentinel_key, sentinel_value FROM registry_mutex"
        ).fetchall()
        pk = conn.execute("PRAGMA table_info(registry_mutex)").fetchall()
    finally:
        conn.close()
    assert rows == [(MUTEX_SENTINEL_KEY, MUTEX_SENTINEL_VALUE)], (
        "exactly one sentinel row expected, found %r" % (rows,)
    )
    pk_cols = [c[1] for c in pk if c[5]]
    assert pk_cols == ["sentinel_key"], (
        "the primary key must be on sentinel_key, found %r" % (pk_cols,)
    )


def test_p1a_t07_registry_schema_built_in_exactly_one_exclusive_transaction(workspace):
    trace = TraceRecorder()
    with trace.install(production()):
        outcome, payload = classify_outcome(
            initializer(), workspace["registry"], workspace["anchor"]
        )
    assert outcome == "COMPLETED", "expected COMPLETED, got %s (%r)" % (outcome, payload)

    begins = trace.indices(target="registry", contains="BEGIN EXCLUSIVE")
    commits = trace.indices(target="registry", contains="COMMIT")
    rollbacks = trace.indices(target="registry", contains="ROLLBACK")
    assert len(begins) == 1, (
        "exactly one registry BEGIN EXCLUSIVE expected, found %d\n%s"
        % (len(begins), trace.render())
    )
    assert len(commits) >= 1, "a matching COMMIT is required"
    assert rollbacks == [], "no ROLLBACK may occur on the initialization path"

    open_i, close_i = begins[0], commits[0]
    assert open_i < close_i, "COMMIT must follow BEGIN EXCLUSIVE"

    required = ["CREATE TABLE", "CREATE TRIGGER",
                "registry_meta(", INIT_STATE_PENDING]
    for needle in required:
        hits = trace.indices(target="registry", contains=needle)
        assert hits, "no registry statement contained %r\n%s" % (needle, trace.render())
        outside = [i for i in hits if not (open_i < i < close_i)]
        assert outside == [], (
            "%r occurred outside the single transaction at indices %r\n%s"
            % (needle, outside, trace.render())
        )


def test_p1a_t08_if_not_exists_retained_but_not_the_concurrency_mechanism(workspace):
    trace = TraceRecorder()
    with trace.install(production()):
        outcome, _payload = classify_outcome(
            initializer(), workspace["registry"], workspace["anchor"]
        )
    assert outcome == "COMPLETED"

    creates = [e for e in trace.entries
               if e["kind"] == "execute" and e["payload"].startswith(("CREATE TABLE", "CREATE TRIGGER"))]
    assert len(creates) == 8, (
        "eight CREATE statements expected across both files, found %d" % (len(creates),)
    )
    for entry in creates:
        assert IF_NOT_EXISTS_TOKEN in entry["payload"], (
            "M5-C5: IF NOT EXISTS is retained for idempotent re-entry; %r lacks it"
            % (entry["payload"][:60],)
        )
    assert trace.indices(target="registry", contains="BEGIN EXCLUSIVE"), (
        "M5-C5: concurrency safety comes from the transaction, not from the token"
    )


# ===========================================================================
# The deterministic initialization trace seam
# ===========================================================================
#
# Passive by construction: every wrapper delegates to the real object and
# returns its result unchanged.  Nothing is suppressed, reordered or injected.
# t37 proves the passivity empirically rather than by assertion.

class _TracedCursorProxy:
    def __init__(self, real, recorder, target):
        self._real, self._rec, self._target = real, recorder, target

    def __getattr__(self, name):
        return getattr(self._real, name)


class _TracedConnection:
    def __init__(self, real, recorder, target):
        self._real, self._rec, self._target = real, recorder, target

    def __getattr__(self, name):
        return getattr(self._real, name)

    def execute(self, sql, *args, **kwargs):
        self._rec.record("execute", self._target, sql)
        return self._real.execute(sql, *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        self._rec.record("executemany", self._target, sql)
        return self._real.executemany(sql, *args, **kwargs)

    def commit(self):
        self._rec.record("commit", self._target, "COMMIT")
        return self._real.commit()

    def rollback(self):
        self._rec.record("rollback", self._target, "ROLLBACK")
        return self._real.rollback()

    def close(self):
        self._rec.record("close", self._target, "")
        return self._real.close()

    def cursor(self, *args, **kwargs):
        return _TracedCursorProxy(self._real.cursor(*args, **kwargs), self._rec, self._target)

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)


class _Sqlite3Shim:
    def __init__(self, real, recorder):
        self._real, self._rec = real, recorder

    def __getattr__(self, name):
        return getattr(self._real, name)

    def connect(self, database, *args, **kwargs):
        target = self._rec.classify_path(database)
        self._rec.record("connect", target, str(database))
        return _TracedConnection(
            self._real.connect(database, *args, **kwargs), self._rec, target
        )


class _OsPathProxy:
    def __init__(self, real, recorder):
        self._real, self._rec = real, recorder

    def __getattr__(self, name):
        return getattr(self._real, name)


class _OsShim:
    def __init__(self, real, recorder):
        self._real, self._rec = real, recorder
        self.path = _OsPathProxy(real.path, recorder)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def open(self, path, *args, **kwargs):
        self._rec.record("open", "fs", str(path))
        return self._real.open(path, *args, **kwargs)

    def write(self, fd, data):
        self._rec.record("write", "fs", "fd=%s bytes=%d" % (fd, len(data)))
        return self._real.write(fd, data)

    def fsync(self, fd):
        self._rec.record("fsync", "fs", "fd=%s" % (fd,))
        return self._real.fsync(fd)

    def close(self, fd):
        self._rec.record("close", "fs", "fd=%s" % (fd,))
        return self._real.close(fd)

    def replace(self, src, dst, *args, **kwargs):
        self._rec.record("replace", "fs", "%s->%s" % (src, dst))
        return self._real.replace(src, dst, *args, **kwargs)

    def rename(self, src, dst, *args, **kwargs):
        self._rec.record("rename", "fs", "%s->%s" % (src, dst))
        return self._real.rename(src, dst, *args, **kwargs)


class TraceRecorder:
    """Records every database and anchor filesystem operation, in order."""

    def __init__(self):
        self.entries = []
        self._lock = threading.Lock()

    def classify_path(self, database):
        text = str(database)
        if text.endswith(".lock") or ".lock" in text:
            return "mutex"
        return "registry"

    def record(self, kind, target, payload):
        with self._lock:
            self.entries.append({
                "index": len(self.entries),
                "kind": kind,
                "target": target,
                "payload": payload,
            })

    def indices(self, target=None, kind=None, contains=None):
        out = []
        for e in self.entries:
            if target is not None and e["target"] != target:
                continue
            if kind is not None and e["kind"] != kind:
                continue
            if contains is not None and contains not in e["payload"]:
                continue
            out.append(e["index"])
        return out

    def first_index(self, target=None, kind=None, contains=None):
        found = self.indices(target=target, kind=kind, contains=contains)
        return found[0] if found else None

    def render(self):
        return "\n".join(
            "%3d  %-11s %-8s %s" % (e["index"], e["kind"], e["target"], e["payload"][:110])
            for e in self.entries
        )

    class _Installed:
        def __init__(self, module, recorder):
            self.module, self.rec = module, recorder
            self.saved = {}

        def __enter__(self):
            for name, shim in (("sqlite3", _Sqlite3Shim), ("os", _OsShim)):
                real = getattr(self.module, name, None)
                if real is None:
                    pytest.fail(
                        "trace seam unavailable: %s exposes no module-level %r to"
                        " observe" % (PRODUCTION_MODULE, name)
                    )
                self.saved[name] = real
                setattr(self.module, name, shim(real, self.rec))
            return self.rec

        def __exit__(self, *exc):
            for name, real in self.saved.items():
                setattr(self.module, name, real)
            return False

    def install(self, module):
        return TraceRecorder._Installed(module, self)


# ===========================================================================
# 5.2  Metadata, identity and anchor content
# ===========================================================================

def test_p1a_t09_exactly_two_registry_meta_rows(initialized):
    rows = read_kv(initialized["registry"], "registry_meta", "meta_key", "meta_value")
    assert sorted(rows) == sorted([META_KEY_SCHEMA_VERSION, META_KEY_IDENTITY]), (
        "exactly two registry_meta rows expected, found %r" % (sorted(rows),)
    )
    assert rows[META_KEY_SCHEMA_VERSION] == REGISTRY_SCHEMA_VERSION, (
        "registry_schema_version must be the frozen literal %r, found %r"
        % (REGISTRY_SCHEMA_VERSION, rows[META_KEY_SCHEMA_VERSION])
    )


def test_p1a_t10_registry_identity_matches_the_independent_derivation(initialized):
    rows = read_kv(initialized["registry"], "registry_meta", "meta_key", "meta_value")
    stored = rows.get(META_KEY_IDENTITY)
    expected = expected_registry_identity(
        initialized["registry"], initialized["anchor"]
    )
    assert stored == expected, (
        "registry_identity must equal the independently recomputed derivation.\n"
        "  stored   = %r\n  expected = %r" % (stored, expected)
    )


def test_p1a_t11_anchor_bytes_equal_the_canonical_document(initialized):
    with open(initialized["anchor"], "rb") as handle:
        raw = handle.read()
    identity = expected_registry_identity(
        initialized["registry"], initialized["anchor"]
    )
    expected = canonical_bytes(expected_fresh_anchor(identity))
    assert raw == expected, (
        "anchor bytes must equal _canonical_bytes(doc) exactly -- no trailing"
        " newline, no wrapper.\n  got      = %r\n  expected = %r" % (raw[:200], expected[:200])
    )
    doc = json.loads(raw.decode("utf-8"))
    assert doc["schema_version"] == ANCHOR_SCHEMA_VERSION
    assert doc["document_sequence"] == 1
    assert doc["programs"] == {}
    assert doc["registry_identity"] == identity
    assert sorted(doc) == sorted([
        "schema_version", "registry_identity", "document_sequence",
        "programs", "document_hash",
    ]), "exact anchor key set required, found %r" % (sorted(doc),)


def test_p1a_t12_anchor_seal_excludes_itself_and_detects_mutation(initialized):
    with open(initialized["anchor"], "rb") as handle:
        doc = json.loads(handle.read().decode("utf-8"))
    body = {k: v for k, v in doc.items() if k != "document_hash"}
    assert doc["document_hash"] == sha256_hex(canonical_bytes(body)), (
        "document_hash must seal the document excluding itself"
    )
    mutated = dict(body)
    mutated["document_sequence"] = body["document_sequence"] + 1
    assert sha256_hex(canonical_bytes(mutated)) != doc["document_hash"], (
        "the seal must detect a mutated body"
    )


def test_p1a_t13_init_state_lives_only_in_registry_state(initialized):
    state = read_kv(initialized["registry"], "registry_state", "state_key", "state_value")
    assert sorted(state) == [INIT_STATE_KEY], (
        "registry_state must hold exactly one row, found %r" % (sorted(state),)
    )
    assert state[INIT_STATE_KEY] == INIT_STATE_COMPLETE, (
        "init_state must be COMPLETE after a successful initialization, found %r"
        % (state[INIT_STATE_KEY],)
    )
    meta = read_kv(initialized["registry"], "registry_meta", "meta_key", "meta_value")
    assert INIT_STATE_KEY not in meta, (
        "S-5 regression: init_state must NOT appear in registry_meta"
    )


def test_p1a_t14_registry_state_carries_no_trigger(initialized):
    conn = raw_connect(initialized["registry"])
    try:
        rows = conn.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
    finally:
        conn.close()
    on_state = [n for n, t in rows if t == "registry_state"]
    assert on_state == [], (
        "registry_state must carry no trigger; found %r" % (on_state,)
    )
    others = sorted(t for _n, t in rows)
    assert others == ["registry_meta", "registry_meta", "trial_ledger", "trial_ledger"], (
        "the absence must be specific: the other two tables keep their triggers;"
        " found %r" % (others,)
    )


# ===========================================================================
# 5.3  Durability and state transition
# ===========================================================================

def test_p1a_t15_anchor_durability_chain_is_complete_and_ordered(workspace):
    trace = TraceRecorder()
    with trace.install(production()):
        outcome, payload = classify_outcome(
            initializer(), workspace["registry"], workspace["anchor"]
        )
    assert outcome == "COMPLETED", "expected COMPLETED, got %s (%r)" % (outcome, payload)

    # Four separately messaged claims, so a partial pass is visible.
    replace_i = trace.first_index(target="fs", kind="replace")
    assert replace_i is not None, (
        "claim 3: os.replace must move the temporary file onto the anchor path\n%s"
        % (trace.render(),)
    )

    writes = trace.indices(target="fs", kind="write")
    assert writes and min(writes) < replace_i, (
        "claim 1: a temporary file must be written before os.replace\n%s"
        % (trace.render(),)
    )

    fsyncs = trace.indices(target="fs", kind="fsync")
    before = [i for i in fsyncs if i < replace_i]
    after = [i for i in fsyncs if i > replace_i]
    assert before, (
        "claim 2: the temporary file must be fsynced BEFORE os.replace."
        "  fsync indices %r, os.replace at %d\n%s" % (fsyncs, replace_i, trace.render())
    )
    assert after, (
        "claim 4: the containing DIRECTORY must be fsynced AFTER os.replace."
        "  Without it the directory entry created by the rename is not durable."
        "  fsync indices %r, os.replace at %d\n%s" % (fsyncs, replace_i, trace.render())
    )

    survivors = [n for n in digest_dir(workspace["root"]) if n.endswith(".tmp")]
    assert survivors == [], "no temporary file may survive: %r" % (survivors,)


def test_p1a_t16_complete_is_written_only_after_the_anchor_is_durable(workspace):
    trace = TraceRecorder()
    with trace.install(production()):
        outcome, _payload = classify_outcome(
            initializer(), workspace["registry"], workspace["anchor"]
        )
    assert outcome == "COMPLETED"

    replace_i = trace.first_index(target="fs", kind="replace")
    assert replace_i is not None, "os.replace was never traced"
    dir_fsyncs = [i for i in trace.indices(target="fs", kind="fsync") if i > replace_i]
    assert dir_fsyncs, "no directory fsync followed os.replace"

    complete_writes = trace.indices(target="registry", contains=INIT_STATE_COMPLETE)
    assert complete_writes, (
        "no registry statement carried %r\n%s" % (INIT_STATE_COMPLETE, trace.render())
    )
    assert min(dir_fsyncs) < min(complete_writes), (
        "init_state=COMPLETE must be written only after the anchor's directory"
        " entry is durable.  directory fsync at %d, COMPLETE write at %d\n%s"
        % (min(dir_fsyncs), min(complete_writes), trace.render())
    )


def test_p1a_t17_init_state_transitions_exactly_once_in_one_direction(workspace):
    trace = TraceRecorder()
    with trace.install(production()):
        outcome, _payload = classify_outcome(
            initializer(), workspace["registry"], workspace["anchor"]
        )
    assert outcome == "COMPLETED"

    pending = trace.indices(target="registry", contains=INIT_STATE_PENDING)
    complete = trace.indices(target="registry", contains=INIT_STATE_COMPLETE)
    assert len(pending) == 1, (
        "exactly one PENDING_ANCHOR_CREATE write expected, found %d\n%s"
        % (len(pending), trace.render())
    )
    assert len(complete) == 1, (
        "exactly one COMPLETE write expected, found %d\n%s"
        % (len(complete), trace.render())
    )
    assert pending[0] < complete[0], "PENDING must precede COMPLETE"

    begins = trace.indices(target="registry", contains="BEGIN EXCLUSIVE")
    commits = trace.indices(target="registry", contains="COMMIT")
    assert begins and begins[0] < pending[0] < commits[0], (
        "the PENDING write must occur inside the single I3 transaction"
    )
    immediates = trace.indices(target="registry", contains="BEGIN IMMEDIATE")
    assert immediates, (
        "I5 must open a BEGIN IMMEDIATE transaction for the COMPLETE update\n%s"
        % (trace.render(),)
    )

    state = read_kv(workspace["registry"], "registry_state", "state_key", "state_value")
    assert state == {INIT_STATE_KEY: INIT_STATE_COMPLETE}, (
        "final registry_state must be exactly one COMPLETE row, found %r" % (state,)
    )


# ===========================================================================
# 5.4  Idempotence -- S11 is a STRICT no-op
# ===========================================================================

def test_p1a_t18_reinitialization_of_a_complete_registry_is_a_strict_no_op(initialized):
    root = initialized["root"]
    before_digest = digest_dir(root)
    before_anchor = file_identity(initialized["anchor"])
    before_registry = file_identity(initialized["registry"])
    before_mutex = file_identity(initialized["mutex"])
    before_meta = read_kv(initialized["registry"], "registry_meta", "meta_key", "meta_value")

    trace = TraceRecorder()
    with trace.install(production()):
        outcome, payload = classify_outcome(
            initializer(), initialized["registry"], initialized["anchor"]
        )
    assert outcome == "COMPLETED", "expected COMPLETED, got %s (%r)" % (outcome, payload)

    assert digest_dir(root) == before_digest, "the workspace digest must be identical"
    assert file_identity(initialized["anchor"]) == before_anchor, (
        "S11 is a STRICT no-op: the anchor's inode and mtime must not change."
        "  before=%r after=%r" % (before_anchor, file_identity(initialized["anchor"]))
    )
    assert file_identity(initialized["registry"]) == before_registry, (
        "the registry must not be modified"
    )
    assert file_identity(initialized["mutex"]) == before_mutex, (
        "the mutex must not be modified"
    )
    assert read_kv(initialized["registry"], "registry_meta", "meta_key", "meta_value") \
        == before_meta, "no second identity may be minted"

    # I4 and I5 must NOT be re-executed.
    assert trace.indices(target="fs", kind="replace") == [], (
        "S11 must not re-execute I4: os.replace occurred\n%s" % (trace.render(),)
    )
    assert trace.indices(target="fs", kind="write") == [], (
        "S11 must not write an anchor temporary file\n%s" % (trace.render(),)
    )
    assert trace.indices(target="registry", contains=INIT_STATE_COMPLETE) == [], (
        "S11 must not re-execute I5: a COMPLETE write occurred\n%s" % (trace.render(),)
    )
    survivors = [n for n in digest_dir(root) if n.endswith(".tmp")]
    assert survivors == [], "S11 must create no temporary file: %r" % (survivors,)


@pytest.mark.parametrize("repetition", [1, 2, 3])
def test_p1a_t19_reinitialization_is_stable_across_repeated_calls(initialized, repetition):
    root = initialized["root"]
    before_digest = digest_dir(root)
    before_anchor = file_identity(initialized["anchor"])
    for _n in range(repetition):
        outcome, payload = classify_outcome(
            initializer(), initialized["registry"], initialized["anchor"]
        )
        assert outcome == "COMPLETED", (
            "repetition %d: expected COMPLETED, got %s (%r)" % (repetition, outcome, payload)
        )
        assert digest_dir(root) == before_digest, (
            "repetition %d: the workspace digest changed" % (repetition,)
        )
        assert file_identity(initialized["anchor"]) == before_anchor, (
            "repetition %d: the anchor was rewritten" % (repetition,)
        )


# ===========================================================================
# 5.5  Recovery -- deterministic, no contention
# ===========================================================================

def _break_to_pending(workspace, remove_anchor):
    """Return the workspace to S9 (anchor absent) or S10 (anchor present)."""
    conn = raw_connect(workspace["registry"])
    try:
        conn.execute(
            "UPDATE registry_state SET state_value = ? WHERE state_key = ?",
            (INIT_STATE_PENDING, INIT_STATE_KEY),
        )
    finally:
        conn.close()
    if remove_anchor:
        os.remove(workspace["anchor"])
    return workspace


def test_p1a_t20_s9_pending_without_anchor_recovers_deterministically(initialized):
    ws = _break_to_pending(initialized, remove_anchor=True)
    assert not os.path.exists(ws["anchor"])

    outcome, payload = classify_outcome(initializer(), ws["registry"], ws["anchor"])
    assert outcome == "COMPLETED", (
        "S9 must RECOVER deterministically without contention; got %s (%r)"
        % (outcome, payload)
    )
    assert os.path.exists(ws["anchor"]), "recovery must write the anchor"

    state = read_kv(ws["registry"], "registry_state", "state_key", "state_value")
    assert state == {INIT_STATE_KEY: INIT_STATE_COMPLETE}, (
        "recovery must leave init_state COMPLETE, found %r" % (state,)
    )
    identity = expected_registry_identity(ws["registry"], ws["anchor"])
    with open(ws["anchor"], "rb") as handle:
        assert handle.read() == canonical_bytes(expected_fresh_anchor(identity)), (
            "the recovered anchor must equal the frozen fresh-initialization document"
        )
    survivors = [n for n in digest_dir(ws["root"]) if n.endswith(".tmp")]
    assert survivors == [], "no temporary file may survive recovery: %r" % (survivors,)


def test_p1a_t21_s10_pending_with_valid_anchor_recovers_byte_identically(initialized):
    ws = _break_to_pending(initialized, remove_anchor=False)
    with open(ws["anchor"], "rb") as handle:
        before_bytes = handle.read()

    outcome, payload = classify_outcome(initializer(), ws["registry"], ws["anchor"])
    assert outcome == "COMPLETED", (
        "S10 must RECOVER deterministically without contention; got %s (%r)"
        % (outcome, payload)
    )
    with open(ws["anchor"], "rb") as handle:
        after_bytes = handle.read()
    assert after_bytes == before_bytes, (
        "the S10 anchor rewrite must be byte-identical"
    )
    state = read_kv(ws["registry"], "registry_state", "state_key", "state_value")
    assert state == {INIT_STATE_KEY: INIT_STATE_COMPLETE}


def test_p1a_t22_s8_zero_length_registry_without_anchor_initializes_fresh(workspace):
    open(workspace["registry"], "wb").close()
    assert os.path.getsize(workspace["registry"]) == 0
    assert not os.path.exists(workspace["anchor"])

    outcome, payload = classify_outcome(
        initializer(), workspace["registry"], workspace["anchor"]
    )
    assert outcome == "COMPLETED", (
        "S8: a zero-length registry with no anchor permits fresh initialization;"
        " got %s (%r)" % (outcome, payload)
    )
    assert canonical_master_mapping(workspace["registry"])["tables"] == \
        GOLDEN_REGISTRY_TABLES, "the zero-length file must be replaced by a real registry"
    assert os.path.exists(workspace["anchor"])


def test_p1a_t23_s2_zero_length_mutex_is_recreated(workspace):
    open(workspace["mutex"], "wb").close()
    assert os.path.getsize(workspace["mutex"]) == 0

    outcome, payload = classify_outcome(
        initializer(), workspace["registry"], workspace["anchor"]
    )
    assert outcome == "COMPLETED", (
        "S2: a zero-length mutex carries no state and is recreated; got %s (%r)"
        % (outcome, payload)
    )
    conn = raw_connect(workspace["mutex"])
    try:
        rows = conn.execute(
            "SELECT sentinel_key, sentinel_value FROM registry_mutex"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(MUTEX_SENTINEL_KEY, MUTEX_SENTINEL_VALUE)], (
        "the recreated mutex must carry exactly the sentinel pair, found %r" % (rows,)
    )


# ===========================================================================
# 5.6  Fail-closed -- deterministic refusal
# ===========================================================================
#
# Every test here asserts, in addition to its own fixture-specific state:
#   * exactly RegistryInitializationRefused is raised;
#   * the raised exception is NOT a sqlite3.Error and NOT an OSError;
#   * __cause__ is None, proving `raise ... from None` was honoured;
#   * the complete workspace digest is unchanged.
# The message is never asserted and never parsed.  It is not contractual.

def assert_refused_without_mutation(workspace, before_digest, label):
    refused = refusal_type()
    outcome, payload = classify_outcome(
        initializer(), workspace["registry"], workspace["anchor"]
    )
    assert outcome == "INITIALIZATION_REFUSED", (
        "%s: expected a deterministic refusal, got %s (%r)" % (label, outcome, payload)
    )
    assert type(payload) is refused, (
        "%s: the refusal must be exactly RegistryInitializationRefused, got %r"
        % (label, type(payload))
    )
    assert not isinstance(payload, sqlite3.Error), (
        "%s: a sqlite3 exception must not escape a deterministic refusal path" % (label,)
    )
    assert not isinstance(payload, OSError), (
        "%s: an OSError must not escape a deterministic refusal path" % (label,)
    )
    assert payload.__cause__ is None, (
        "%s: refusals are raised `from None`; __cause__ was %r"
        % (label, payload.__cause__)
    )
    after = digest_dir(workspace["root"])
    assert after == before_digest, (
        "%s: the workspace must be byte-identical after a refusal.\n"
        "  before = %r\n  after  = %r" % (label, before_digest, after)
    )
    return payload


def _make_valid_mutex(workspace):
    conn = raw_connect(workspace["mutex"])
    try:
        conn.execute(GOLDEN_MUTEX_TABLES["registry_mutex"])
        conn.execute(
            "INSERT INTO registry_mutex(sentinel_key, sentinel_value) VALUES(?, ?)",
            (MUTEX_SENTINEL_KEY, MUTEX_SENTINEL_VALUE),
        )
    finally:
        conn.close()


def test_p1a_t24_s3_mutex_table_without_sentinel_row_fails_closed(workspace):
    conn = raw_connect(workspace["mutex"])
    try:
        conn.execute(GOLDEN_MUTEX_TABLES["registry_mutex"])
    finally:
        conn.close()
    before = digest_dir(workspace["root"])
    assert_refused_without_mutation(workspace, before, "S3 sentinel row missing")
    assert not os.path.exists(workspace["registry"]), "no registry may be created"


def test_p1a_t25_s4_wrong_sentinel_value_fails_closed(workspace):
    conn = raw_connect(workspace["mutex"])
    try:
        conn.execute(GOLDEN_MUTEX_TABLES["registry_mutex"])
        conn.execute(
            "INSERT INTO registry_mutex(sentinel_key, sentinel_value) VALUES(?, ?)",
            (MUTEX_SENTINEL_KEY, "NOT_THE_SENTINEL"),
        )
    finally:
        conn.close()
    before = digest_dir(workspace["root"])
    assert_refused_without_mutation(workspace, before, "S4 wrong sentinel value")
    assert not os.path.exists(workspace["registry"])


def test_p1a_t26_s5_divergent_mutex_ddl_fails_closed(workspace):
    conn = raw_connect(workspace["mutex"])
    try:
        conn.execute(
            "CREATE TABLE registry_mutex ( sentinel_key TEXT NOT NULL,"
            " sentinel_value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO registry_mutex(sentinel_key, sentinel_value) VALUES(?, ?)",
            (MUTEX_SENTINEL_KEY, MUTEX_SENTINEL_VALUE),
        )
    finally:
        conn.close()
    before = digest_dir(workspace["root"])
    assert_refused_without_mutation(workspace, before, "S5 divergent mutex DDL")
    assert not os.path.exists(workspace["registry"])


def test_p1a_t27_s6_malformed_mutex_bytes_fail_closed(workspace):
    payload = b"this is not a sqlite database" * 8
    with open(workspace["mutex"], "wb") as handle:
        handle.write(payload)
    before = digest_dir(workspace["root"])
    assert_refused_without_mutation(workspace, before, "S6 malformed mutex")
    with open(workspace["mutex"], "rb") as handle:
        assert handle.read() == payload, "the malformed file's bytes must be unchanged"
    assert not os.path.exists(workspace["registry"])


def test_p1a_t28_s8b_zero_length_registry_with_anchor_fails_closed(initialized):
    with open(initialized["anchor"], "rb") as handle:
        anchor_bytes = handle.read()
    open(initialized["registry"], "wb").close()
    assert os.path.getsize(initialized["registry"]) == 0
    before = digest_dir(initialized["root"])

    assert_refused_without_mutation(
        initialized, before, "S8b zero-length registry beside a surviving anchor"
    )
    with open(initialized["anchor"], "rb") as handle:
        assert handle.read() == anchor_bytes, "the anchor must be left byte-identical"
    assert os.path.getsize(initialized["registry"]) == 0, (
        "the zero-length registry must not be overwritten"
    )


def test_p1a_t29_s12_complete_registry_without_anchor_fails_closed(initialized):
    os.remove(initialized["anchor"])
    before = digest_dir(initialized["root"])
    assert_refused_without_mutation(
        initialized, before, "S12 COMPLETE registry with the anchor lost"
    )
    assert not os.path.exists(initialized["anchor"]), (
        "no anchor may be recreated: doing so would reset every high-water value"
    )


def test_p1a_t30_s13_pending_with_non_empty_ledger_fails_closed(initialized):
    conn = raw_connect(initialized["registry"])
    try:
        conn.execute(
            "UPDATE registry_state SET state_value = ? WHERE state_key = ?",
            (INIT_STATE_PENDING, INIT_STATE_KEY),
        )
        conn.execute(
            "INSERT INTO trial_ledger(research_program_id, chain_index, row_json)"
            " VALUES(?, ?, ?)",
            ("p1a-probe-program", 0, json.dumps({"probe": True})),
        )
    finally:
        conn.close()
    before = digest_dir(initialized["root"])
    assert_refused_without_mutation(
        initialized, before, "S13 PENDING with a non-empty ledger"
    )


def test_p1a_t31_non_directory_parent_fails_closed(tmp_path):
    blocker = tmp_path / "not_a_directory"
    blocker.write_bytes(b"x")
    ws = {
        "root": str(tmp_path),
        "registry": str(blocker / "trial_registry.sqlite3"),
        "anchor": str(blocker / "trial_registry.anchor"),
        "mutex": mutex_path_for(str(blocker / "trial_registry.anchor")),
    }
    before = digest_dir(str(tmp_path))
    assert_refused_without_mutation(ws, before, "non-directory parent")


@pytest.mark.parametrize("code", PROHIBITED_CODES)
def test_p1a_t32_no_prohibited_code_appears_in_any_refusal(workspace, code):
    with open(workspace["mutex"], "wb") as handle:
        handle.write(b"not a database")
    refused = refusal_type()

    assert code not in refused.__name__, (
        "the refusal type name must not contain %r" % (code,)
    )
    outcome, payload = classify_outcome(
        initializer(), workspace["registry"], workspace["anchor"]
    )
    assert outcome == "INITIALIZATION_REFUSED", (
        "expected a refusal fixture; got %s (%r)" % (outcome, payload)
    )
    # Absence is inspected here; that does not make the message contractual.
    assert code not in str(payload), (
        "%r must never appear in an initializer outcome" % (code,)
    )
    assert code not in type(payload).__name__


# ===========================================================================
# 5.7  Concurrency and trace
# ===========================================================================

_CONTENDER_SOURCE = """\
import json, os, sqlite3, sys
sys.path.insert(0, sys.argv[1])
registry, anchor = sys.argv[2], sys.argv[3]
out = {"outcome": "UNCLASSIFIED", "detail": ""}
try:
    from app.research.phase5_trial_registry import (
        initialize_registry, RegistryInitializationRefused,
    )
except BaseException as exc:
    out = {"outcome": "SURFACE_ABSENT",
           "detail": "%s: %s" % (type(exc).__name__, exc)}
else:
    try:
        value = initialize_registry(registry, anchor)
    except RegistryInitializationRefused as exc:
        out = {"outcome": "INITIALIZATION_REFUSED", "detail": type(exc).__name__}
    except sqlite3.OperationalError as exc:
        code = getattr(exc, "sqlite_errorcode", None)
        if code is not None and (code & 0xFF) in (5, 6):
            out = {"outcome": "BUSY_TIMEOUT", "detail": "sqlite_errorcode=%d" % code}
        else:
            out = {"outcome": "UNCLASSIFIED",
                   "detail": "OperationalError sqlite_errorcode=%r" % (code,)}
    except BaseException as exc:
        out = {"outcome": "UNCLASSIFIED",
               "detail": "%s: %s" % (type(exc).__name__, exc)}
    else:
        out = {"outcome": "COMPLETED", "detail": str(value)}
sys.stdout.write(json.dumps(out))
"""


class _WinnerHold:
    """Pauses participant A immediately after it acquires the mutex EXCLUSIVE lock.

    The seam is installed on the production module's own sqlite3 reference, so
    A blocks *inside* its initializer while genuinely holding the lock.  No
    external lock is taken, so A can never block itself.  Release is by
    threading.Event -- signals only, never sleeps.
    """

    def __init__(self, mutex_path):
        self.mutex_path = os.fspath(mutex_path)
        self.acquired = threading.Event()
        self.release = threading.Event()
        self.saw_exclusive = False

    def wrap(self, real_sqlite3):
        hold = self

        class _Conn:
            def __init__(self, real, is_mutex):
                self._real, self._is_mutex = real, is_mutex

            def __getattr__(self, name):
                return getattr(self._real, name)

            def execute(self, sql, *args, **kwargs):
                result = self._real.execute(sql, *args, **kwargs)
                if self._is_mutex and "BEGIN EXCLUSIVE" in sql.upper():
                    hold.saw_exclusive = True
                    hold.acquired.set()
                    hold.release.wait()
                return result

            def __enter__(self):
                self._real.__enter__()
                return self

            def __exit__(self, *exc):
                return self._real.__exit__(*exc)

        class _Shim:
            def __getattr__(self, name):
                return getattr(real_sqlite3, name)

            def connect(self, database, *args, **kwargs):
                is_mutex = os.fspath(database) == hold.mutex_path
                return _Conn(real_sqlite3.connect(database, *args, **kwargs), is_mutex)

        return _Shim()


def _run_two_phase(workspace, winner_label):
    module = production()
    hold = _WinnerHold(workspace["mutex"])
    result = {}

    def participant_a():
        result["a"] = classify_outcome(
            initializer(), workspace["registry"], workspace["anchor"]
        )

    real_sqlite3 = getattr(module, "sqlite3", None)
    if real_sqlite3 is None:
        pytest.fail(
            "concurrency seam unavailable: %s exposes no module-level sqlite3"
            % (PRODUCTION_MODULE,)
        )
    module.sqlite3 = hold.wrap(real_sqlite3)
    thread = threading.Thread(target=participant_a, daemon=True)
    try:
        thread.start()
        # Phase 1: block until A holds the EXCLUSIVE lock.  No sleep.
        if not hold.acquired.wait(timeout=CONTENDER_DEADLINE_S):
            hold.release.set()
            thread.join(timeout=CONTENDER_DEADLINE_S)
            pytest.fail(
                "participant %s never signalled acquisition of the mutex"
                " EXCLUSIVE lock within %.0fs; saw_exclusive=%r"
                % (winner_label, CONTENDER_DEADLINE_S, hold.saw_exclusive)
            )
        # Phase 2: release B and observe its exact classified result.
        repo_root = os.environ.get("MIE_REPO_ROOT", "")
        proc = subprocess.run(
            [sys.executable, "-c", _CONTENDER_SOURCE, repo_root,
             workspace["registry"], workspace["anchor"]],
            capture_output=True, text=True, timeout=CONTENDER_DEADLINE_S,
        )
        result["b_raw"] = proc.stdout
        result["b_err"] = proc.stderr
    finally:
        # Phase 3: only now is A released.
        hold.release.set()
        thread.join(timeout=CONTENDER_DEADLINE_S)
        module.sqlite3 = real_sqlite3
    return hold, result


def _assert_two_phase(workspace, result, hold, winner_label):
    assert hold.saw_exclusive, (
        "the seam never observed a BEGIN EXCLUSIVE on the mutex; the"
        " designated winner did not acquire the lock"
    )
    assert result.get("b_raw"), (
        "the contender produced no classified output; stderr was:\n%s"
        % (result.get("b_err", ""),)
    )
    b = json.loads(result["b_raw"])
    assert b["outcome"] == "BUSY_TIMEOUT", (
        "the contender must receive an unchanged busy sqlite3.OperationalError;"
        " got %s (%s)" % (b["outcome"], b["detail"])
    )
    assert b["outcome"] != "INITIALIZATION_REFUSED", (
        "S-2: a busy error must NOT be translated to RegistryInitializationRefused"
    )

    a_outcome, a_payload = result["a"]
    assert a_outcome == "COMPLETED", (
        "the designated winner %s must complete; got %s (%r)"
        % (winner_label, a_outcome, a_payload)
    )
    assert os.fspath(a_payload) == os.fspath(workspace["registry"])

    present = sorted(digest_dir(workspace["root"]))
    assert present == sorted([
        os.path.basename(workspace["registry"]),
        os.path.basename(workspace["anchor"]),
        os.path.basename(workspace["mutex"]),
    ]), "exactly three artifacts must remain, found %r" % (present,)
    state = read_kv(workspace["registry"], "registry_state", "state_key", "state_value")
    assert state == {INIT_STATE_KEY: INIT_STATE_COMPLETE}
    meta = read_kv(workspace["registry"], "registry_meta", "meta_key", "meta_value")
    assert sorted(meta) == sorted([META_KEY_SCHEMA_VERSION, META_KEY_IDENTITY]), (
        "exactly one identity may exist, found %r" % (sorted(meta),)
    )
    survivors = [n for n in digest_dir(workspace["root"]) if n.endswith(".tmp")]
    assert survivors == [], "no temporary file may survive: %r" % (survivors,)


def test_p1a_t33_two_phase_designated_winner_a(workspace):
    hold, result = _run_two_phase(workspace, "A")
    _assert_two_phase(workspace, result, hold, "A")


def test_p1a_t34_two_phase_designated_winner_b(workspace):
    # Symmetry: the roles are exchanged by construction, not by scheduling.
    hold, result = _run_two_phase(workspace, "B")
    _assert_two_phase(workspace, result, hold, "B")


@pytest.mark.parametrize("processes", [4])
def test_p1a_t35_concurrent_stress_supplemental(workspace, processes):
    """SUPPLEMENTAL REALISM ONLY.  Never the sole evidence for M5-C3."""
    repo_root = os.environ.get("MIE_REPO_ROOT", "")
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _CONTENDER_SOURCE, repo_root,
             workspace["registry"], workspace["anchor"]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _n in range(processes)
    ]
    outcomes = []
    for proc in procs:
        out, err = proc.communicate(timeout=CONTENDER_DEADLINE_S)
        assert out, "a participant produced no classified output; stderr:\n%s" % (err,)
        outcomes.append(json.loads(out)["outcome"])

    unclassified = [o for o in outcomes if o == "UNCLASSIFIED"]
    assert unclassified == [], (
        "M5-C3: every outcome must classify into the closed allow-list;"
        " unclassified outcomes: %r (all: %r)" % (unclassified, outcomes)
    )
    assert all(o in ("COMPLETED", "INITIALIZATION_REFUSED", "BUSY_TIMEOUT",
                     "SURFACE_ABSENT") for o in outcomes), (
        "unexpected outcome label in %r" % (outcomes,)
    )
    assert "SURFACE_ABSENT" not in outcomes, (
        "the production surface was unavailable to the participants: %r" % (outcomes,)
    )
    assert "COMPLETED" in outcomes, "at least one participant must complete"


def test_p1a_t36_trace_is_deterministic(tmp_path):
    traces = []
    for label in ("a", "b"):
        root = tmp_path / ("det_" + label)
        root.mkdir()
        registry = str(root / "trial_registry.sqlite3")
        anchor = str(root / "trial_registry.anchor")
        trace = TraceRecorder()
        with trace.install(production()):
            outcome, payload = classify_outcome(initializer(), registry, anchor)
        assert outcome == "COMPLETED", (
            "run %s: expected COMPLETED, got %s (%r)" % (label, outcome, payload)
        )
        traces.append([
            (e["kind"], e["target"], e["payload"].replace(str(root), "<ROOT>"))
            for e in trace.entries
        ])
    assert traces[0] == traces[1], (
        "the initialization trace must be deterministic entry for entry"
    )


def test_p1a_t37_trace_seam_is_passive(tmp_path):
    digests = {}
    for label, observed in (("seam_on", True), ("seam_off", False)):
        root = tmp_path / label
        root.mkdir()
        registry = str(root / "trial_registry.sqlite3")
        anchor = str(root / "trial_registry.anchor")
        if observed:
            trace = TraceRecorder()
            with trace.install(production()):
                outcome, payload = classify_outcome(initializer(), registry, anchor)
        else:
            outcome, payload = classify_outcome(initializer(), registry, anchor)
        assert outcome == "COMPLETED", (
            "%s: expected COMPLETED, got %s (%r)" % (label, outcome, payload)
        )
        digests[label] = digest_dir(str(root))
        digests[label + "_schema"] = canonical_master_mapping(registry)
        digests[label + "_state"] = read_kv(
            registry, "registry_state", "state_key", "state_value"
        )

    assert sorted(digests["seam_on"]) == sorted(digests["seam_off"]), (
        "enabling observation must not change the artifact set"
    )
    assert digests["seam_on_schema"] == digests["seam_off_schema"], (
        "enabling observation must not change the schema"
    )
    assert digests["seam_on_state"] == digests["seam_off_state"], (
        "enabling observation must not change init_state"
    )
