"""Recovery-specific CLI transport, unchanged bounded functions from v2 execute-live.

Keeping these two functions here avoids importing the unrelated receipt runtime
and cryptography just to bound a CLI subprocess. No AWS or subprocess on import.
Original source SHA: f1bed0aad725163ef6fd069416c3660d6d05cd27fa1e8f82b71c73d6319c23c1
"""
import os
import selectors
import signal
import subprocess
import time
PYTHON='/Users/antenehhailu/Desktop/market-intelligence-engine/.venv/bin/python'

class CallUnknown(RuntimeError):
    def __init__(self, reason, stdout=b'', stderr=b''):
        super().__init__(reason)
        self.reason, self.stdout, self.stderr = reason, stdout, stderr

def bounded_cli(argv, env, *, timeout=30, limit=1024*1024):
    """Bound elapsed time and both captured streams; kill process group on overflow."""
    output = [bytearray(), bytearray()]
    p = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        with selectors.DefaultSelector() as selector:
            for n, stream in enumerate((p.stdout, p.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, n)
            deadline = time.monotonic() + timeout
            while selector.get_map():
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise CallUnknown('TIMEOUT', *map(bytes, output))
                for key, _ in selector.select(min(remaining, .1)):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    target = output[key.data]
                    room = limit-len(target)
                    target.extend(block[:room])
                    if len(block) > room:
                        raise CallUnknown('OUTPUT_LIMIT', *map(bytes, output))
            try:
                code = p.wait(timeout=max(.001, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                raise CallUnknown('TIMEOUT', *map(bytes, output))
            return subprocess.CompletedProcess(argv, code, *map(bytes, output))
    except BaseException:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait(timeout=5)
        raise
    finally:
        p.stdout.close()
        p.stderr.close()
