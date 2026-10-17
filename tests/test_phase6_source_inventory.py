import pytest
from test_phase6_source_intake import setup,clock
from test_phase6_binance_source import raw,message
from app.research.phase6_source_inventory import SourceInventory
from app.research.phase6_validation import canonical_hash as H


def test_actual_intake_builds_complete_prefix_without_dropping_duplicates(tmp_path):
    intake,root=setup(tmp_path)
    inventory=SourceInventory(plan=intake.plan,reservation=intake.reservation)
    first=inventory.accept(intake.receive(raw(message()),clock=clock(120010)))
    second=inventory.accept(intake.receive(raw(message('quote')),clock=clock(120011)))
    duplicate=inventory.accept(intake.receive(raw(message()),clock=clock(120012)))
    m=message();m['k']['x']=False
    ignored=inventory.accept(intake.receive(raw(m),clock=clock(120013)))
    q=inventory.bundle()
    assert first['new_segment'] and not second['new_segment'] and not duplicate['new_segment']
    assert len(q['receipts'])==3 and len(q['segments'])==1 and ignored['packet_count']==4
    assert q['segments'][0]['accepted_receipt_hash']==H(q['receipts'][0])
    q['receipts'].clear();assert len(inventory.bundle()['receipts'])==3
    intake.close()


@pytest.mark.parametrize('fault',['gap','commit','payload','revision'])
def test_bad_input_stops_inventory(tmp_path,fault):
    intake,root=setup(tmp_path);inventory=SourceInventory(plan=intake.plan,reservation=intake.reservation)
    first=intake.receive(raw(message()),clock=clock(120010))
    if fault=='revision':
        inventory.accept(first);m=message();m['k']['v']='11'
        bad=intake.receive(raw(m),clock=clock(120011))
    else:
        bad=first
        if fault=='gap':bad['commit']['packet_seq']=2;bad['commit_hash']=H(bad['commit'])
        if fault=='commit':bad['commit_hash']='f'*64
        if fault=='payload':bad['payload']['close']='102'
    with pytest.raises(ValueError):inventory.accept(bad)
    assert inventory.stopped
    with pytest.raises(ValueError):inventory.bundle()
    intake.close()


@pytest.mark.parametrize('fault',['bool_sequence','bool_bytes','unknown_result','unknown_commit','probe_schema','probe_time','probe_session','skew','backward','session'])
def test_ignored_frames_have_the_same_strict_clock_boundary(tmp_path,fault):
    intake,root=setup(tmp_path);inventory=SourceInventory(plan=intake.plan,reservation=intake.reservation)
    m=message();m['k']['x']=False
    first=intake.receive(raw(m),clock=clock(120010))
    inventory.accept(first)
    bad=intake.receive(raw(m),clock=clock(120011))
    c=bad['commit'];probe=bad['probe']
    if fault=='bool_sequence':c['packet_seq']=True
    if fault=='bool_bytes':c['raw_bytes']=True
    if fault=='unknown_result':bad['extra']=1
    if fault=='unknown_commit':c['extra']=1
    if fault=='probe_schema':probe['offset_bound_ms']=True
    if fault=='probe_time':probe['measured_utc_ms']=120012
    if fault=='probe_session':probe['clock_session_id']='different'
    if fault=='skew':probe['offset_bound_ms']=intake.plan['max_clock_skew_ms']+1
    if fault=='backward':
        c['received_utc_ms']=120009;c['received_monotonic_ns']=120009000000
        probe.update(measured_utc_ms=120009,measured_monotonic_ns=120009000000)
    if fault=='session':c['clock_session_id']=probe['clock_session_id']='different'
    c['clock_probe_hash']=H(probe);bad['commit_hash']=H(c)
    with pytest.raises(ValueError):inventory.accept(bad)
    assert inventory.stopped
    intake.close()


@pytest.mark.parametrize('fault',['bool_sequence','zero_sequence','previous','late','extra'])
def test_reservation_must_be_valid_before_inventory(tmp_path,fault):
    import copy
    intake,root=setup(tmp_path);r=copy.deepcopy(intake.reservation)
    if fault=='bool_sequence':r['sequence']=True
    if fault=='zero_sequence':r['sequence']=0
    if fault=='previous':r['previous_reservation_hash']='a'*64
    if fault=='late':r['reserved_utc_ms']=intake.plan['start_ms']
    if fault=='extra':r['extra']=1
    with pytest.raises(ValueError):SourceInventory(plan=intake.plan,reservation=r)
    intake.close()
