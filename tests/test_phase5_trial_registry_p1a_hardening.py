"""WP5-0A2-R2 · P1A · ADDITIVE HARDENING TESTS.

ROUND            : WP5-0A2-R2 (rebuild), package P1A, hardening round
BASELINE COMMIT  : affd12544b564fa10a7c68cd95a373f27bee8250
ADDITIVE TO      : tests/test_phase5_trial_registry.py
                     sha256 c5c562af4867cae7238997ed00375929fd14d28c1c15ac1da6f13ae9c17888b3
                   THAT FILE IS FROZEN.  It is not edited, not imported from, and
                   not relied upon by anything below.

SCOPE.  Exactly the three findings raised against the P1A GREEN payload:

  H1  PRAGMA integrity_check is never executed.  R2 section 3.1 requires an
      existing registry to satisfy it before its schema, metadata, state or
      ledger contents are relied upon.  Schema equality is not a substitute for
      SQLite structural integrity.

  H2  S10 recovery is not crash-safe.  R2 S10 requires re-executing I4 and I5.
      Byte equality of a surviving anchor does not prove its directory entry was
      durable before the prior crash, so a real durable rewrite must occur.

  H3  `from None` is unenforced.  The GREEN discrimination battery disclosed a
      surviving mutant: removing `from None` is invisible to a `__cause__ is
      None` assertion, because neither form sets `__cause__`.  The observable
      that does discriminate is `__suppress_context__`.

INDEPENDENCE RULE.  Every expected literal is restated here.  This module never
imports a production constant to build an expected value, and never imports the
frozen test module.  The observation seams below are written from scratch rather
than reused, so a defect in one seam cannot silently excuse the other.

PASSIVITY.  Every wrapper delegates to the real object and returns its result
unchanged, with exactly one deliberate exception: when a test explicitly arms
it, the shim substitutes the result of PRAGMA integrity_check.  That substitution
is the instrument under test and is never enabled implicitly.
"""

import hashlib
import json
import os
import sqlite3
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

INTEGRITY_STATEMENT_FRAGMENT = "integrity_check"
INTEGRITY_OK_ROWS = [("ok",)]

# Deliberately not "ok".  Real SQLite emits free-form diagnostic rows here; the
# contract accepts exactly one row whose value is "ok" and nothing else, so any
# other shape must fail closed.
INTEGRITY_BAD_ROWS = [("*** in database main ***\nPage 4 is never used",)]

PRODUCTION_MODULE = "app.research.phase5_trial_registry"


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


def expected_registry_identity(registry_path, anchor_path):
    return sha256_hex(canonical_bytes({
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        "registry_path": os.path.abspath(registry_path),
        "anchor_path": os.path.abspath(anchor_path),
        "anchor_schema_version": ANCHOR_SCHEMA_VERSION,
    }))


def expected_fresh_anchor_bytes(registry_identity):
    body = {
        "schema_version": ANCHOR_SCHEMA_VERSION,
        "registry_identity": registry_identity,
        "document_sequence": 1,
        "programs": {},
    }
    document = dict(body)
    document["document_hash"] = sha256_hex(canonical_bytes(body))
    return canonical_bytes(document)


def mutex_path_for(anchor_path):
    return os.fspath(anchor_path) + ".lock"


# ---------------------------------------------------------------------------
# Production surface accessor
# ---------------------------------------------------------------------------

def production():
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
    function = getattr(module, "initialize_registry", None)
    if function is None:
        pytest.fail(
            "P1A production surface incomplete: %s defines no initialize_registry"
            % (PRODUCTION_MODULE,)
        )
    return function


def refusal_type():
    module = production()
    exception = getattr(module, "RegistryInitializationRefused", None)
    if exception is None:
        pytest.fail(
            "P1A production surface incomplete: %s defines no"
            " RegistryInitializationRefused" % (PRODUCTION_MODULE,)
        )
    return exception


def is_busy_operational_error(exc):
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if code is None:
        return False
    return (code & 0xFF) in (5, 6)


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
# Workspace observation
# ---------------------------------------------------------------------------

