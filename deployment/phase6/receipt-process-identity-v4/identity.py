"""Strict local macOS process identity. No AWS, signals, stores or work on import.

ABI: installed Apple SDK sys/proc_info.h (proc_bsdinfo, PROC_PIDTBSDINFO),
libproc.h, sys/sysctl.h (CTL_KERN=1, KERN_PROCARGS2=49). Environment bytes
returned by PROCARGS2 are never decoded, returned, logged or persisted.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import struct

VERSION = 'receipt-process-identity.v4'
SCHEMA = 'p6.process_identity.v4'


def require(ok):
    if not ok:
        raise ValueError('PROCESS_IDENTITY_V4_REFUSED')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def hexhash(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def absolute(value):
    require(type(value) is str and value.startswith('/') and '\0' not in value
            and os.path.normpath(value) == value)
    return value


def file_hash(path):
    """Hash a regular, non-symlink file, with bounded size and stable fd metadata."""
    absolute(str(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= 32*1024*1024)
        h = hashlib.sha256()
        count = 0
        while True:
            block = os.read(fd, 65536)
            if not block:
                break
            count += len(block)
            require(count <= before.st_size)
            h.update(block)
        after = os.fstat(fd)
        require(count == before.st_size and (before.st_dev, before.st_ino,
            before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns))
        return h.hexdigest()
    finally:
        os.close(fd)


def validate_pin(pin, *, files=True):
    require(type(pin) is dict and set(pin) == {'schema_version', 'launcher',
        'launcher_realpath', 'launcher_sha256', 'kernel_executable', 'kernel_sha256'})
    require(pin['schema_version'] == 'p6.interpreter_pin.v4')
    for key in ('launcher', 'launcher_realpath', 'kernel_executable'):
        absolute(pin[key])
    require(hexhash(pin['launcher_sha256']) and hexhash(pin['kernel_sha256']))
    if files:
        require(str(Path(pin['launcher']).resolve(strict=True)) == pin['launcher_realpath'])
        require(str(Path(pin['kernel_executable']).resolve(strict=True)) == pin['kernel_executable'])
        require(file_hash(pin['launcher_realpath']) == pin['launcher_sha256'])
        require(file_hash(pin['kernel_executable']) == pin['kernel_sha256'])
    return pin


def parse_procargs(raw):
    """Decode only argc arguments after executable path and padding, never environ."""
    require(type(raw) is bytes and 6 <= len(raw) <= 1024*1024)
    argc = struct.unpack_from('=i', raw)[0]
    require(1 <= argc <= 128)
    end = raw.find(b'\0', 4)
    require(end > 4)
    absolute(raw[4:end].decode('utf-8', errors='strict'))
    pos = end + 1
    while pos < len(raw) and raw[pos] == 0:
        pos += 1
    args = []
    for _ in range(argc):
        end = raw.find(b'\0', pos)
        require(end >= pos and end-pos <= 32768)
        args.append(raw[pos:end].decode('utf-8', errors='strict'))
        pos = end + 1
    require(bool(args[0]))
    return args


class _BSDInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in
        ('flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid',
         'rgid', 'svuid', 'svgid', 'reserved')]
    _fields_ += [('comm', ctypes.c_char*16), ('name', ctypes.c_char*32)]
    _fields_ += [(name, ctypes.c_uint32) for name in
        ('nfiles', 'pgid', 'jobc', 'tdev', 'tpgid')]
    _fields_ += [('nice', ctypes.c_int32), ('start_sec', ctypes.c_uint64),
                ('start_usec', ctypes.c_uint64)]


class Darwin:
    """Errors remain errors: missing permission never means 'process absent'."""
    def __init__(self):
        require(platform.system() == 'Darwin' and ctypes.sizeof(_BSDInfo) == 136)
        self.proc = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        self.lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        self.proc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                          ctypes.c_void_p, ctypes.c_int]
        self.proc.proc_pidinfo.restype = ctypes.c_int
        self.proc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        self.proc.proc_pidpath.restype = ctypes.c_int
        self.lib.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        self.lib.sysctl.restype = ctypes.c_int
        self.lib.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        self.lib.sysctlbyname.restype = ctypes.c_int

    def birth(self, pid):
        require(type(pid) is int and 1 < pid < 2**31)
        b = _BSDInfo()
        require(self.proc.proc_pidinfo(pid, 3, 0, ctypes.byref(b), ctypes.sizeof(b)) == ctypes.sizeof(b))
        require(b.pid == pid and b.status != 5 and b.start_sec > 0 and b.start_usec < 1000000)
        buf = ctypes.create_string_buffer(128)
        length = ctypes.c_size_t(len(buf))
        require(self.lib.sysctlbyname(b'kern.bootsessionuuid', buf, ctypes.byref(length), None, 0) == 0)
        boot = buf.value.decode('ascii')
        require(re.fullmatch('[0-9A-Fa-f-]{36}', boot) is not None)
        return dict(pid=pid, uid=b.uid, ruid=b.ruid, svuid=b.svuid,
                    start_sec=b.start_sec, start_usec=b.start_usec, boot_uuid=boot)

    def executable(self, pid):
        buf = ctypes.create_string_buffer(4096)
        count = self.proc.proc_pidpath(pid, buf, len(buf))
        require(0 < count < len(buf))
        return absolute(buf.value.decode('utf-8', errors='strict'))

    def argv(self, pid):
        mib = (ctypes.c_int*3)(1, 49, pid)
        buf = ctypes.create_string_buffer(1024*1024)
        length = ctypes.c_size_t(len(buf))
        require(self.lib.sysctl(mib, 3, buf, ctypes.byref(length), None, 0) == 0)
        require(0 < length.value <= len(buf))
        return parse_procargs(buf.raw[:length.value])


def snapshot(pid, *, backend=None):
    backend = Darwin() if backend is None else backend
    before = backend.birth(pid)
    executable = backend.executable(pid)
    args = backend.argv(pid)
    sha = file_hash(executable)
    after = backend.birth(pid)
    # Detect both PID reuse and exec/argv changes during these non-atomic reads.
    require(before == after and backend.executable(pid) == executable and backend.argv(pid) == args)
    require(backend.birth(pid) == before)
    return dict(**before, kernel_executable=executable, executable_sha256=sha, argv=args)


def expectation(*, pid, uid, argv, script_sha256, root, contract_sha256, pin):
    validate_pin(pin, files=False)
    require(type(pid) is int and pid > 1 and type(uid) is int and uid >= 0)
    require(type(argv) is list and 3 <= len(argv) <= 128 and
            all(type(x) is str and '\0' not in x for x in argv))
    require(argv[0] == pin['launcher'] and argv[1] == '-u')
    absolute(argv[2]); absolute(str(root))
    require(hexhash(script_sha256) and hexhash(contract_sha256))
    return dict(schema_version='p6.process_expectation.v4', pid=pid, uid=uid, argv=argv,
        script_sha256=script_sha256, root=str(root), contract_sha256=contract_sha256,
        interpreter=pin)


def check_snapshot(value, expected):
    require(type(value) is dict and set(value) == {'pid', 'uid', 'ruid', 'svuid',
        'start_sec', 'start_usec', 'boot_uuid', 'kernel_executable', 'executable_sha256', 'argv'})
    pin = expected['interpreter']
    require(value['pid'] == expected['pid'] and type(value['pid']) is int)
    require(all(type(value[k]) is int and value[k] == expected['uid'] for k in ('uid', 'ruid', 'svuid')))
    require(type(value['start_sec']) is int and value['start_sec'] > 0 and
            type(value['start_usec']) is int and 0 <= value['start_usec'] < 1000000)
    require(type(value['boot_uuid']) is str and re.fullmatch('[0-9A-Fa-f-]{36}', value['boot_uuid']) is not None)
    require(value['kernel_executable'] == pin['kernel_executable'] and value['executable_sha256'] == pin['kernel_sha256'])
    # Only the explicitly pinned launcher->kernel mapping is accepted.
    require(value['argv'] == [pin['kernel_executable'], *expected['argv'][1:]])


def validate_expected(expected):
    require(type(expected) is dict and expected.get('schema_version') == 'p6.process_expectation.v4')
    rebuilt = expectation(pid=expected['pid'], uid=expected['uid'], argv=expected['argv'],
        script_sha256=expected['script_sha256'], root=expected['root'],
        contract_sha256=expected['contract_sha256'], pin=expected['interpreter'])
    require(expected == rebuilt)


def bind(expected, *, inspect=snapshot):
    validate_expected(expected)
    validate_pin(expected['interpreter'])
    require(file_hash(expected['argv'][2]) == expected['script_sha256'])
    observed = inspect(expected['pid'])
    check_snapshot(observed, expected)
    return dict(schema_version=SCHEMA, expectation_sha256=digest(expected), birth=observed)


def verify(binding, expected, *, inspect=snapshot, protective=False):
    validate_expected(expected)
    require(type(binding) is dict and set(binding) == {'schema_version', 'expectation_sha256', 'birth'})
    require(binding['schema_version'] == SCHEMA and binding['expectation_sha256'] == digest(expected))
    validate_pin(expected['interpreter'])
    # Already-loaded observer may still be stopped if preparation script pins fail.
    if not protective:
        require(file_hash(expected['argv'][2]) == expected['script_sha256'])
    check_snapshot(binding['birth'], expected)
    current = inspect(expected['pid'])
    check_snapshot(current, expected)
    require(current == binding['birth'])
    return current
