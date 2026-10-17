import itertools
import json
import pytest
from test_phase6_source_intake import setup
from test_phase6_binance_source import message,raw
from app.research import phase6_clockbound as clock_module
from app.research.phase6_clockbound_intake import ClockBoundSourceIntake
from app.research.phase6_object_store import ObjectWriter
from app.research.phase6_validation import canonical_hash as H


class Native:
    instances=[]
    def __init__(self,*args):
        self.value=(120009000000,120011000000,1);self.closed=False;self.reads=0
        self.instances.append(self)
    def interval(self):self.reads+=1;return self.value
    def close(self):self.closed=True


def binding(tmp_path,monkeypatch):
    intake,root=setup(tmp_path)
    plan,reservation=intake.plan,intake.reservation
    intake.close()
    target=tmp_path/'bound';target.mkdir()
    writer=ObjectWriter(str(target),owner='test',run_id=H(reservation),max_bytes=1000000)
    Native.instances=[];monkeypatch.setattr(clock_module,'_Native',Native)
    ticks=itertools.count(1000000,1000)
    monkeypatch.setattr(clock_module.time,'CLOCK_BOOTTIME',7,raising=False)
    monkeypatch.setattr(clock_module.time,'clock_gettime_ns',lambda _:next(ticks))
    monkeypatch.setattr(clock_module.time,'monotonic_ns',lambda:next(ticks))
    return ClockBoundSourceIntake(writer,plan=plan,reservation=reservation,
        library_path='/synthetic/libclockbound.so',library_sha256='a'*64),target


def test_bound_native_clock_is_persisted_before_ack(tmp_path,monkeypatch):
    owner,root=binding(tmp_path,monkeypatch)
    out=owner.receive(raw(message()))
    assert Native.instances[0].reads==1
    probe=json.loads((root/(out['commit']['clock_probe_hash']+'.blob')).read_text())
    receipt=json.loads((root/(out['commit']['receipt_hash']+'.blob')).read_text())
    assert probe==out['probe'] and probe['source_identity']=='clockbound-2.0.3-'+'a'*64
    assert receipt['clock_session_id']==probe['clock_session_id']
    assert receipt['received_monotonic_ns']==probe['valid_until_monotonic_ns']
    assert not any(out[k] for k in ('runtime_accepted','source_authenticated','clock_authenticated'))
    owner.close();assert Native.instances[0].closed


@pytest.mark.parametrize('fault',['native','invalid_frame','write','reentry','close_reentry'])
def test_any_admitted_failure_stops_both_owners(tmp_path,monkeypatch,fault):
    owner,root=binding(tmp_path,monkeypatch);native=Native.instances[0];frame=raw(message())
    if fault=='native':native.value=(120009000000,120011000000,2)
    elif fault=='invalid_frame':frame=b'{}'
    elif fault=='write':
        def fail(*a,**k):raise OSError('uncertain write')
        monkeypatch.setattr(owner._intake.writer,'put',fail)
    else:
        original=native.interval
        def recursive():
            try:owner.close() if fault=='close_reentry' else owner.receive(frame)
            except ValueError:pass
            return original()
        monkeypatch.setattr(native,'interval',recursive)
    before=set(root.iterdir())
    with pytest.raises((ValueError,OSError)):owner.receive(frame)
    assert owner.stopped and owner._intake.stopped and owner._clock.stopped
    if fault!='write':assert set(root.iterdir())==before
    calls=native.reads
    with pytest.raises(ValueError):owner.receive(raw(message()))
    assert native.reads==calls
    owner.close();assert native.closed


def test_owner_change_does_not_touch_native(tmp_path,monkeypatch):
    owner,root=binding(tmp_path,monkeypatch);native=Native.instances[0]
    with monkeypatch.context() as patch:
        patch.setattr(clock_module.os,'getpid',lambda:owner._pid+1)
        with pytest.raises(ValueError):owner.receive(raw(message()))
        with pytest.raises(ValueError):owner.close()
        assert native.reads==0 and not native.closed
    owner.close()


def test_cleanup_attempts_both_resources(tmp_path,monkeypatch):
    owner,root=binding(tmp_path,monkeypatch)
    def fail():raise OSError('native close')
    monkeypatch.setattr(owner._clock,'close',fail)
    calls=[];original=owner._intake.close
    def close():calls.append(True);original()
    monkeypatch.setattr(owner._intake,'close',close)
    with pytest.raises(OSError,match='native close'):owner.close()
    assert calls==[True] and owner.stopped


def test_same_native_clock_and_session_across_frames(tmp_path,monkeypatch):
    owner,root=binding(tmp_path,monkeypatch)
    first=owner.receive(raw(message()))
    Native.instances[0].value=(120010000000,120012000000,1)
    second=owner.receive(raw(message('quote')))
    assert len(Native.instances)==1 and Native.instances[0].reads==2
    assert first['receipt']['clock_session_id']==second['receipt']['clock_session_id']
    assert second['receipt']['previous_receipt_hash']==H(first['receipt'])
    owner.close()


@pytest.mark.parametrize('fault',['native_acquisition','invalid_reservation'])
def test_constructor_failure_leaves_writer_unclaimed(tmp_path,monkeypatch,fault):
    owner,root=binding(tmp_path,monkeypatch)
    plan=owner._intake.plan;reservation=dict(owner._intake.reservation)
    owner.close();target=tmp_path/'failed';target.mkdir()
    writer=ObjectWriter(str(target),owner='test',run_id=H(reservation),max_bytes=1000000)
    if fault=='native_acquisition':
        def fail(*a):raise OSError('native unavailable')
        monkeypatch.setattr(clock_module,'_Native',fail)
    else:reservation['sequence']=0
    with pytest.raises((OSError,ValueError)):
        ClockBoundSourceIntake(writer,plan=plan,reservation=reservation,
            library_path='/synthetic/libclockbound.so',library_sha256='a'*64)
    assert writer._claim is None and list(target.iterdir())==[target/'writer.json']
    if fault=='invalid_reservation':assert Native.instances[-1].closed
    writer.close()


def test_event_clock_shares_receipt_session_without_new_native(tmp_path,monkeypatch):
    owner,root=binding(tmp_path,monkeypatch)
    receipt=owner.receive(raw(message()))['receipt'];event=owner.event()
    assert event['clock_session_id']==receipt['clock_session_id']
    assert event['monotonic_ns']>receipt['received_monotonic_ns'] and len(Native.instances)==1
    owner.close()
    with pytest.raises(ValueError):owner.event()