def digest_dir(directory):
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
    connection = sqlite3.connect(os.fspath(path), isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def read_kv(path, table, key_column, value_column):
    connection = raw_connect(path)
    try:
        rows = connection.execute(
            "SELECT %s, %s FROM %s ORDER BY %s"
            % (key_column, value_column, table, key_column)
        ).fetchall()
    finally:
        connection.close()
    return dict(rows)


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# The observation seam
# ---------------------------------------------------------------------------
#
# One ordered index space across both sqlite and filesystem operations, so an
# ordering claim spanning the two -- which is exactly what H2-A asserts -- is
# expressible without correlating two clocks.

class _Recorder:
    def __init__(self):
        self.entries = []
        self._lock = threading.Lock()

    def record(self, kind, target, payload):
        with self._lock:
            self.entries.append({
                "index": len(self.entries),
                "kind": kind,
                "target": target,
                "payload": payload,
            })

    def indices(self, target=None, kind=None, contains=None, lowered=False):
        found = []
        for entry in self.entries:
            if target is not None and entry["target"] != target:
                continue
            if kind is not None and entry["kind"] != kind:
                continue
            if contains is not None:
                haystack = entry["payload"]
                if lowered:
                    haystack = haystack.lower()
                if contains not in haystack:
                    continue
            found.append(entry["index"])
        return found

    def first(self, **kwargs):
        found = self.indices(**kwargs)
        return found[0] if found else None

    def render(self):
        return "\n".join(
            "%3d  %-9s %-8s %s"
            % (e["index"], e["kind"], e["target"], e["payload"][:110])
            for e in self.entries
        )


class _FakeCursor:
    """Stands in for a sqlite3 cursor when a PRAGMA result is substituted."""

    def __init__(self, rows):
        self._rows = [tuple(row) for row in rows]

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _TracedConnection:
    def __init__(self, real, recorder, target, substitute_integrity):
        self._real = real
        self._rec = recorder
        self._target = target
        self._substitute_integrity = substitute_integrity

    def __getattr__(self, name):
        return getattr(self._real, name)

    def execute(self, sql, *args, **kwargs):
        self._rec.record("execute", self._target, sql)
        if (self._substitute_integrity is not None
                and self._target == "registry"
                and INTEGRITY_STATEMENT_FRAGMENT in sql.lower()):
            # The one deliberate substitution.  Armed only by an explicit test.
            return _FakeCursor(self._substitute_integrity)
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
        return self._real.cursor(*args, **kwargs)

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)


class _Sqlite3Shim:
    def __init__(self, real, recorder, registry_path, substitute_integrity):
        self._real = real
        self._rec = recorder
        self._registry_path = os.path.abspath(os.fspath(registry_path))
        self._substitute_integrity = substitute_integrity

    def __getattr__(self, name):
        return getattr(self._real, name)

    def connect(self, database, *args, **kwargs):
        target = ("registry"
                  if os.path.abspath(os.fspath(database)) == self._registry_path
                  else "mutex")
        self._rec.record("connect", target, str(database))
        return _TracedConnection(
            self._real.connect(database, *args, **kwargs),
            self._rec, target, self._substitute_integrity,
        )


class _OsPathProxy:
    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)


class _OsShim:
    def __init__(self, real, recorder):
        self._real = real
        self._rec = recorder
        self.path = _OsPathProxy(real.path)

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


class _Installed:
    def __init__(self, module, recorder, registry_path, substitute_integrity):
        self.module = module
        self.rec = recorder
        self.registry_path = registry_path
        self.substitute_integrity = substitute_integrity
        self.saved = {}

    def __enter__(self):
        for name in ("sqlite3", "os"):
            real = getattr(self.module, name, None)
            if real is None:
                pytest.fail(
                    "observation seam unavailable: %s exposes no module-level %r"
                    % (PRODUCTION_MODULE, name)
                )
            self.saved[name] = real
        setattr(self.module, "sqlite3", _Sqlite3Shim(
            self.saved["sqlite3"], self.rec,
            self.registry_path, self.substitute_integrity,
        ))
        setattr(self.module, "os", _OsShim(self.saved["os"], self.rec))
        return self.rec

    def __exit__(self, *exc):
        for name, real in self.saved.items():
            setattr(self.module, name, real)
        return False


def observe(registry_path, substitute_integrity=None):
    """Install the seam.  ``substitute_integrity=None`` observes only."""
    return _Installed(production(), _Recorder(), registry_path,
                      substitute_integrity)


# ---------------------------------------------------------------------------
# Fixtures and state construction
# ---------------------------------------------------------------------------

