import json
import pytest
from test_phase6_binance_source import message,raw
from test_phase6_validation import plan
from app.research.phase6_source_intake import SourceIntake
from app.research.phase6_object_store import ObjectWriter
from app.research.phase6_validation import canonical_hash as H


def setup(tmp_path,limit=10000):
    p=plan();p.update(provider='binance-public-spot',instrument='BTC:USDT',intake_limit_bytes=limit)
    r=dict(schema_version='p6.reservation.v1',plan_hash=H(p),authorization_hash='a'*64,registry_identity='b'*64,sequence=1,reserved_utc_ms=1,previous_reservation_hash='0'*64)
    root=tmp_path/'raw';root.mkdir();w=ObjectWriter(str(root),owner='test',run_id=H(r),max_bytes=1000000)
    return SourceIntake(w,plan=p,reservation=r),root


def clock(t):
    return lambda:dict(utc_ms=t,monotonic_ns=t*1000000,clock_session_id='test',probe=dict(schema_version='p6.clock_probe.v1',clock_session_id='test',measured_utc_ms=t,measured_monotonic_ns=t*1000000,valid_until_monotonic_ns=t*1000000,offset_bound_ms=1,source_identity='synthetic'))


def test_actual_raw_receipt_and_normalized_objects(tmp_path):
    intake,root=setup(tmp_path);b=raw(message());out=intake.receive(b,clock=clock(120010))
    c=out['commit']
    assert (root/(c['raw_payload_hash']+'.blob')).read_bytes()==b
    for field,obj in [('receipt_hash',out['receipt']),('normalized_payload_hash',out['payload']),('clock_probe_hash',out['probe'])]:
        assert json.loads((root/(c[field]+'.blob')).read_bytes())==obj
    assert c['cumulative_raw_bytes']==len(b) and not out['runtime_accepted']
    original=H(out['receipt']);out['receipt']['receipt_seq']=999
    nextout=intake.receive(raw(message('quote')),clock=clock(120011))
    assert nextout['receipt']['previous_receipt_hash']==original
    intake.close()


def test_duplicate_frames_count_against_cumulative_limit(tmp_path):
    b=raw(message());intake,root=setup(tmp_path,2*len(b))
    intake.receive(b,clock=clock(120010));out=intake.receive(b,clock=clock(120011))
    assert out['commit']['cumulative_raw_bytes']==2*len(b)
    before=set(root.iterdir())
    with pytest.raises(ValueError):intake.receive(b,clock=clock(120012))
    assert set(root.iterdir())==before and intake.stopped
    intake.close()


def test_unclosed_frame_is_retained_and_counted(tmp_path):
    intake,root=setup(tmp_path);m=message();m['k']['x']=False
    out=intake.receive(raw(m),clock=clock(120010))
    assert out['receipt'] is None and out['commit']['packet_seq']==1 and out['commit']['cumulative_raw_bytes']==len(raw(m))
    assert (root/(out['commit']['raw_payload_hash']+'.blob')).read_bytes()==raw(m)
    intake.close()


@pytest.mark.parametrize('fault',['clock','source','expired_probe','reentry','unknown_write'])
def test_failures_stop_without_retry(tmp_path,monkeypatch,fault):
    intake,root=setup(tmp_path);b=raw(message());c=clock(120010)
    if fault=='clock':c=lambda:dict(utc_ms=True)
    if fault=='source':b=b'{}'
    if fault=='expired_probe':
        sample=c();sample['probe']['valid_until_monotonic_ns']=0;c=lambda:sample
    if fault=='reentry':
        def c():
            try:intake.receive(b,clock=clock(120010))
            except ValueError:pass
            return clock(120010)()
    if fault=='unknown_write':
        put=intake.writer.put
        def uncertain(*a,**kw):put(*a,**kw);raise RuntimeError('lost ack')
        monkeypatch.setattr(intake.writer,'put',uncertain)
    with pytest.raises((ValueError,RuntimeError)):intake.receive(b,clock=c)
    assert intake.stopped
    with pytest.raises(ValueError):intake.receive(b,clock=clock(120011))
    intake.close()


@pytest.mark.parametrize('changes',[{'sequence':0},{'previous_reservation_hash':'a'*64},{'sequence':2},{'reserved_utc_ms':60000},{'reserved_utc_ms':60001}])
def test_invalid_reservation_refused_before_claim(tmp_path,changes):
    p=plan();p.update(provider='binance-public-spot',instrument='BTC:USDT')
    r=dict(schema_version='p6.reservation.v1',plan_hash=H(p),authorization_hash='a'*64,registry_identity='b'*64,sequence=1,reserved_utc_ms=1,previous_reservation_hash='0'*64)
    r.update(changes);root=tmp_path/'invalid';root.mkdir()
    writer=ObjectWriter(str(root),owner='test',run_id=H(r),max_bytes=1000000)
    with pytest.raises(ValueError):SourceIntake(writer,plan=p,reservation=r)
    assert writer._claim is None and list(root.iterdir())==[root/'writer.json']
    writer.close()
