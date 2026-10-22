"""Harmless local process child for pre-window admission; never imports AWS."""
import json
import os
import signal
import sys

if __name__ == '__main__':
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    print(json.dumps(dict(status='LOCAL_PROBE_READY', pid=os.getpid(),
                         executable=sys.executable)), flush=True)
    sys.stdin.readline()
