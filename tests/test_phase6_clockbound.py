import pytest
from app.research import phase6_clockbound as module


class Native:
    def __init__(self,*a):self.value=(1000000000,1002000000,1)
    def interval(self):return self.value
    def close(self):pass


def clock(monkeypatch,ticks=None):
    monkeypatch.setattr(module,'_Native',Native)
    values=ticks or [1000000,2000000,3000000,4000000]
    boot=iter(values)
    monkeypatch.setattr(module.time,'CLOCK_BOOTTIME',7,raising=False)
    monkeypatch.setattr(module.time,'clock_gettime_ns',lambda _:next(boot))
    ticks=iter(values)
    monkeypatch.setattr(module.time,'monotonic_ns',lambda:next(ticks))
    return module.ClockBoundClock(library_path='/synthetic/libclockbound.so',library_sha256='a'*64,max_offset_ms=100)


def test_instant_only_bound_includes_rounding_and_entire_call(monkeypatch):
    c=clock(monkeypatch);s=c.sample();p=s['probe']
    assert s['utc_ms']==1001 and p['offset_bound_ms']==2
    assert s['monotonic_ns']==p['measured_monotonic_ns']==p['valid_until_monotonic_ns']==2000000
    assert p['source_identity']=='clockbound-2.0.3-'+'a'*64
    assert c.event()==dict(utc_ms=1001,monotonic_ns=4000000,clock_session_id=c.session)


@pytest.mark.parametrize('value',[(0,1,0),(0,1,2),(0,1,3),(2,1,1),(True,1,1),(0,300000000,1)])
def test_native_invalid_or_uncertain_permanently_stops(monkeypatch,value):
    c=clock(monkeypatch);c.native.value=value
    with pytest.raises(module.ClockRefused):c.sample()
    assert c.stopped
    with pytest.raises(module.ClockRefused):c.sample()


def test_slow_call_exceeds_bound(monkeypatch):
    c=clock(monkeypatch,[1,101000001])
    with pytest.raises(module.ClockRefused):c.sample()


def test_regression_and_owner_change(monkeypatch):
    c=clock(monkeypatch);c.sample();c.native.value=(0,2000000,1)
    with pytest.raises(module.ClockRefused):c.sample()
    c=clock(monkeypatch);monkeypatch.setattr(module.os,'getpid',lambda:c.pid+1)
    with pytest.raises(module.ClockRefused):c.sample()


def test_no_linux_native_library_fallback(monkeypatch):
    monkeypatch.setattr(module.platform,'system',lambda:'Darwin')
    with pytest.raises(module.ClockRefused):module.ClockBoundClock(library_path='/missing',library_sha256='a'*64,max_offset_ms=100)


def test_suspend_is_included_in_uncertainty(monkeypatch):
    c=clock(monkeypatch)
    boot=iter([0,200000000])
    monkeypatch.setattr(module.time,'clock_gettime_ns',lambda _:next(boot))
    with pytest.raises(module.ClockRefused):c.sample()
    assert c.stopped
