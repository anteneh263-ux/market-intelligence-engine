"""One-owner native clock binding for durable application-frame intake."""
import os
import threading
from app.research.phase6_clockbound import ClockBoundClock
from app.research.phase6_source_intake import SourceIntake, need
from app.research.phase6_start_validation import _snapshot


class ClockBoundSourceIntake:
    """Owns intake and clock; no clock injection or automatic recovery per frame."""
    def __init__(self,writer,*,plan,reservation,library_path,library_sha256):
        q=_snapshot(dict(plan=plan,reservation=reservation))
        # Construction is before admission. A native acquisition failure must not
        # claim the supplied writer. Intake validation still controls its claim.
        clock=ClockBoundClock(library_path=library_path,library_sha256=library_sha256,
                              max_offset_ms=q['plan']['max_clock_skew_ms'])
        try:intake=SourceIntake(writer,plan=q['plan'],reservation=q['reservation'])
        except BaseException:
            try:clock.close()
            except BaseException:pass  # Preserve the construction failure.
            raise
        self._clock,self._intake=clock,intake
        self._pid,self._thread=os.getpid(),threading.get_ident()
        self.stopped=False;self._busy=False

    def _owner(self):
        need(os.getpid()==self._pid and threading.get_ident()==self._thread)

    def _stop(self):
        self.stopped=True;self._intake.stopped=True;self._clock.stopped=True

    def receive(self,raw):
        self._owner();need(not self.stopped)
        if self._busy:
            self._stop();raise ValueError('BOUND_INTAKE_REENTRY')
        self._busy=True
        try:
            result=self._intake.receive(raw,clock=self._clock.sample)
            need(not self.stopped)
            return result
        except BaseException:
            self._stop();raise
        finally:self._busy=False

    def event(self):
        """Fresh event time from the same owner/session used for receive receipts."""
        self._owner();need(not self.stopped)
        if self._busy:
            self._stop();raise ValueError('BOUND_INTAKE_REENTRY')
        try:return self._clock.event()
        except BaseException:
            self._stop();raise

    def close(self):
        self._owner();self._stop()
        if self._busy:raise ValueError('BOUND_INTAKE_REENTRY')
        error=None
        for resource in (self._clock,self._intake):
            try:resource.close()
            except BaseException as exc:
                if error is None:error=exc
        if error is not None:raise error