@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    anchor = str(root / "trial_registry.anchor")
    return {
        "root": str(root),
        "registry": str(root / "trial_registry.sqlite3"),
        "anchor": anchor,
        "mutex": mutex_path_for(anchor),
    }


def seed_complete(workspace):
    """A genuine COMPLETE registry, built by the production initializer itself.

    Built with NO seam installed, so the starting state is never an artefact of
    the instrument.
    """
    outcome, payload = classify_outcome(
        initializer(), workspace["registry"], workspace["anchor"]
    )
    assert outcome == "COMPLETED", (
        "precondition: a fresh initialization must complete; got %s (%r)"
        % (outcome, payload)
    )
    return workspace


def break_to_pending(workspace, remove_anchor):
    """Return the workspace to S9 (anchor absent) or S10 (anchor present)."""
    connection = raw_connect(workspace["registry"])
    try:
        connection.execute(
            "UPDATE registry_state SET state_value = ? WHERE state_key = ?",
            (INIT_STATE_PENDING, INIT_STATE_KEY),
        )
    finally:
        connection.close()
    if remove_anchor:
        os.remove(workspace["anchor"])
    return workspace


def assert_refused_exactly(outcome, payload, label):
    refused = refusal_type()
    assert outcome == "INITIALIZATION_REFUSED", (
        "%s: expected a deterministic refusal, got %s (%r)"
        % (label, outcome, payload)
    )
    assert type(payload) is refused, (
        "%s: the refusal must be exactly RegistryInitializationRefused, got %r"
        % (label, type(payload))
    )
    assert not isinstance(payload, sqlite3.Error), (
        "%s: a sqlite3 exception must not escape a deterministic refusal" % (label,)
    )
    assert not isinstance(payload, OSError), (
        "%s: an OSError must not escape a deterministic refusal" % (label,)
    )
    assert payload.__cause__ is None, (
        "%s: refusals are raised `from None`; __cause__ was %r"
        % (label, payload.__cause__)
    )


def first_content_read(recorder):
    """Index of the first registry statement that relies on stored content.

    Schema, metadata, state and ledger reads all qualify.  This is what H1
    requires the integrity check to precede.
    """
    candidates = []
    for needle in ("sqlite_master", "registry_meta", "registry_state",
                   "trial_ledger"):
        candidates.extend(recorder.indices(target="registry", contains=needle))
    return min(candidates) if candidates else None


# ===========================================================================
# H1  --  PRAGMA integrity_check
# ===========================================================================

def test_p1a_h1a_complete_registry_failing_integrity_check_is_refused(workspace):
    """H1-A.  A COMPLETE registry that fails integrity_check must fail closed."""
    seed_complete(workspace)
    before_digest = digest_dir(workspace["root"])
    before_registry = file_identity(workspace["registry"])
    before_anchor = file_identity(workspace["anchor"])
    before_mutex = file_identity(workspace["mutex"])

    with observe(workspace["registry"], INTEGRITY_BAD_ROWS) as trace:
        outcome, payload = classify_outcome(
            initializer(), workspace["registry"], workspace["anchor"]
        )

    integrity = trace.indices(target="registry",
                              contains=INTEGRITY_STATEMENT_FRAGMENT,
                              lowered=True)
    assert integrity, (
        "H1: PRAGMA integrity_check was never executed on the registry."
        "  Schema equality is not a substitute for SQLite structural"
        " integrity.\n%s" % (trace.render(),)
    )

    # Once the check has failed, NOTHING may be read.  The ordering claim is
    # expressed here as an absence rather than as an index comparison, because
    # on this path a correct implementation performs no content read at all --
    # an index comparison would be unsatisfiable.  The positive ordering claim
    # lives in H1-C, where content reads do occur.
    content = first_content_read(trace)
    assert content is None, (
        "H1-A: nothing may be read from the registry once integrity_check has"
        " failed.  A content read occurred at index %r\n%s"
        % (content, trace.render())
    )

    assert_refused_exactly(outcome, payload, "H1-A non-ok integrity, COMPLETE")

    assert digest_dir(workspace["root"]) == before_digest, (
        "H1-A: the workspace must be byte-identical after the refusal"
    )
    assert file_identity(workspace["registry"]) == before_registry, (
        "H1-A: no repair may touch the registry"
    )
    assert file_identity(workspace["anchor"]) == before_anchor, (
        "H1-A: no repair may touch the anchor"
    )
    assert file_identity(workspace["mutex"]) == before_mutex, (
        "H1-A: no repair may touch the mutex"
    )


