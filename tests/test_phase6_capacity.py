"""Synthetic capacity checks, not a prospective pilot or end-to-end acceptance."""
import copy
import pytest
from test_phase6_intake_validation import fixture, rebind
from app.research.phase6_validation import canonical_hash as H
from app.research.phase6_intake_validation import verify_intake_prefix
from app.research.phase6_start_validation import _snapshot, _Refused
from app.research.phase6_capacity import V2


def large_prefix(count=1830):
    q=fixture(); original=copy.deepcopy(q['receipts'][0]); probe=next(iter(q['clock_probes'].values()))
    q['receipts']=[];q['clock_probes']={}
    for i in range(count-1):
        e=copy.deepcopy(original)
        price=dict(schema_version='p6.quote.v1',provider=q['plan']['provider'],instrument=q['plan']['instrument'],bid=str(100+i),ask=str(101+i))
        t=dict(probe,measured_utc_ms=60000+i,measured_monotonic_ns=i+1,valid_until_monotonic_ns=i+1)
        q['payloads'][H(price)]=price;q['clock_probes'][H(t)]=t
        e.update(kind='QUOTE',normalized_payload_hash=H(price),clock_probe_hash=H(t),provider_event_ms=60000+i,received_utc_ms=60000+i,received_monotonic_ns=i+1)
        q['receipts'].append(e)
    original['received_monotonic_ns']=count+1
    t=dict(probe,valid_until_monotonic_ns=count+1)
    q['clock_probes'][H(t)]=t;original['clock_probe_hash']=H(t)
    q['receipts'].append(original);rebind(q)
    return q


def test_v2_retains_1830_distinct_receipts_and_default_stays_bounded():
    q=large_prefix();before=H(q)
    assert verify_intake_prefix(q)['binding_status']=='INVALID'
    out=verify_intake_prefix(q,capacity=V2)
    assert out['binding_status']=='VALID',out
    assert out['last_receipt_seq']==1830 and H(q)==before
    assert not out['runtime_accepted']


@pytest.mark.parametrize('fault',['receipt','payload','probe','sequence'])
def test_large_prefix_still_checks_early_history(fault):
    q=large_prefix()
    if fault=='receipt':q['receipts'][10]['previous_receipt_hash']='f'*64
    if fault=='payload':q['payloads'][q['receipts'][10]['normalized_payload_hash']]['bid']='99999'
    if fault=='probe':q['clock_probes'][q['receipts'][10]['clock_probe_hash']]['offset_bound_ms']=999
    if fault=='sequence':q['receipts'][10]['receipt_seq']=999
    assert verify_intake_prefix(q,capacity=V2)['binding_status']=='INVALID'


def test_limits_reject_unknown_profile_and_unbounded_input():
    with pytest.raises(ValueError):_snapshot({},capacity='unbounded')
    with pytest.raises(_Refused):_snapshot([0]*4097,capacity=V2)
    with pytest.raises(_Refused):_snapshot({str(i):'x'*4096 for i in range(4096)},capacity=V2)
    with pytest.raises(_Refused):_snapshot({str(i):0 for i in range(65)})


def test_actual_journal_binds_large_prefix(tmp_path):
    from test_phase6_event_validation import case
    from app.research.phase6_object_store import ObjectWriter
    from app.research.phase6_event_journal import EventJournal
    q=large_prefix();m=case()['manifest'];segment=q['segments'][-1]
    m.update(run_id=H(q['reservation']),plan_hash=H(q['plan']),segment_id=H(segment),persisted_utc_ms=120011)
    root=tmp_path/'capacity';root.mkdir()
    w=ObjectWriter(str(root),owner='test',run_id=m['run_id'],max_bytes=16*1024*1024)
    j=EventJournal(w,m,utc_ms=120011,monotonic_ns=10000,capacity=V2)
    j.append('BAR_ACCEPTED',dict(schema_version='p6.payload.bar_accepted.v1',segment_hash=H(segment),receipt_hash=segment['accepted_receipt_hash'],bar_hash=segment['bar_hash'],accepted_prefix_hash=H(q['prefix'])),utc_ms=120012,monotonic_ns=10001)
    result=j.bind_intake(q)
    import json
    stored=json.loads((root/(result['bundle_hash']+'.blob')).read_bytes())
    assert stored==q and len(stored['receipts'])==1830
    j.close()


@pytest.mark.parametrize('field', ['run_id','segment_seq','start_ms','end_ms','bar_hash','accepted_receipt_hash','previous_segment_hash','schema_version'])
def test_optimized_segments_reject_same_corruption_as_v1(field):
    from test_phase6_intake_validation import fixture
    q=fixture(True)
    value=q['segments'][1][field]
    q['segments'][1][field]=value+1 if type(value) is int else 'f'*64
    q['prefix']['segment_hashes']=[H(s) for s in q['segments']]
    assert verify_intake_prefix(q)['binding_status']=='INVALID'
    assert verify_intake_prefix(q,capacity=V2)['binding_status']=='INVALID'
