"""WP5-0A2-R2 · P1B · TERMINAL-EVENT TIMING TESTS.

CONTRACT         : MIE-PHASE5-P1B-TIMING-INSTRUMENTATION-R1-REISSUE-001
BASELINE COMMIT  : f04e11c468f71327d1117a43a9096eab8e6eb25b
SEAM UNDER TEST  : tests/test_phase5_trial_registry_p1a_hardening.py
PRODUCTION       : app/research/phase5_trial_registry.py -- READ-ONLY, NEVER EDITED
                     sha256 5670fcb50f3cc82f784fa0a45a055461063234bc05721c695b3c80625ce0c030

WHY THIS MODULE EXISTS.  MIE_P1B_EVENT_TIMING_AUDIT_001 determined
STOP_TRACE_SEAM_TIMING_INSUFFICIENT: all twelve wrappers record strictly BEFORE
the delegated call, so an event proves only that control reached the wrapper.
A failed operation leaves an entry byte-identical in shape to a successful one,
and successor-based inference is unsound wherever a ``finally`` or ``except``
clause supplies the successor.  These tests specify the opt-in terminal-event
extension that makes success, failure and completion observable.

SCOPE LIMIT, STATED EXPLICITLY AND NOT CLOSED HERE.  Operations that the seam
never wrapped remain outside it and acquire no terminal event: every
``os.path.*`` call (routed through ``_OsPathProxy``), ``os.fspath``, the builtin
``open()`` used for the anchor content read, and any cursor method obtained from
``connection.cursor()``.  Their absence is a scope limit of any boundary set
derived from this trace.  Recording them is a separate decision and is NOT part
of this work package.

NOT IN SCOPE.  No crash boundary is derived.  No trace is captured.  No crash
injection, no signal handling, no process kill and no child process at all.
P2 remains unauthorized.

INDEPENDENCE RULE.  Every expected literal is restated here.  Nothing is
imported from the seam to build an expected value; only the objects under test
are imported from it.  The frozen module tests/test_phase5_trial_registry.py is
neither imported nor relied upon.
"""

import hashlib
import importlib
import importlib.util
import os
import sys

import pytest


# ---------------------------------------------------------------------------
# Frozen literals -- restated, never imported
# ---------------------------------------------------------------------------

PRODUCTION_MODULE = "app.research.phase5_trial_registry"
PRODUCTION_RELATIVE = os.path.join("app", "research", "phase5_trial_registry.py")
PRODUCTION_SHA256 = (
    "5670fcb50f3cc82f784fa0a45a055461063234bc05721c695b3c80625ce0c030"
)

SEAM_MODULE = "test_phase5_trial_registry_p1a_hardening"

INTEGRITY_STATEMENT_FRAGMENT = "integrity_check"
INTEGRITY_OK_ROWS = [("ok",)]

# The exact terminal-event vocabulary this contract specifies.
OK_SUFFIX = ":ok"
ERR_SUFFIX = ":err"
ERROR_EVIDENCE_PREFIX = "error="
FD_EVIDENCE_PREFIX = "fd="
BYTES_EVIDENCE_PREFIX = "bytes="
SUBSTITUTED_EVIDENCE = "substituted"

# The complete wrapper inventory, exactly as the committed seam defines it and
# as MIE_P1B_EVENT_TIMING_AUDIT_001 section 6 enumerates it.  Twelve entries.
CONNECTION_WRAPPERS = ("execute", "executemany", "commit", "rollback", "close")
OS_WRAPPERS = ("open", "write", "fsync", "close", "replace", "rename")
WRAPPER_INVENTORY_SIZE = 1 + len(CONNECTION_WRAPPERS) + len(OS_WRAPPERS)


# ---------------------------------------------------------------------------
# Module accessors
# ---------------------------------------------------------------------------