def test_p1a_h1b_pending_registry_failing_integrity_check_is_refused(workspace):
    """H1-B.  A PENDING registry that fails integrity_check must not recover."""
    seed_complete(workspace)
    break_to_pending(workspace, remove_anchor=True)
    assert not os.path.exists(workspace["anchor"])
    before_digest = digest_dir(workspace["root"])
    before_registry = file_identity(workspace["registry"])

    with observe(workspace["registry"], INTEGRITY_BAD_ROWS) as trace:
        outcome, payload = classify_outcome(
            initializer(), workspace["registry"], workspace["anchor"]
        )

    integrity = trace.indices(target="registry",
                              contains=INTEGRITY_STATEMENT_FRAGMENT,
                              lowered=True)
    assert integrity, (
        "H1: PRAGMA integrity_check was never executed on the PENDING"
        " registry\n%s" % (trace.render(),)
    )

    assert_refused_exactly(outcome, payload, "H1-B non-ok integrity, PENDING")

    assert not os.path.exists(workspace["anchor"]), (
        "H1-B: recovery must not create the anchor when integrity fails"
    )
    assert trace.indices(target="fs", kind="write") == [], (
        "H1-B: no anchor temporary file may be written\n%s" % (trace.render(),)
    )
    assert trace.indices(target="fs", kind="replace") == [], (
        "H1-B: os.replace must not run\n%s" % (trace.render(),)
    )
    assert trace.indices(target="registry", contains=INIT_STATE_COMPLETE) == [], (
        "H1-B: init_state must not be advanced\n%s" % (trace.render(),)
    )

    state = read_kv(workspace["registry"], "registry_state",
                    "state_key", "state_value")
    assert state == {INIT_STATE_KEY: INIT_STATE_PENDING}, (
        "H1-B: init_state must still be PENDING, found %r" % (state,)
    )
    assert digest_dir(workspace["root"]) == before_digest
    assert file_identity(workspace["registry"]) == before_registry


def test_p1a_h1c_ok_integrity_leaves_both_paths_available(workspace):
    """H1-C.  Exactly [('ok',)] must leave COMPLETE and PENDING working.

    The positive control.  A fail-closed check that also closes the door on
    healthy registries would satisfy H1-A and H1-B and still be worthless.
    """
    seed_complete(workspace)

    # --- COMPLETE: the strict no-op must remain available -----------------
    with observe(workspace["registry"], INTEGRITY_OK_ROWS) as trace:
        outcome, payload = classify_outcome(
            initializer(), workspace["registry"], workspace["anchor"]
        )
    integrity = trace.indices(target="registry",
                              contains=INTEGRITY_STATEMENT_FRAGMENT,
                              lowered=True)
    assert integrity, (
        "H1-C: integrity_check must run on the COMPLETE path\n%s"
        % (trace.render(),)
    )
    # The positive ordering claim.  This is the path where content IS read, so
    # it is the only path on which "integrity_check comes first" is observable.
    content = first_content_read(trace)
    assert content is not None, (
        "H1-C precondition: the COMPLETE path must read registry content, or"
        " the ordering claim below is vacuous\n%s" % (trace.render(),)
    )
    assert integrity[0] < content, (
        "H1: integrity_check must precede any reliance on schema, metadata,"
        " state or ledger contents.  integrity at %d, first content read at %d\n%s"
        % (integrity[0], content, trace.render())
    )
    assert outcome == "COMPLETED", (
        "H1-C: an ok integrity result must leave S11 available; got %s (%r)"
        % (outcome, payload)
    )
    assert os.fspath(payload) == os.fspath(workspace["registry"])

    # --- PENDING: recovery must remain available --------------------------
    break_to_pending(workspace, remove_anchor=True)
    with observe(workspace["registry"], INTEGRITY_OK_ROWS) as trace:
        outcome, payload = classify_outcome(
            initializer(), workspace["registry"], workspace["anchor"]
        )
    assert trace.indices(target="registry",
                         contains=INTEGRITY_STATEMENT_FRAGMENT,
                         lowered=True), (
        "H1-C: integrity_check must run on the PENDING path\n%s"
        % (trace.render(),)
    )
    assert outcome == "COMPLETED", (
        "H1-C: an ok integrity result must leave S9 recovery available;"
        " got %s (%r)" % (outcome, payload)
    )

    state = read_kv(workspace["registry"], "registry_state",
                    "state_key", "state_value")
    assert state == {INIT_STATE_KEY: INIT_STATE_COMPLETE}
    identity = expected_registry_identity(
        workspace["registry"], workspace["anchor"]
    )
    assert read_bytes(workspace["anchor"]) == expected_fresh_anchor_bytes(identity)
    survivors = [n for n in digest_dir(workspace["root"]) if n.endswith(".tmp")]
    assert survivors == [], "no temporary file may survive: %r" % (survivors,)


