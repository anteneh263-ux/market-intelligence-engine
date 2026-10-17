"""Bounded one-shot transport candidate; not prospective activation authority."""
import os
import ssl
import threading
import time
from app.research.phase6_binance_source import decode, MAX_BYTES
from app.research.phase6_clockbound_intake import ClockBoundSourceIntake
from app.research.phase6_source_intake import need

ENDPOINT='wss://stream.binance.com:443/ws/'
SUBSCRIBE='{"method":"SUBSCRIBE","params":["btcusdt@kline_1m","btcusdt@ticker"],"id":1}'
VERSION='16.1.1'


def _connect():
    import websockets
    from websockets.sync.client import connect
    need(websockets.__version__==VERSION)
    return connect(ENDPOINT,ssl=ssl.create_default_context(),proxy=None,compression=None,
        open_timeout=10,close_timeout=2,ping_interval=20,ping_timeout=10,
        max_size=MAX_BYTES,max_queue=1)


class BinanceClockBoundTransport:
    def __init__(self,writer,*,plan,reservation,library_path,library_sha256):
        self._intake=ClockBoundSourceIntake(writer,plan=plan,reservation=reservation,
            library_path=library_path,library_sha256=library_sha256)
        self._pid,self._thread=os.getpid(),threading.get_ident()
        self._socket=None;self._used=False;self._busy=False;self.stopped=False

    def _owner(self):
        need(os.getpid()==self._pid and threading.get_ident()==self._thread)

    def _close(self):
        self.stopped=True;error=None
        for resource in (self._socket,self._intake):
            if resource is None:continue
            try:resource.close()
            except BaseException as exc:
                if error is None:error=exc
        if error is not None:raise error

    def event(self):
        """Same-session event capability for the sequential downstream operator."""
        self._owner();need(not self.stopped)
        try:return self._intake.event()
        except BaseException:
            self.stopped=True;raise

    def close(self):
        self._owner();self.stopped=True
        if self._busy:raise ValueError('TRANSPORT_REENTRY')
        self._close()

    def run(self,*,max_messages,duration_seconds,on_commit):
        self._owner()
        if self._busy:
            self.stopped=True;raise ValueError('TRANSPORT_REENTRY')
        need(not self._used and not self.stopped)
        need(type(max_messages) is int and 1<=max_messages<=10000)
        need(type(duration_seconds) is int and 1<=duration_seconds<=1800 and callable(on_commit))
        self._used=True;self._busy=True
        count=0;last=None
        try:
            self._socket=_connect()
            self._socket.send(SUBSCRIBE)
            ack=decode(self._socket.recv(timeout=10,decode=False))
            need(set(ack)=={'result','id'} and ack['result'] is None and type(ack['id']) is int and ack['id']==1)
            # Collection duration starts only after subscription acknowledgement.
            deadline=time.monotonic()+duration_seconds
            while count<max_messages:
                need(not self.stopped)
                remaining=deadline-time.monotonic()
                if remaining<=0:break
                frame=self._socket.recv(timeout=min(5,remaining),decode=False)
                # Even a frame arriving at the boundary is retained; a deadline
                # overrun cannot be acknowledged as a successful collection.
                result=self._intake.receive(frame)
                need(not self.stopped and time.monotonic()<=deadline)
                commit_hash=result['commit_hash']
                stop_requested=on_commit(result) is True
                need(not self.stopped and time.monotonic()<=deadline)
                count+=1;last=commit_hash
                if stop_requested:break
            result=dict(messages=count,last_commit_hash=last,runtime_accepted=False,
                        source_authenticated=False,endpoint=ENDPOINT)
        except BaseException:
            try:self._close()
            except BaseException:pass
            raise
        else:
            self._close();return result
        finally:self._busy=False
