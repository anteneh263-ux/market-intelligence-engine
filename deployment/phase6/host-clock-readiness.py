"""Observational admission diagnostic only; never signs receipts or restarts a clock owner."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import subprocess
import sys
import time


def tracking(raw):
    fields = {}
    for line in raw.splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            if key.strip() in fields:
                raise ValueError('DUPLICATE_TRACKING_FIELD')
            fields[key.strip()] = value.strip()
    interval = Decimal(fields['Update interval'].removesuffix(' seconds'))
    ref = int(datetime.strptime(fields['Ref time (UTC)'], '%a %b %d %H:%M:%S %Y')
              .replace(tzinfo=timezone.utc).timestamp())
    valid = (interval.is_finite() and interval >= 16 and fields['Leap status'] == 'Normal'
             and fields['Reference ID'] == 'A9FEA97B (169.254.169.123)')
    return ref, valid


def observe(create_clock, get_tracking, emit, elapsed, sleep):
    last_reference = None
    regular = 0
    deadline = elapsed() + 90
    while regular < 2:
        if elapsed() >= deadline:
            raise ValueError('CHRONY_READINESS_TIMEOUT')
        raw = get_tracking()
        reference, valid = tracking(raw)
        if last_reference is not None and reference < last_reference:
            raise ValueError('CHRONY_REFERENCE_REGRESSED')
        if not valid:
            regular = 0
        elif last_reference is not None and reference > last_reference:
            regular += 1
        emit(dict(kind='chrony', tracking=raw, regular_updates=regular, valid=valid))
        last_reference = reference
        if regular < 2:
            sleep(1)
    clock = create_clock()  # Exactly one owner. A refusal is terminal for this preparation.
    try:
        session = None
        previous = None
        for i in range(10):
            sample = clock.sample()
            if session is None:
                session = sample['clock_session_id']
            if sample['clock_session_id'] != session or (previous is not None and
                    sample['monotonic_ns'] <= previous):
                raise ValueError('CLOCK_OWNER_CHANGED_OR_STALLED')
            previous = sample['monotonic_ns']
            emit(dict(kind='native_observation', index=i, sample=sample))
            if i < 9:
                sleep(1)
    finally:
        primary = sys.exc_info()[1]
        try:
            clock.close()
        except BaseException as secondary:
            if primary is None:
                raise
            if hasattr(primary, 'add_note'):
                primary.add_note('Secondary clock close failure: ' + type(secondary).__name__)
    return dict(status='READINESS_OBSERVED', samples=10, clock_session_id=session,
                runtime_accepted=False, prospective_evidence=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library', required=True)
    p.add_argument('--sha256', required=True)
    args = p.parse_args()
    from app.research.phase6_clockbound import ClockBoundClock
    def get_tracking():
        return subprocess.check_output(['/usr/bin/chronyc', 'tracking'], text=True, timeout=3,
                                       env=dict(PATH='/usr/bin:/bin', LC_ALL='C'))
    def emit(event):
        print(json.dumps(event, sort_keys=True), flush=True)
    answer = observe(lambda: ClockBoundClock(library_path=args.library, library_sha256=args.sha256,
        max_offset_ms=100), get_tracking, emit,
        lambda: time.clock_gettime(time.CLOCK_BOOTTIME), time.sleep)
    emit(answer)


if __name__ == '__main__':
    main()