# ===========================================================================
# H2  --  S10 must re-execute I4 and I5
# ===========================================================================

def test_p1a_h2a_s10_recovery_performs_a_real_durable_rewrite(workspace):
    """H2-A.  S10 must re-run the whole durable chain, not just mark COMPLETE.

    Byte equality of the surviving anchor proves the CONTENT is right.  It does
    not prove the directory entry was ever durable -- a crash between os.replace
    and the directory fsync leaves exactly that state.  So the chain is re-run.
    """
    seed_complete(workspace)
    before_bytes = read_bytes(workspace["anchor"])
    break_to_pending(workspace, remove_anchor=False)
    assert os.path.exists(workspace["anchor"]), "S10 keeps the anchor"

    connection = raw_connect(workspace["registry"])
    try:
        ledger = connection.execute("SELECT COUNT(*) FROM trial_ledger").fetchall()
    finally:
        connection.close()
    assert int(ledger[0][0]) == 0, "S10 precondition: the ledger is empty"

    with observe(workspace["registry"]) as trace:
        outcome, payload = classify_outcome(
            initializer(), workspace["registry"], workspace["anchor"]
        )
    assert outcome == "COMPLETED", (
        "S10 must recover deterministically; got %s (%r)" % (outcome, payload)
    )

    # Five separately messaged claims, so a partial pass is visible.
    replace_indices = trace.indices(target="fs", kind="replace")
    assert replace_indices, (
        "H2 claim 3: S10 must re-execute I4 -- os.replace never ran, so the"
        " anchor was accepted on byte equality alone\n%s" % (trace.render(),)
    )
    replace_at = replace_indices[0]

    writes = trace.indices(target="fs", kind="write")
    assert writes and min(writes) < replace_at, (
        "H2 claim 1: a temporary file must be written before os.replace."
        "  writes %r, os.replace at %d\n%s" % (writes, replace_at, trace.render())
    )

    fsyncs = trace.indices(target="fs", kind="fsync")
    before_replace = [i for i in fsyncs if i < replace_at]
    after_replace = [i for i in fsyncs if i > replace_at]
    assert before_replace, (
        "H2 claim 2: the temporary file must be fsynced BEFORE os.replace."
        "  fsyncs %r, os.replace at %d\n%s" % (fsyncs, replace_at, trace.render())
    )
    assert after_replace, (
        "H2 claim 4: the containing DIRECTORY must be fsynced AFTER os.replace."
        "  fsyncs %r, os.replace at %d\n%s" % (fsyncs, replace_at, trace.render())
    )

    complete_writes = trace.indices(target="registry",
                                    contains=INIT_STATE_COMPLETE)
    assert complete_writes, (
        "S10 must end by writing COMPLETE\n%s" % (trace.render(),)
    )
    assert min(after_replace) < min(complete_writes), (
        "H2 claim 5: COMPLETE must be written only after the directory fsync."
        "  directory fsync at %d, COMPLETE at %d\n%s"
        % (min(after_replace), min(complete_writes), trace.render())
    )

    # The whole ordering, asserted as one chain so the report reads plainly.
    chain = (min(writes), min(before_replace), replace_at,
             min(after_replace), min(complete_writes))
    assert list(chain) == sorted(chain), (
        "H2: required order is temp write -> file fsync -> os.replace ->"
        " directory fsync -> COMPLETE; observed %r\n%s"
        % (chain, trace.render())
    )

    assert read_bytes(workspace["anchor"]) == before_bytes, (
        "H2: the S10 rewrite must be byte-identical"
    )
    state = read_kv(workspace["registry"], "registry_state",
                    "state_key", "state_value")
    assert state == {INIT_STATE_KEY: INIT_STATE_COMPLETE}
    survivors = [n for n in digest_dir(workspace["root"]) if n.endswith(".tmp")]
    assert survivors == [], "no temporary file may survive: %r" % (survivors,)