def _load_seam():
    """The committed seam module, however pytest happens to have imported it."""
    existing = sys.modules.get(SEAM_MODULE)
    if existing is not None:
        return existing
    try:
        return importlib.import_module(SEAM_MODULE)
    except ImportError:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            SEAM_MODULE + ".py")
        spec = importlib.util.spec_from_file_location(SEAM_MODULE, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[SEAM_MODULE] = module
        spec.loader.exec_module(module)
        return module


seam = _load_seam()


def production():
    return importlib.import_module(PRODUCTION_MODULE)


def initializer():
    module = production()
    function = getattr(module, "initialize_registry", None)
    if function is None:
        pytest.fail("P1A production surface incomplete: no initialize_registry")
    return function


def repository_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


# ---------------------------------------------------------------------------
# Controllable delegates
#
# The wrappers are exercised against stand-ins rather than against SQLite, so a
# failing delegate, a partial write and an in-progress delegate are all
# expressible deterministically, with no fault injection reaching real state.
# ---------------------------------------------------------------------------

class _Delegate:
    """Base stand-in: records calls, returns a fixed result or raises."""

    def __init__(self, results=None, failures=None):
        self.results = dict(results or {})
        self.failures = dict(failures or {})
        self.calls = []
        self.observer = None

    def _dispatch(self, name, args):
        self.calls.append((name, args))
        if self.observer is not None:
            self.observer(name, args)
        if name in self.failures:
            raise self.failures[name]
        return self.results.get(name, "<%s-result>" % (name,))


class _StubConnection(_Delegate):
    def execute(self, sql, *a, **k):
        return self._dispatch("execute", (sql,) + a)

    def executemany(self, sql, *a, **k):
        return self._dispatch("executemany", (sql,) + a)

    def commit(self):
        return self._dispatch("commit", ())

    def rollback(self):
        return self._dispatch("rollback", ())

    def close(self):
        return self._dispatch("close", ())


class _StubOs(_Delegate):
    def __init__(self, results=None, failures=None):
        _Delegate.__init__(self, results, failures)
        # _OsShim.__init__ reads real.path, so it must be present.
        self.path = os.path

    def open(self, path, *a, **k):
        return self._dispatch("open", (path,) + a)

    def write(self, fd, data):
        return self._dispatch("write", (fd, data))

    def fsync(self, fd):
        return self._dispatch("fsync", (fd,))

    def close(self, fd):
        return self._dispatch("close", (fd,))

    def replace(self, src, dst, *a, **k):
        return self._dispatch("replace", (src, dst) + a)

    def rename(self, src, dst, *a, **k):
        return self._dispatch("rename", (src, dst) + a)


class _StubSqlite3(_Delegate):
    def connect(self, database, *a, **k):
        return self._dispatch("connect", (database,) + a)


def recorder(terminal_events=True):
    """A recorder with terminal events armed.

    Constructed inside test bodies, never in a fixture, so that its absence on
    an uninstrumented seam is reported as a test FAILURE rather than as a
    collection or setup ERROR.
    """
    return seam._Recorder(terminal_events=terminal_events)


def traced_connection(rec, real, target="registry", substitute=None):
    return seam._TracedConnection(real, rec, target, substitute)


def os_shim(rec, real):
    return seam._OsShim(real, rec)


def sqlite_shim(rec, real, registry_path, substitute=None):
    return seam._Sqlite3Shim(real, rec, registry_path, substitute)


def call_connection_wrapper(name, connection):
    if name == "execute":
        return connection.execute("SELECT 1")
    if name == "executemany":
        return connection.executemany("INSERT INTO t VALUES (?)", [(1,)])
    if name == "commit":
        return connection.commit()
    if name == "rollback":
        return connection.rollback()
    if name == "close":
        return connection.close()
    raise AssertionError("unknown connection wrapper %r" % (name,))


def call_os_wrapper(name, shim):
    if name == "open":
        return shim.open("/nonexistent/probe", 0)
    if name == "write":
        return shim.write(7, b"abcd")
    if name == "fsync":
        return shim.fsync(7)
    if name == "close":
        return shim.close(7)
    if name == "replace":
        return shim.replace("/nonexistent/a", "/nonexistent/b")
    if name == "rename":
        return shim.rename("/nonexistent/a", "/nonexistent/b")
    raise AssertionError("unknown os wrapper %r" % (name,))


# ---------------------------------------------------------------------------
# Event vocabulary helpers
# ---------------------------------------------------------------------------

def is_terminal(entry):
    return "seq" in entry


def pre_events(entries):
    return [e for e in entries if not is_terminal(e)]


def terminal_events(entries):
    return [e for e in entries if is_terminal(e)]


def legacy_shape(entries):
    """The historical stream as (kind, target, payload) triples."""
    return [(e["kind"], e["target"], e["payload"]) for e in pre_events(entries)]


def assert_pairing(entries, label):
    """Every pairing invariant this contract requires, in one place."""
    pre = {}
    for entry in entries:
        if not is_terminal(entry):
            pre[entry["index"]] = entry
    paired = {}
    for entry in terminal_events(entries):
        seq = entry["seq"]
        assert seq in pre, (
            "%s: orphan terminal event %r references no pre-event" % (label, entry)
        )
        assert seq < entry["index"], (
            "%s: terminal event %r does not follow its pre-event" % (label, entry)
        )
        assert seq not in paired, (
            "%s: pre-event %d received a second terminal event %r"
            % (label, seq, entry)
        )
        paired[seq] = entry
        base = pre[seq]
        assert entry["kind"] in (base["kind"] + OK_SUFFIX,
                                 base["kind"] + ERR_SUFFIX), (
            "%s: terminal kind %r does not name its pre-event kind %r"
            % (label, entry["kind"], base["kind"])
        )
        assert entry["target"] == base["target"], (
            "%s: terminal event %r lost the normalized target %r of its"
            " pre-event" % (label, entry, base["target"])
        )
    return paired


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

def make_workspace(root):
    os.mkdir(root)
    anchor = os.path.join(root, "trial_registry.anchor")
    return {
        "root": root,
        "registry": os.path.join(root, "trial_registry.sqlite3"),
        "anchor": anchor,
        "mutex": anchor + ".lock",
    }


@pytest.fixture()
def workspace(tmp_path):
    return make_workspace(str(tmp_path / "ws"))


def normalise(text, workspace):
    """Replace the absolute temporary root with a stable token.

    The same textual normalisation the P1B preflight specifies, so two runs in
    different temporary directories are comparable.
    """
    return text.replace(workspace["root"], "<ROOT>")


def seed_complete(workspace):
    """A genuine COMPLETE registry, built with NO seam installed."""
    result = initializer()(workspace["registry"], workspace["anchor"])
    assert os.fspath(result) == os.fspath(workspace["registry"])
    return workspace


# ===========================================================================
# 1-2  --  the legacy stream is the default and is preserved exactly
# ===========================================================================

def test_p1b_01_omitted_option_preserves_the_legacy_stream(tmp_path):
    """1.  Calls that omit the option emit pre-events only."""
    ws = make_workspace(str(tmp_path / "omitted"))
    with seam.observe(ws["registry"]) as trace:
        result = initializer()(ws["registry"], ws["anchor"])

    assert os.fspath(result) == os.fspath(ws["registry"]), (
        "the return value must be unchanged when the option is omitted"
    )
    assert terminal_events(trace.entries) == [], (
        "omitting terminal_events must emit no terminal event\n%s"
        % (trace.render(),)
    )
    for entry in trace.entries:
        assert sorted(entry) == ["index", "kind", "payload", "target"], (
            "the historical entry carries exactly four fields; found %r"
            % (sorted(entry),)
        )
    assert trace.entries, "precondition: the run must record something"


def test_p1b_02_explicit_false_preserves_the_legacy_stream(tmp_path):
    """2.  terminal_events=False is byte-identical to omitting it."""
    omitted_ws = make_workspace(str(tmp_path / "a"))
    with seam.observe(omitted_ws["registry"]) as omitted:
        initializer()(omitted_ws["registry"], omitted_ws["anchor"])

    false_ws = make_workspace(str(tmp_path / "b"))
    with seam.observe(false_ws["registry"], terminal_events=False) as disabled:
        result = initializer()(false_ws["registry"], false_ws["anchor"])

    assert os.fspath(result) == os.fspath(false_ws["registry"])
    assert terminal_events(disabled.entries) == [], (
        "terminal_events=False must emit no terminal event\n%s"
        % (disabled.render(),)
    )
    assert len(disabled.entries) == len(omitted.entries), (
        "event count must be identical: omitted %d, False %d"
        % (len(omitted.entries), len(disabled.entries))
    )
    assert (normalise(disabled.render(), false_ws)
            == normalise(omitted.render(), omitted_ws)), (
        "terminal_events=False must render exactly as the legacy stream"
    )


def test_p1b_02b_enabling_does_not_disturb_the_pre_event_stream(tmp_path):
    """2 (continued).  Enabling adds events; it changes none of the old ones."""
    off_ws = make_workspace(str(tmp_path / "off"))
    with seam.observe(off_ws["registry"], terminal_events=False) as off:
        initializer()(off_ws["registry"], off_ws["anchor"])

    on_ws = make_workspace(str(tmp_path / "on"))
    with seam.observe(on_ws["registry"], terminal_events=True) as on:
        initializer()(on_ws["registry"], on_ws["anchor"])

    off_shape = [(k, t, normalise(p, off_ws)) for k, t, p in legacy_shape(off.entries)]
    on_shape = [(k, t, normalise(p, on_ws)) for k, t, p in legacy_shape(on.entries)]
    assert on_shape == off_shape, (
        "the pre-event subsequence must be identical with the option on and off"
    )
    assert terminal_events(on.entries), (
        "precondition: the opt-in run must actually emit terminal events"
    )


# ===========================================================================
# 3-4  --  every wrapper pairs, on success and on failure
# ===========================================================================

def test_p1b_03_every_successful_wrapper_emits_one_pre_event_and_one_ok():
    """3.  All twelve wrappers, success path."""
    assert WRAPPER_INVENTORY_SIZE == 12, (
        "the wrapper inventory is twelve: connect, five connection methods and"
        " six os methods"
    )

    for name in CONNECTION_WRAPPERS:
        rec = recorder()
        connection = traced_connection(rec, _StubConnection())
        call_connection_wrapper(name, connection)
        kinds = [e["kind"] for e in rec.entries]
        assert kinds == [name, name + OK_SUFFIX], (
            "connection wrapper %r: expected one pre-event and one :ok, got %r"
            % (name, kinds)
        )
        assert_pairing(rec.entries, "connection." + name)

    for name in OS_WRAPPERS:
        rec = recorder()
        shim = os_shim(rec, _StubOs(results={"open": 11, "write": 4}))
        call_os_wrapper(name, shim)
        kinds = [e["kind"] for e in rec.entries]
        assert kinds == [name, name + OK_SUFFIX], (
            "os wrapper %r: expected one pre-event and one :ok, got %r"
            % (name, kinds)
        )
        assert_pairing(rec.entries, "os." + name)

    rec = recorder()
    shim = sqlite_shim(rec, _StubSqlite3(results={"connect": _StubConnection()}),
                       "/nonexistent/registry.sqlite3")
    connection = shim.connect("/nonexistent/registry.sqlite3")
    kinds = [e["kind"] for e in rec.entries]
    assert kinds == ["connect", "connect" + OK_SUFFIX], (
        "connect: expected one pre-event and one :ok, got %r" % (kinds,)
    )
    assert isinstance(connection, seam._TracedConnection), (
        "connect must still return a traced connection; passivity is unchanged"
    )
    assert_pairing(rec.entries, "connect")


def test_p1b_04_every_failing_wrapper_emits_one_pre_event_and_one_err():
    """4.  All twelve wrappers, failure path."""
    for name in CONNECTION_WRAPPERS:
        rec = recorder()
        boom = RuntimeError("delegate refused")
        connection = traced_connection(rec, _StubConnection(failures={name: boom}))
        with pytest.raises(RuntimeError):
            call_connection_wrapper(name, connection)
        kinds = [e["kind"] for e in rec.entries]
        assert kinds == [name, name + ERR_SUFFIX], (
            "connection wrapper %r: expected one pre-event and one :err, got %r"
            % (name, kinds)
        )
        assert_pairing(rec.entries, "connection." + name)

    for name in OS_WRAPPERS:
        rec = recorder()
        boom = OSError("delegate refused")
        shim = os_shim(rec, _StubOs(failures={name: boom}))
        with pytest.raises(OSError):
            call_os_wrapper(name, shim)
        kinds = [e["kind"] for e in rec.entries]
        assert kinds == [name, name + ERR_SUFFIX], (
            "os wrapper %r: expected one pre-event and one :err, got %r"
            % (name, kinds)
        )
        assert_pairing(rec.entries, "os." + name)

    rec = recorder()
    shim = sqlite_shim(rec, _StubSqlite3(failures={"connect": OSError("no")}),
                       "/nonexistent/registry.sqlite3")
    with pytest.raises(OSError):
        shim.connect("/nonexistent/registry.sqlite3")
    kinds = [e["kind"] for e in rec.entries]
    assert kinds == ["connect", "connect" + ERR_SUFFIX], (
        "connect: expected one pre-event and one :err, got %r" % (kinds,)
    )
    assert_pairing(rec.entries, "connect")


# ===========================================================================
# 5-7  --  pairing invariants over a real initialization trace
# ===========================================================================

def test_p1b_05_every_terminal_event_references_an_earlier_pre_event(workspace):
    """5.  seq names a pre-event, and that pre-event precedes it."""
    with seam.observe(workspace["registry"], terminal_events=True) as trace:
        initializer()(workspace["registry"], workspace["anchor"])
    paired = assert_pairing(trace.entries, "fresh initialization")
    assert paired, "precondition: the run must produce terminal events"
    for entry in terminal_events(trace.entries):
        assert isinstance(entry["seq"], int), (
            "seq must be the integer sequence number of the pre-event, got %r"
            % (entry["seq"],)
        )


def test_p1b_06_no_orphan_terminal_event(workspace):
    """6.  No terminal event references a sequence that does not exist."""
    with seam.observe(workspace["registry"], terminal_events=True) as trace:
        initializer()(workspace["registry"], workspace["anchor"])
    indices = set(e["index"] for e in pre_events(trace.entries))
    orphans = [e for e in terminal_events(trace.entries)
               if e["seq"] not in indices]
    assert orphans == [], "orphan terminal events: %r\n%s" % (orphans,
                                                             trace.render())


def test_p1b_07_no_pre_event_receives_two_terminal_events(workspace):
    """7.  A pre-event is resolved at most once."""
    with seam.observe(workspace["registry"], terminal_events=True) as trace:
        initializer()(workspace["registry"], workspace["anchor"])
    seen = {}
    duplicates = []
    for entry in terminal_events(trace.entries):
        if entry["seq"] in seen:
            duplicates.append((seen[entry["seq"]], entry))
        seen[entry["seq"]] = entry
    assert duplicates == [], (
        "pre-events resolved twice: %r\n%s" % (duplicates, trace.render())
    )


# ===========================================================================
# 8-9  --  successful scalar evidence
# ===========================================================================

def test_p1b_08_os_open_ok_records_the_real_returned_descriptor(tmp_path):
    """8.  os.open:ok carries the descriptor os.open actually returned."""
    target = str(tmp_path / "probe.bin")
    rec = recorder()
    shim = os_shim(rec, os)          # the real os module, a real descriptor
    fd = shim.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        assert isinstance(fd, int)
        kinds = [e["kind"] for e in rec.entries]
        assert kinds == ["open", "open" + OK_SUFFIX], kinds
        terminal = rec.entries[1]
        assert terminal["payload"] == "%s%d" % (FD_EVIDENCE_PREFIX, fd), (
            "os.open:ok must record the returned descriptor %d, got %r"
            % (fd, terminal["payload"])
        )
        assert terminal["target"] == "fs"
    finally:
        os.close(fd)


def test_p1b_09_os_write_ok_records_the_actual_returned_byte_count():
    """9.  os.write:ok carries the returned count, partial writes included."""
    # A short write: ten bytes offered, three accepted.  This is the case the
    # pre-event payload cannot express, because it records len(data).
    rec = recorder()
    shim = os_shim(rec, _StubOs(results={"write": 3}))
    written = shim.write(7, b"0123456789")
    assert written == 3, "the delegate's return value must be passed through"
    kinds = [e["kind"] for e in rec.entries]
    assert kinds == ["write", "write" + OK_SUFFIX], kinds
    assert rec.entries[0]["payload"] == "fd=7 bytes=10", (
        "the pre-event still records the REQUESTED length, unchanged: %r"
        % (rec.entries[0]["payload"],)
    )
    assert rec.entries[1]["payload"] == "%s3" % (BYTES_EVIDENCE_PREFIX,), (
        "write:ok must record the ACCEPTED count 3, got %r"
        % (rec.entries[1]["payload"],)
    )

    # And the full-write case, against the real os module.
    rec = recorder()
    shim = os_shim(rec, _StubOs(results={"write": 10}))
    assert shim.write(7, b"0123456789") == 10
    assert rec.entries[1]["payload"] == "%s10" % (BYTES_EVIDENCE_PREFIX,)


# ===========================================================================
# 10-11  --  error evidence
# ===========================================================================

def test_p1b_10_exceptions_are_reraised_unchanged():
    """10.  Identity, type and arguments all survive the instrument."""
    boom = OSError(28, "No space left on device")
    rec = recorder()
    shim = os_shim(rec, _StubOs(failures={"fsync": boom}))
    with pytest.raises(OSError) as excinfo:
        shim.fsync(7)
    assert excinfo.value is boom, "the original exception object must be re-raised"
    assert type(excinfo.value) is OSError
    assert excinfo.value.args == (28, "No space left on device")
    assert excinfo.value.errno == 28

    # A bare `raise` must not chain the exception to itself.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is not boom


def test_p1b_11_error_evidence_carries_the_type_but_not_the_message():
    """11.  The exception type is recorded; the message never is."""
    secret = "sensitive-diagnostic-text-9f3c"
    boom = RuntimeError(secret)
    rec = recorder()
    connection = traced_connection(rec, _StubConnection(failures={"commit": boom}))
    with pytest.raises(RuntimeError):
        connection.commit()

    terminal = rec.entries[1]
    assert terminal["kind"] == "commit" + ERR_SUFFIX
    assert terminal["payload"] == "%sRuntimeError" % (ERROR_EVIDENCE_PREFIX,), (
        "error evidence must be the type name alone, got %r"
        % (terminal["payload"],)
    )
    for entry in rec.entries:
        assert secret not in entry["payload"], (
            "the exception message must never reach the trace: %r"
            % (entry["payload"],)
        )
    assert "0x" not in terminal["payload"], (
        "no address or unstable representation may be recorded"
    )


# ===========================================================================
# 12-13  --  ordering under a controlled and an interleaved delegate
# ===========================================================================

def test_p1b_12_no_ok_exists_while_the_delegate_is_still_running():
    """12.  A pre-event without a terminal event means IN FLIGHT, not success."""
    observed = {}

    def watcher(name, args):
        # Sampled from inside the delegate, before it has returned.
        observed["during"] = [dict(e) for e in rec.entries]

    rec = recorder()
    stub = _StubOs()
    stub.observer = watcher
    shim = os_shim(rec, stub)
    shim.fsync(7)

    during = observed["during"]
    assert len(during) == 1, (
        "exactly the pre-event may exist while the delegate runs, found %r"
        % (during,)
    )
    assert during[0]["kind"] == "fsync"
    assert terminal_events(during) == [], (
        "no terminal event may exist before the delegate returns: %r" % (during,)
    )
    assert [e["kind"] for e in rec.entries] == ["fsync", "fsync" + OK_SUFFIX], (
        "the :ok must appear only after the delegate returned"
    )


def test_p1b_13_interleaved_operations_pair_by_sequence_not_adjacency():
    """13.  A nested operation must not steal the outer operation's terminal."""
    rec = recorder()
    os_stub = _StubOs()
    shim = os_shim(rec, os_stub)

    def nested(name, args):
        # A second wrapped operation runs to completion inside the first.
        shim.fsync(9)

    connection_stub = _StubConnection()
    connection_stub.observer = nested
    connection = traced_connection(rec, connection_stub)
    connection.execute("SELECT 1")

    kinds = [e["kind"] for e in rec.entries]
    assert kinds == ["execute", "fsync", "fsync" + OK_SUFFIX,
                     "execute" + OK_SUFFIX], (
        "expected the inner operation to resolve first, got %r" % (kinds,)
    )
    paired = assert_pairing(rec.entries, "interleaved")
    assert paired[0]["kind"] == "execute" + OK_SUFFIX, (
        "the outer pre-event at seq 0 must be resolved by the outer :ok"
    )
    assert paired[1]["kind"] == "fsync" + OK_SUFFIX, (
        "the inner pre-event at seq 1 must be resolved by the inner :ok"
    )
    # Adjacency alone would have paired execute with fsync:ok.
    assert paired[0]["index"] == 3 and paired[1]["index"] == 2


# ===========================================================================
# 14-15  --  the seam remains passive
# ===========================================================================

def test_p1b_14_production_attributes_are_restored_after_success(workspace):
    """14.  sqlite3 and os are the real modules again after a clean run."""
    module = production()
    real_sqlite3 = module.sqlite3
    real_os = module.os
    with seam.observe(workspace["registry"], terminal_events=True) as trace:
        initializer()(workspace["registry"], workspace["anchor"])
    assert module.sqlite3 is real_sqlite3, "sqlite3 must be restored"
    assert module.os is real_os, "os must be restored"
    assert terminal_events(trace.entries), "precondition: terminal events armed"


def test_p1b_15_production_attributes_are_restored_after_failure(workspace):
    """15.  The restoration must not depend on the block succeeding."""
    module = production()
    real_sqlite3 = module.sqlite3
    real_os = module.os
    sentinel = RuntimeError("deliberate, inside the observed block")
    with pytest.raises(RuntimeError) as excinfo:
        with seam.observe(workspace["registry"], terminal_events=True):
            initializer()(workspace["registry"], workspace["anchor"])
            raise sentinel
    assert excinfo.value is sentinel
    assert module.sqlite3 is real_sqlite3, "sqlite3 must be restored on failure"
    assert module.os is real_os, "os must be restored on failure"


# ===========================================================================
# 16-17  --  determinism and the substitution path
# ===========================================================================

def test_p1b_16_two_equivalent_opt_in_runs_render_identically(tmp_path):
    """16.  Canonical output is determined by the operations, not the run."""
    first = make_workspace(str(tmp_path / "run1"))
    with seam.observe(first["registry"], terminal_events=True) as trace1:
        initializer()(first["registry"], first["anchor"])

    second = make_workspace(str(tmp_path / "run2"))
    with seam.observe(second["registry"], terminal_events=True) as trace2:
        initializer()(second["registry"], second["anchor"])

    rendered1 = normalise(trace1.render(), first)
    rendered2 = normalise(trace2.render(), second)
    assert rendered1 == rendered2, (
        "two equivalent opt-in runs must render identically after path"
        " normalisation\n--- run 1 ---\n%s\n--- run 2 ---\n%s"
        % (rendered1, rendered2)
    )
    # R2.  The serialized artifact must carry the pairing key.  R1 kept `seq`
    # in memory only, which lost pairing for anyone holding the rendered trace.
    assert SEQ_TOKEN in rendered1, (
        "the rendered trace must serialize the pairing key\n%s" % (rendered1,)
    )


def test_p1b_17_the_substitution_path_produces_explicit_terminal_evidence(workspace):
    """17.  A substituted result must never claim that SQLite executed it."""
    seed_complete(workspace)
    with seam.observe(workspace["registry"], INTEGRITY_OK_ROWS,
                      terminal_events=True) as trace:
        result = initializer()(workspace["registry"], workspace["anchor"])
    assert os.fspath(result) == os.fspath(workspace["registry"])

    integrity = [e for e in pre_events(trace.entries)
                 if e["target"] == "registry"
                 and INTEGRITY_STATEMENT_FRAGMENT in e["payload"].lower()]
    assert integrity, (
        "precondition: the substitution path must have been taken\n%s"
        % (trace.render(),)
    )
    paired = assert_pairing(trace.entries, "substituted integrity_check")
    for entry in integrity:
        terminal = paired.get(entry["index"])
        assert terminal is not None, (
            "the substituted execute must still be resolved by a terminal event"
        )
        assert terminal["kind"] == "execute" + OK_SUFFIX
        assert terminal["payload"] == SUBSTITUTED_EVIDENCE, (
            "substituted completion must be identified deterministically as %r,"
            " got %r" % (SUBSTITUTED_EVIDENCE, terminal["payload"])
        )


# ===========================================================================
# 18-20  --  the option, the production bytes, and the standing prohibition
# ===========================================================================

def test_p1b_18_non_boolean_option_values_are_rejected(workspace):
    """18.  Only True and False are accepted; 1 and 0 are not booleans."""
    for value in (1, 0, "True", "", None, [], object()):
        with pytest.raises(TypeError):
            seam.observe(workspace["registry"], terminal_events=value)
    # And the two acceptable values are accepted.
    for value in (True, False):
        seam.observe(workspace["registry"], terminal_events=value)


def test_p1b_19_production_source_bytes_remain_unchanged():
    """19.  This work package modifies no production code."""
    path = os.path.join(repository_root(), PRODUCTION_RELATIVE)
    assert os.path.isfile(path), "production module missing at %s" % (path,)
    assert sha256_file(path) == PRODUCTION_SHA256, (
        "app/research/phase5_trial_registry.py must remain byte-identical to"
        " the committed blob %s" % (PRODUCTION_SHA256,)
    )


def test_p1b_20_this_module_uses_no_crash_injection():
    """20.  No signal handling, kill, child process or process termination.

    The tokens are assembled from fragments so that this assertion cannot match
    its own source text.
    """
    forbidden = (
        "os." + "kill",
        "os." + "_exit",
        "os." + "abort",
        "sig" + "nal.",
        "SIG" + "KILL",
        "SIG" + "TERM",
        "sub" + "process",
        "multi" + "processing",
        "os." + "fork",
    )
    with open(os.path.abspath(__file__), "r", encoding="utf-8") as handle:
        source = handle.read()
    found = [token for token in forbidden if token in source]
    assert found == [], (
        "this module must contain no crash-injection mechanism; found %r"
        % (found,)
    )


# ===========================================================================
# R2  --  THE SERIALIZATION CONTRACT
#
# MIE-PHASE5-P1B-TIMING-INSTRUMENTATION-R2-001, decision
# STOP_TRACE_ARTIFACT_PAIRING_LOST.
#
# R1 recorded the pre-operation index in the in-memory `seq` field but
# `_Recorder.render()` omitted it.  The authoritative trace artifact is built
# from `render()` alone, so a reader holding only that artifact could not pair
# an interleaved terminal event with the operation it completes.
#
# EVERY TEST BELOW PARSES THE BYTES RETURNED BY `render()` AND NOTHING ELSE.
# `recorder.entries`, the entry dictionaries, the private `seq` field and every
# other in-memory representation are deliberately NOT consulted here: a proof
# that reads them would prove nothing about the artifact.
# ===========================================================================

SEQ_TOKEN = "seq="


class TraceContractError(AssertionError):
    """The rendered trace violates the serialization contract."""


def _split_rendered_line(line):
    """index, kind, target and the remainder, from one rendered line."""
    parts = line.split(None, 3)
    if len(parts) < 3:
        raise TraceContractError("malformed trace line: %r" % (line,))
    remainder = parts[3] if len(parts) == 4 else ""
    return parts[0], parts[1], parts[2], remainder


def parse_rendered_trace(text):
    """Parse rendered bytes into records, failing closed on any violation.

    Fails closed -- raising TraceContractError rather than returning a partial
    or guessed result -- on a malformed line, a non-integer sequence, a
    negative sequence, a missing sequence on a terminal event, more than one
    serialized sequence on a line, or a sequence on a pre-operation event.
    """
    records = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        index_text, kind, target, remainder = _split_rendered_line(line)
        if not index_text.isdigit():
            raise TraceContractError(
                "event index must be a decimal integer: %r" % (line,)
            )
        occurrences = line.count(SEQ_TOKEN)
        terminal = kind.endswith(OK_SUFFIX) or kind.endswith(ERR_SUFFIX)
        if terminal:
            if occurrences != 1:
                raise TraceContractError(
                    "a terminal event must serialize exactly one %r field;"
                    " found %d in %r" % (SEQ_TOKEN, occurrences, line)
                )
            if not remainder.startswith(SEQ_TOKEN):
                raise TraceContractError(
                    "%r must precede the payload, so payload truncation can"
                    " never remove it: %r" % (SEQ_TOKEN, line)
                )
            field, _, payload = remainder.partition(" ")
            digits = field[len(SEQ_TOKEN):]
            # isdigit rejects "-1", "" and "abc" alike: no sign, no whitespace.
            if not digits.isdigit():
                raise TraceContractError(
                    "%r must be a non-negative decimal integer, got %r in %r"
                    % (SEQ_TOKEN, digits, line)
                )
            seq = int(digits)
        else:
            if occurrences != 0:
                raise TraceContractError(
                    "a pre-operation event must serialize no %r field: %r"
                    % (SEQ_TOKEN, line)
                )
            seq = None
            payload = remainder
        records.append({
            "index": int(index_text),
            "kind": kind,
            "target": target,
            "seq": seq,
            "payload": payload,
        })
    return records


def validate_rendered_pairing(records):
    """Pair terminal events to pre-operation events.  Fails closed."""
    pre = {}
    for record in records:
        if record["seq"] is not None:
            continue
        if record["index"] in pre:
            raise TraceContractError(
                "duplicate pre-operation index %d" % (record["index"],)
            )
        pre[record["index"]] = record

    paired = {}
    for record in records:
        seq = record["seq"]
        if seq is None:
            continue
        if seq not in pre:
            raise TraceContractError(
                "terminal event %d references unknown sequence %d"
                % (record["index"], seq)
            )
        if seq >= record["index"]:
            raise TraceContractError(
                "terminal event %d must reference an EARLIER event; it"
                " references %d" % (record["index"], seq)
            )
        if seq in paired:
            raise TraceContractError(
                "pre-operation event %d already has terminal event %d;"
                " %d is a duplicate"
                % (seq, paired[seq]["index"], record["index"])
            )
        base = pre[seq]
        if record["kind"] not in (base["kind"] + OK_SUFFIX,
                                  base["kind"] + ERR_SUFFIX):
            raise TraceContractError(
                "terminal kind %r does not name pre-operation kind %r"
                % (record["kind"], base["kind"])
            )
        if record["target"] != base["target"]:
            raise TraceContractError(
                "terminal target %r does not match pre-operation target %r"
                % (record["target"], base["target"])
            )
        paired[seq] = record
    return pre, paired


def rendered_initialization(workspace):
    """The rendered trace of one observed fresh initialization."""
    with seam.observe(workspace["registry"], terminal_events=True) as trace:
        initializer()(workspace["registry"], workspace["anchor"])
    return trace.render()


def rendered_interleaved():
    """The required interleaving, rendered.

    A wrapped fsync runs to completion INSIDE a still-running wrapped execute,
    so the inner operation's terminal event lands between the outer operation's
    pre-event and its terminal event.  Adjacency mispairs this; a serialized
    sequence number does not.
    """
    rec = recorder()
    shim = os_shim(rec, _StubOs())

    def nested(name, args):
        shim.fsync(9)

    connection_stub = _StubConnection()
    connection_stub.observer = nested
    traced_connection(rec, connection_stub).execute("SELECT 1")
    return rec.render()


# ---------------------------------------------------------------------------
# 21-24, 29  --  the positive serialization properties
# ---------------------------------------------------------------------------

def test_p1b_21_every_terminal_line_serializes_exactly_one_sequence(workspace):
    """21.  One seq=<non-negative integer> per terminal event, no more."""
    rendered = rendered_initialization(workspace)
    records = parse_rendered_trace(rendered)
    terminals = [r for r in records if r["seq"] is not None]
    assert terminals, (
        "precondition: the rendered trace must contain terminal events\n%s"
        % (rendered,)
    )
    for record in terminals:
        assert isinstance(record["seq"], int) and record["seq"] >= 0, (
            "seq must be a non-negative integer, got %r" % (record["seq"],)
        )
    for line in rendered.split("\n"):
        kind = line.split(None, 2)[1] if len(line.split(None, 2)) > 1 else ""
        if kind.endswith(OK_SUFFIX) or kind.endswith(ERR_SUFFIX):
            assert line.count(SEQ_TOKEN) == 1, (
                "exactly one serialized sequence per terminal line: %r" % (line,)
            )


def test_p1b_22_serialized_sequence_points_to_an_earlier_pre_event(workspace):
    """22.  The reference is backwards, always, into a pre-operation event."""
    records = parse_rendered_trace(rendered_initialization(workspace))
    pre, paired = validate_rendered_pairing(records)
    assert paired, "precondition: at least one pairing must exist"
    for seq, terminal in paired.items():
        assert seq < terminal["index"]
        assert pre[seq]["seq"] is None, (
            "a terminal event must reference a PRE-OPERATION event"
        )


def test_p1b_23_serialized_kinds_match_between_terminal_and_pre_event(workspace):
    """23.  <kind>:ok / <kind>:err names its own pre-operation kind."""
    records = parse_rendered_trace(rendered_initialization(workspace))
    pre, paired = validate_rendered_pairing(records)
    for seq, terminal in paired.items():
        assert terminal["kind"] in (pre[seq]["kind"] + OK_SUFFIX,
                                    pre[seq]["kind"] + ERR_SUFFIX), (
            "kind mismatch: terminal %r, pre-operation %r"
            % (terminal["kind"], pre[seq]["kind"])
        )


def test_p1b_24_serialized_targets_match_between_terminal_and_pre_event(workspace):
    """24.  The normalized target survives serialization unchanged."""
    records = parse_rendered_trace(rendered_initialization(workspace))
    pre, paired = validate_rendered_pairing(records)
    for seq, terminal in paired.items():
        assert terminal["target"] == pre[seq]["target"], (
            "target mismatch: terminal %r, pre-operation %r"
            % (terminal["target"], pre[seq]["target"])
        )
    assert set(r["target"] for r in records) <= {"registry", "mutex", "fs"}, (
        "the target vocabulary is closed and unchanged"
    )


def test_p1b_29_pre_operation_lines_serialize_no_sequence(workspace):
    """29.  Only terminal events carry the token."""
    rendered = rendered_initialization(workspace)
    records = parse_rendered_trace(rendered)
    pre_lines = [r for r in records if r["seq"] is None]
    assert pre_lines, "precondition: pre-operation events must exist"
    for record in pre_lines:
        assert SEQ_TOKEN not in record["payload"], (
            "a pre-operation payload must not carry the pairing token: %r"
            % (record["payload"],)
        )


def test_p1b_29b_legacy_rendering_carries_no_sequence_token(tmp_path):
    """29 (legacy).  With the option off the artifact is the historical one."""
    off = make_workspace(str(tmp_path / "off"))
    with seam.observe(off["registry"], terminal_events=False) as disabled:
        initializer()(off["registry"], off["anchor"])
    rendered = disabled.render()
    assert SEQ_TOKEN not in rendered, (
        "the legacy artifact must contain no %r token\n%s"
        % (SEQ_TOKEN, rendered)
    )
    records = parse_rendered_trace(rendered)
    assert [r for r in records if r["seq"] is not None] == [], (
        "the legacy artifact must contain no terminal event"
    )

    omitted = make_workspace(str(tmp_path / "omitted"))
    with seam.observe(omitted["registry"]) as default:
        initializer()(omitted["registry"], omitted["anchor"])
    assert (normalise(disabled.render(), off)
            == normalise(default.render(), omitted)), (
        "terminal_events=False must render exactly as the default"
    )


# ---------------------------------------------------------------------------
# 30  --  the interleaving is reconstructed exactly, from rendered bytes
# ---------------------------------------------------------------------------

def test_p1b_30_interleaved_completion_order_is_reconstructed_from_render():
    """30.  The required example, recovered from the artifact alone.

        event 0: execute
        event 1: fsync
        event 2: fsync:ok       seq=1
        event 3: execute:ok     seq=0
    """
    rendered = rendered_interleaved()
    records = parse_rendered_trace(rendered)

    shape = [(r["index"], r["kind"], r["target"], r["seq"]) for r in records]
    assert shape == [
        (0, "execute", "registry", None),
        (1, "fsync", "fs", None),
        (2, "fsync" + OK_SUFFIX, "fs", 1),
        (3, "execute" + OK_SUFFIX, "registry", 0),
    ], "unexpected serialized interleaving:\n%s" % (rendered,)

    pre, paired = validate_rendered_pairing(records)
    assert paired[0]["index"] == 3, "seq 0 is completed by event 3"
    assert paired[1]["index"] == 2, "seq 1 is completed by event 2"

    # Completion ORDER, reconstructed: the inner operation finishes first.
    completion_order = [r["seq"] for r in records if r["seq"] is not None]
    assert completion_order == [1, 0], (
        "the inner operation must complete before the outer one: %r"
        % (completion_order,)
    )

    # Adjacency would pair the outer execute:ok with event 2, not its pre-event.
    # The serialized sequence must instead point to the non-adjacent event 0.
    assert records[3]["seq"] == records[0]["index"] < records[3]["index"] - 1
    assert paired[0]["kind"] == "execute" + OK_SUFFIX
    assert paired[1]["kind"] == "fsync" + OK_SUFFIX


# ---------------------------------------------------------------------------
# 25-28  --  the parser fails closed on every malformed artifact
#
# These artifacts are hand-written text, never produced by the seam.  They
# exist so that the proof above cannot be satisfied by a permissive reader.
# ---------------------------------------------------------------------------

GOOD_TRACE = "\n".join([
    "  0  execute   registry SELECT 1",
    "  1  execute:ok registry seq=0 ",
])


def test_p1b_25_a_self_or_forward_reference_fails_closed():
    """25.  A terminal event may reference neither itself nor a later event."""
    assert validate_rendered_pairing(parse_rendered_trace(GOOD_TRACE))

    self_reference = "\n".join([
        "  1  execute   registry SELECT 1",
        "  1  execute:ok registry seq=1 ",
    ])
    with pytest.raises(TraceContractError, match="must reference an EARLIER event"):
        validate_rendered_pairing(parse_rendered_trace(self_reference))

    forward_reference = "\n".join([
        "  0  execute:ok registry seq=1 ",
        "  1  execute   registry SELECT 1",
    ])
    with pytest.raises(TraceContractError, match="must reference an EARLIER event"):
        validate_rendered_pairing(parse_rendered_trace(forward_reference))


def test_p1b_26_duplicate_terminal_events_fail_closed():
    """26.  A pre-operation event is resolved at most once."""
    duplicated = "\n".join([
        "  0  execute   registry SELECT 1",
        "  1  execute:ok registry seq=0 ",
        "  2  execute:ok registry seq=0 ",
    ])
    with pytest.raises(TraceContractError):
        validate_rendered_pairing(parse_rendered_trace(duplicated))


def test_p1b_27_malformed_missing_negative_or_duplicate_sequence_fails_closed():
    """27.  Every defective form of the token is rejected, not guessed."""
    malformed = "\n".join([
        "  0  execute   registry SELECT 1",
        "  1  execute:ok registry seq=abc ",
    ])
    with pytest.raises(TraceContractError):
        parse_rendered_trace(malformed)

    missing = "\n".join([
        "  0  execute   registry SELECT 1",
        "  1  execute:ok registry ",
    ])
    with pytest.raises(TraceContractError):
        parse_rendered_trace(missing)

    negative = "\n".join([
        "  0  execute   registry SELECT 1",
        "  1  execute:ok registry seq=-1 ",
    ])
    with pytest.raises(TraceContractError):
        parse_rendered_trace(negative)

    duplicate_token = "\n".join([
        "  0  execute   registry SELECT 1",
        "  1  execute:ok registry seq=0 seq=0 ",
    ])
    with pytest.raises(TraceContractError):
        parse_rendered_trace(duplicate_token)

    token_on_pre_event = "\n".join([
        "  0  execute   registry seq=0 SELECT 1",
    ])
    with pytest.raises(TraceContractError):
        parse_rendered_trace(token_on_pre_event)

    empty_sequence = "\n".join([
        "  0  execute   registry SELECT 1",
        "  1  execute:ok registry seq= ",
    ])
    with pytest.raises(TraceContractError):
        parse_rendered_trace(empty_sequence)


def test_p1b_28_an_unknown_referenced_sequence_fails_closed():
    """28.  A reference into nothing is a defect, not a silent skip."""
    unknown = "\n".join([
        "  0  execute   registry SELECT 1",
        "  1  execute:ok registry seq=7 ",
    ])
    with pytest.raises(TraceContractError):
        validate_rendered_pairing(parse_rendered_trace(unknown))

    kind_mismatch = "\n".join([
        "  0  fsync     fs       fd=7",
        "  1  execute:ok fs       seq=0 ",
    ])
    with pytest.raises(TraceContractError):
        validate_rendered_pairing(parse_rendered_trace(kind_mismatch))

    target_mismatch = "\n".join([
        "  0  execute   registry SELECT 1",
        "  1  execute:ok mutex    seq=0 ",
    ])
    with pytest.raises(TraceContractError):
        validate_rendered_pairing(parse_rendered_trace(target_mismatch))
