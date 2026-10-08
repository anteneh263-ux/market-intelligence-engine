"""Descriptor-bound immutable blobs; no run authorization or recovery authority."""
import fcntl
import hashlib
import json
import os
import re
import stat
import threading


def _need(ok, reason):
    if not ok:
        raise ValueError(reason)


def _hex(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _header(value):
    _need(type(value) is dict and set(value) == {'schema_version', 'owner', 'run_id', 'max_bytes'}, 'INPUT_INVALID')
    _need(value['schema_version'] == 'p6.object_store.v1' and type(value['owner']) is str
          and re.fullmatch('[A-Za-z0-9_.:-]{1,128}', value['owner']) is not None
          and _hex(value['run_id']) and type(value['max_bytes']) is int
          and 0 < value['max_bytes'] < 2**63, 'INPUT_INVALID')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def _root(path):
    _need(type(path) is str and path.startswith('/') and path != '/'
          and os.path.normpath(path) == path and not path.startswith('//'), 'STORAGE_REFUSED')
    parts = path.split('/')[1:]
    _need(not any(re.search(r'(?:^|[^a-z0-9])h[-_ ]?0?25(?:[^0-9]|$)', p, re.I) for p in parts), 'STORAGE_REFUSED')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(fd):
    s = os.fstat(fd)
    return s.st_dev, s.st_ino


def _bound(path, fd):
    other = _root(path)
    try:
        _need(_identity(other) == _identity(fd), 'STORAGE_REFUSED')
    finally:
        os.close(other)


def _snapshot(s):
    return s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def _read(fd, name, size):
    before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == size, 'INTEGRITY_FAILURE')
    source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        _need(_snapshot(os.fstat(source)) == _snapshot(before), 'INTEGRITY_FAILURE')
        chunks = []
        remaining = size + 1
        while remaining:
            block = os.read(source, min(remaining, 1024 * 1024))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        _need(_snapshot(os.fstat(source)) == _snapshot(before), 'INTEGRITY_FAILURE')
        _need(_snapshot(os.stat(name, dir_fd=fd, follow_symlinks=False)) == _snapshot(before), 'INTEGRITY_FAILURE')
        raw = b''.join(chunks)
        _need(len(raw) == size, 'INTEGRITY_FAILURE')
        return raw
    finally:
        os.close(source)


def _publish(fd, name, raw):
    target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(target, view)
            _need(count > 0, 'STORAGE_REFUSED')
            view = view[count:]
        os.fsync(target)
        os.fchmod(target, 0o400)
        os.fsync(target)
    finally:
        os.close(target)
    os.fsync(fd)
    _need(_read(fd, name, len(raw)) == raw, 'STORAGE_REFUSED')


