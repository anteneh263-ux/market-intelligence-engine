"""Pinned Linux ClockBound 2.0.3 client. No fabricated wall-clock fallback."""
import ctypes
import fcntl
import hashlib
import os
import platform
import secrets
import stat
import threading
import time


class ClockRefused(ValueError):
    def __init__(self,message,*,diagnostic=None):
        super().__init__(message)
        self.diagnostic=dict(diagnostic) if diagnostic is not None else None


class _Timespec(ctypes.Structure):
    _fields_=[('tv_sec',ctypes.c_long),('tv_nsec',ctypes.c_long)]


class _Error(ctypes.Structure):
    _fields_=[('kind',ctypes.c_int),('sys_errno',ctypes.c_int),('detail',ctypes.c_void_p)]


class _Result(ctypes.Structure):
    _fields_=[('earliest',_Timespec),('latest',_Timespec),('clock_status',ctypes.c_int)]


class _Native:
    def __init__(self,path,digest):
        if platform.system()!='Linux' or ctypes.sizeof(ctypes.c_long)!=8 or not os.path.isabs(path):
            raise ClockRefused('Pinned 64-bit Linux library required')
        self.fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        self.ctx=None
        try:
            st=os.fstat(self.fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size>16*1024*1024:raise ClockRefused('Invalid library')
            # Load only sealed bytes, never the mutable source inode.
            source_fd=self.fd
            self.fd=os.memfd_create('mie-clockbound',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
            h=hashlib.sha256()
            try:
                total=0
                while True:
                    block=os.read(source_fd,65536)
                    if not block:break
                    total+=len(block)
                    if total>16*1024*1024:raise ClockRefused('Invalid library')
                    h.update(block)
                    pending=memoryview(block)
                    while pending:
                        count=os.write(self.fd,pending)
                        if count<=0:raise ClockRefused('Library snapshot failed')
                        pending=pending[count:]
            finally:os.close(source_fd)
            fcntl.fcntl(self.fd,fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
            if h.hexdigest()!=digest:raise ClockRefused('Library hash mismatch')
            self.lib=ctypes.CDLL('/proc/self/fd/'+str(self.fd))
            self.lib.clockbound_open.argtypes=[ctypes.c_char_p,ctypes.POINTER(_Error)]
            self.lib.clockbound_open.restype=ctypes.c_void_p
            self.lib.clockbound_now.argtypes=[ctypes.c_void_p,ctypes.POINTER(_Result)]
            self.lib.clockbound_now.restype=ctypes.POINTER(_Error)
            self.lib.clockbound_close.argtypes=[ctypes.c_void_p]
            self.lib.clockbound_close.restype=ctypes.POINTER(_Error)
            error=_Error();self.ctx=self.lib.clockbound_open(b'/var/run/clockbound/shm0',ctypes.byref(error))
            if not self.ctx or error.kind:raise ClockRefused('ClockBound unavailable')
        except BaseException:
            self.close();raise

    def interval(self):
        result=_Result()
        if self.lib.clockbound_now(self.ctx,ctypes.byref(result)):raise ClockRefused('ClockBound read failed')
        def ns(t):
            if t.tv_sec<0 or not 0<=t.tv_nsec<1000000000:raise ClockRefused('Invalid native timestamp')
            return t.tv_sec*1000000000+t.tv_nsec
        return ns(result.earliest),ns(result.latest),result.clock_status

    def close(self):
        if self.ctx:
            self.lib.clockbound_close(self.ctx);self.ctx=None
        if self.fd is not None:os.close(self.fd);self.fd=None


class ClockBoundClock:
    """Single-process/thread, instant-only probes; deployment pins daemon policy."""
    def __init__(self,*,library_path,library_sha256,max_offset_ms):
        if (type(library_sha256) is not str or len(library_sha256)!=64
                or any(c not in '0123456789abcdef' for c in library_sha256)
                or type(max_offset_ms) is not int or not 0<max_offset_ms<=100):
            raise ClockRefused('Explicit clock contract required')
        self.native=_Native(library_path,library_sha256)
        self.pid,self.thread=os.getpid(),threading.get_ident()
        self.session=secrets.token_hex(32);self.source='clockbound-2.0.3-'+library_sha256
        self.limit=max_offset_ms;self.last=None;self.stopped=False;self.busy=False

    def sample(self):
        if self.stopped or os.getpid()!=self.pid or threading.get_ident()!=self.thread:
            raise ClockRefused('Clock owner stopped or changed')
        if self.busy:
            self.stopped=True;raise ClockRefused('Clock reentry')
        self.busy=True
        try:
            boot_before=time.clock_gettime_ns(time.CLOCK_BOOTTIME)
            before=time.monotonic_ns();lo,hi,status=self.native.interval();after=time.monotonic_ns()
            boot_after=time.clock_gettime_ns(time.CLOCK_BOOTTIME)
            if (type(lo) is not int or type(hi) is not int or type(status) is not int
                    or status!=1 or not 0<=lo<=hi or not 0<=before<=after<2**63
                    or not 0<=boot_before<=boot_after<2**63
                    or boot_after-boot_before<after-before):
                raise ClockRefused('Unsynchronized or invalid clock interval',diagnostic=dict(
                    earliest_ns=lo,latest_ns=hi,native_status=status,monotonic_before_ns=before,
                    monotonic_after_ns=after,boottime_before_ns=boot_before,boottime_after_ns=boot_after))
            utc=(lo+hi)//2000000
            # BOOTTIME covers the entire bracket, including Linux suspend.
            radius=max(abs(utc*1000000-lo),abs(hi-utc*1000000))+(boot_after-boot_before)
            bound=(radius+999999)//1000000
            if not 0<=utc<2**63 or bound>self.limit or self.stopped:
                raise ClockRefused('Clock uncertainty exceeds contract')
            if self.last and (utc<self.last[0] or after<=self.last[1]):
                raise ClockRefused('Clock regressed')
            self.last=(utc,after)
            probe=dict(schema_version='p6.clock_probe.v1',clock_session_id=self.session,
                measured_utc_ms=utc,measured_monotonic_ns=after,valid_until_monotonic_ns=after,
                offset_bound_ms=bound,source_identity=self.source)
            return dict(utc_ms=utc,monotonic_ns=after,clock_session_id=self.session,probe=probe)
        except BaseException:
            self.stopped=True;raise
        finally:self.busy=False

    def event(self):
        result=self.sample();del result['probe'];return result

    def utc_ms(self):return self.sample()['utc_ms']

    def close(self):
        if os.getpid()!=self.pid or threading.get_ident()!=self.thread:raise ClockRefused('Wrong owner')
        self.stopped=True;self.native.close()