def test_p1a_h2b_s11_complete_remains_a_strict_no_op(workspace):
    """H2-B.  Making S10 rewrite must not make S11 rewrite.

    The obvious wrong fix for H2 is to rewrite the anchor unconditionally.  That
    would pass H2-A and silently destroy the S11 guarantee, so the two are
    asserted together.
    """
    seed_complete(workspace)
    before_digest = digest_dir(workspace["root"])
    before_registry = file_identity(workspace["registry"])
    before_anchor = file_identity(workspace["anchor"])
    before_mutex = file_identity(workspace["mutex"])
    before_meta = read_kv(workspace["registry"], "registry_meta",
                          "meta_key", "meta_value")

    with observe(workspace["registry"]) as trace:
        outcome, payload = classify_outcome(
            initializer(), workspace["registry"], workspace["anchor"]
        )
    assert outcome == "COMPLETED", (
        "S11 must complete as a no-op; got %s (%r)" % (outcome, payload)
    )

    assert trace.indices(target="fs", kind="write") == [], (
        "H2-B: S11 must not write an anchor temporary file\n%s" % (trace.render(),)
    )
    assert trace.indices(target="fs", kind="replace") == [], (
        "H2-B: S11 must not re-execute I4\n%s" % (trace.render(),)
    )
    assert trace.indices(target="fs", kind="fsync") == [], (
        "H2-B: S11 must perform no anchor fsync at all\n%s" % (trace.render(),)
    )
    assert trace.indices(target="registry", contains=INIT_STATE_COMPLETE) == [], (
        "H2-B: S11 must not re-execute I5\n%s" % (trace.render(),)
    )

    assert file_identity(workspace["anchor"]) == before_anchor, (
        "H2-B: the anchor's inode and mtime must not change."
        "  before=%r after=%r"
        % (before_anchor, file_identity(workspace["anchor"]))
    )
    assert file_identity(workspace["registry"]) == before_registry
    assert file_identity(workspace["mutex"]) == before_mutex
    assert digest_dir(workspace["root"]) == before_digest
    assert read_kv(workspace["registry"], "registry_meta",
                   "meta_key", "meta_value") == before_meta, (
        "H2-B: no second identity may be minted"
    )
    survivors = [n for n in digest_dir(workspace["root"]) if n.endswith(".tmp")]
    assert survivors == [], "S11 must create no temporary file: %r" % (survivors,)


# ===========================================================================
# H3  --  the translating refusal path must suppress its context
# ===========================================================================

def test_p1a_h3_translated_refusal_suppresses_its_context(workspace):
    """H3.  `raise ... from None` must be observable, not merely intended.

    The GREEN battery disclosed that a `__cause__ is None` assertion cannot
    distinguish `raise X from None` from a bare `raise X`, because neither sets
    __cause__.  __suppress_context__ does distinguish them, and that is the only
    new claim here.

    S6 -- malformed mutex bytes -- is used because it is unambiguously a
    TRANSLATING path: an underlying sqlite3.DatabaseError is replaced.  The
    exception type, the message contract and the public API are untouched.
    """
    with open(workspace["mutex"], "wb") as handle:
        handle.write(b"this is not a sqlite database" * 8)
    before_digest = digest_dir(workspace["root"])

    outcome, payload = classify_outcome(
        initializer(), workspace["registry"], workspace["anchor"]
    )
    assert_refused_exactly(outcome, payload, "H3 S6 malformed mutex")

    assert payload.__context__ is not None, (
        "H3 precondition: this must be a TRANSLATING path, so an underlying"
        " exception must be present as __context__.  Found none, which means"
        " the test is no longer exercising what it claims to."
    )
    assert payload.__suppress_context__ is True, (
        "H3: a translated refusal must be raised `from None`, which sets"
        " __suppress_context__.  It was False, so the underlying exception"
        " would surface in the traceback."
    )

    # The public contract is unchanged: no message is asserted or parsed.
    assert digest_dir(workspace["root"]) == before_digest, (
        "H3: the workspace must be byte-identical after the refusal"
    )
    assert not os.path.exists(workspace["registry"]), (
        "H3: no registry may be created on a mutex refusal path"
    )