class ObjectWriter:
    """Single-process, single-use writer to an explicitly supplied empty root."""

    def __init__(self, root, *, owner, run_id, max_bytes):
        self._fd = None
        self._claim = None
        self._pid = os.getpid()
        self._thread = threading.get_ident()
        self._stopped = True
        self._root = root
        self._objects = {}
        self._total = 0
        self._limit = max_bytes
        self._header = _header(dict(schema_version='p6.object_store.v1', owner=owner,
                                    run_id=run_id, max_bytes=max_bytes))
        try:
            self._fd = _root(root)
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _bound(root, self._fd)
            _need(not os.listdir(self._fd), 'STORAGE_REFUSED')
            _publish(self._fd, 'writer.json', self._header)
            _bound(root, self._fd)
            self._stopped = False
        except BaseException as exc:
            self.close()
            if isinstance(exc, (OSError, ValueError)):
                raise ValueError('STORAGE_REFUSED') from None
            raise

    @property
    def header(self):
        return json.loads(self._header)

    def claim(self):
        _need(not self._stopped and self._pid == os.getpid()
              and self._thread == threading.get_ident(), 'WRITER_STOPPED')
        _need(self._claim is None and not self._objects, 'WRITER_OWNERSHIP')
        self._claim = object()
        return self._claim

    def put(self, raw, *, claim=None):
        _need(not self._stopped and self._pid == os.getpid()
              and self._thread == threading.get_ident(), 'WRITER_STOPPED')
        _need(claim is self._claim, 'WRITER_OWNERSHIP')
        _need(type(raw) is bytes, 'INPUT_INVALID')
        digest = hashlib.sha256(raw).hexdigest()
        name = digest + '.blob'
        try:
            _bound(self._root, self._fd)
            _need(_read(self._fd, 'writer.json', len(self._header)) == self._header, 'STORAGE_REFUSED')
            _need(set(os.listdir(self._fd)) == {'writer.json'} | {h + '.blob' for h in self._objects}, 'STORAGE_REFUSED')
            if digest in self._objects:
                _need(_read(self._fd, name, len(raw)) == raw, 'STORAGE_REFUSED')
            else:
                _need(self._total + len(raw) <= self._limit, 'STORAGE_REFUSED')
                _publish(self._fd, name, raw)
                self._objects[digest] = len(raw)
                self._total += len(raw)
            _bound(self._root, self._fd)
            return digest
        except BaseException as exc:
            self._stopped = True
            if isinstance(exc, (OSError, ValueError)):
                raise ValueError('STORAGE_REFUSED') from None
            raise

    def close(self, *, claim=None):
        _need(self._pid != os.getpid() or self._thread == threading.get_ident(), 'WRITER_STOPPED')
        _need(claim is self._claim, 'WRITER_OWNERSHIP')
        # Never LOCK_UN: a fork shares the open file description with its parent.
        # Closing the child's copy alone cannot release the parent's descriptor.
        self._stopped = True
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def __enter__(self):
        _need(not self._stopped and self._pid == os.getpid()
              and self._thread == threading.get_ident(), 'WRITER_STOPPED')
        return self

    def __exit__(self, *_):
        self.close()


def inspect_objects(root, *, header, objects):
    """Check caller-supplied inventory read-only; external authenticity is upstream."""
    encoded = _header(header)
    _need(type(objects) is dict, 'INPUT_INVALID')
    _need(all(_hex(h) and type(n) is int and 0 <= n < 2**63 for h, n in objects.items()), 'INPUT_INVALID')
    total = sum(objects.values())
    _need(total <= header['max_bytes'], 'INPUT_INVALID')
    fd = None
    try:
        fd = _root(root)
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        _bound(root, fd)
    except (OSError, ValueError):
        if fd is not None:
            os.close(fd)
        raise ValueError('STORAGE_REFUSED') from None
    try:
        expected = {'writer.json'} | {h + '.blob' for h in objects}
        _need(set(os.listdir(fd)) == expected, 'INTEGRITY_FAILURE')
        initial = {name: _snapshot(os.stat(name, dir_fd=fd, follow_symlinks=False)) for name in expected}
        _need(_read(fd, 'writer.json', len(encoded)) == encoded, 'INTEGRITY_FAILURE')
        for digest, size in objects.items():
            _need(hashlib.sha256(_read(fd, digest + '.blob', size)).hexdigest() == digest, 'INTEGRITY_FAILURE')
        _need(set(os.listdir(fd)) == expected, 'INTEGRITY_FAILURE')
        _need(initial == {name: _snapshot(os.stat(name, dir_fd=fd, follow_symlinks=False)) for name in expected}, 'INTEGRITY_FAILURE')
        _bound(root, fd)
        return dict(schema_version='p6.object_inspection.v1', structural_status='VALID',
                    technical_verification='LOCAL_ONLY', object_count=len(objects),
                    total_bytes=total, runtime_accepted=False)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) == 'STORAGE_REFUSED':
            raise
        raise ValueError('INTEGRITY_FAILURE') from None
    finally:
        os.close(fd)
