import copy
import pytest
from test_phase6_validation import request,next_segment
from app.research.phase6_validation import canonical_hash as H


def fixture(two=False):
    q=next_segment() if two else request('SEGMENT')
    segments=([q['prior_segment']] if two else [])+[q['document']]
    return dict(plan=q['plan'],reservation=q['reservation'],receipts=q['prior_receipts'],segments=segments,
        payloads=q['payloads'],clock_probes=q['clock_probes'],prefix=dict(schema_version='p6.accepted_prefix.v1',run_id=H(q['reservation']),segment_hashes=[H(s) for s in segments]))


def verify(q):
    from app.research.phase6_intake_validation import verify_intake_prefix
    return verify_intake_prefix(q)


def append(q,kind='QUOTE',revision=False):
    e=copy.deepcopy(q['receipts'][-1]);e.update(receipt_seq=e['receipt_seq']+1,received_utc_ms=e['received_utc_ms']+1,received_monotonic_ns=e['received_monotonic_ns']+1,previous_receipt_hash=H(q['receipts'][-1]),kind=kind)
    if kind=='QUOTE':
        p=dict(schema_version='p6.quote.v1',provider=q['plan']['provider'],instrument=q['plan']['instrument'],bid='100',ask='101')
        q['payloads'][H(p)]=p;e['normalized_payload_hash']=H(p)
    elif revision:
        p=copy.deepcopy(q['payloads'][e['normalized_payload_hash']]);p['volume']='11';q['payloads'][H(p)]=p;e['normalized_payload_hash']=H(p)
    q['receipts'].append(e)


@pytest.mark.parametrize('two',[False,True])
@pytest.mark.parametrize('extra',['none','quote','duplicate'])
def test_ordered_prefix_no_quote_features(two,extra):
    q=fixture(two)
    if extra!='none':append(q,'QUOTE' if extra=='quote' else 'BAR_FINAL')
    before=copy.deepcopy(q);r=verify(q)
    assert r['binding_status']=='VALID',r
    assert r['accepted_prefix_hash']==H(q['prefix']) and r['bar_hashes']==[s['bar_hash'] for s in q['segments']]
    assert r['last_receipt_seq']==len(q['receipts']) and not r['runtime_accepted'] and not r['callback_inputs_verified']
    assert q==before


def test_revision_rejected():
    q=fixture();append(q,'BAR_FINAL',True)
    assert verify(q)['reason_code']=='REVISION_CONFLICT'


@pytest.mark.parametrize('fault',['receipt_gap','receipt_hash','clock','payload_hash','probe_hash','missing_payload','extra_payload','extra_probe','segment_gap','segment_hash','prefix_reverse','prefix_run','prefix_extra','omit_segment','duplicate_segment','future_bar'])
def test_bad_chain_or_prefix(fault):
    q=fixture(True)
    if fault=='receipt_gap':q['receipts'][-1]['receipt_seq']=4
    if fault=='receipt_hash':q['receipts'][-1]['previous_receipt_hash']='a'*64
    if fault=='clock':q['receipts'][-1]['clock_session_id']='changed'
    if fault=='payload_hash':next(iter(q['payloads'].values()))['volume']='9'
    if fault=='probe_hash':next(iter(q['clock_probes'].values()))['offset_bound_ms']=2
    if fault=='missing_payload':q['payloads'].pop(q['segments'][0]['bar_hash'])
    if fault=='extra_payload':q['payloads']['a'*64]=copy.deepcopy(next(iter(q['payloads'].values())))
    if fault=='extra_probe':q['clock_probes']['a'*64]=copy.deepcopy(next(iter(q['clock_probes'].values())))
    if fault=='segment_gap':q['segments'][1]['segment_seq']=3
    if fault=='segment_hash':q['segments'][1]['previous_segment_hash']='a'*64
    if fault=='prefix_reverse':q['prefix']['segment_hashes'].reverse()
    if fault=='prefix_run':q['prefix']['run_id']='a'*64
    if fault=='prefix_extra':q['prefix']['future']='x'
    if fault=='omit_segment':q['segments'].pop();q['prefix']['segment_hashes'].pop()
    if fault=='duplicate_segment':q['segments'].append(copy.deepcopy(q['segments'][-1]));q['prefix']['segment_hashes'].append(H(q['segments'][-1]))
    if fault=='future_bar':q['receipts'].pop()
    assert verify(q)['binding_status']=='INVALID'


@pytest.mark.parametrize('field',['plan','reservation','receipts','segments','payloads','clock_probes','prefix'])
def test_missing_fields(field):
    q=fixture();del q[field];assert verify(q)['binding_status']=='INVALID'


@pytest.mark.parametrize('value',[None,[],{},True])
def test_bad_input(value):assert verify(value)['binding_status']=='INVALID'


def test_limits_and_empty():
    q=fixture();q['receipts']*=65;assert verify(q)['binding_status']=='INVALID'
    q=fixture();q['segments']=[];assert verify(q)['binding_status']=='INVALID'


def rebind(q):
    previous='0'*64;first={}
    for i,e in enumerate(q['receipts'],1):
        e.update(receipt_seq=i,previous_receipt_hash=previous);previous=H(e)
        if e['kind']=='BAR_FINAL':first.setdefault(e['normalized_payload_hash'],H(e))
    previous='0'*64
    for s in q['segments']:
        s['accepted_receipt_hash']=first[s['bar_hash']];s['previous_segment_hash']=previous;previous=H(s)
    q['prefix']['segment_hashes']=[H(s) for s in q['segments']]


@pytest.mark.parametrize('kind',['QUOTE','BAR_FINAL'])
def test_interleaved_receipts_preserve_segment_order(kind):
    q=fixture(True);one=fixture();append(one,kind);e=one['receipts'][-1]
    q['payloads'].update(one['payloads']);q['receipts'].insert(1,e);rebind(q)
    r=verify(q);assert r['binding_status']=='VALID' and len(r['bar_hashes'])==2


def test_quote_before_first_bar():
    q=fixture();append(q);quote=q['receipts'].pop();quote.update(received_utc_ms=120005,received_monotonic_ns=90)
    q['receipts'].insert(0,quote);rebind(q)
    assert verify(q)['binding_status']=='VALID'


def test_duplicate_cannot_be_accepted_receipt():
    q=fixture();append(q,'BAR_FINAL');q['segments'][0]['accepted_receipt_hash']=H(q['receipts'][-1]);q['prefix']['segment_hashes']=[H(q['segments'][0])]
    assert verify(q)['binding_status']=='INVALID'


def test_reordered_first_bars_refused_even_rehashed():
    q=fixture(True);q['receipts'].reverse()
    for i,e in enumerate(q['receipts']):e.update(received_utc_ms=180020+i,received_monotonic_ns=300+i)
    q['segments'].reverse();rebind(q)
    assert verify(q)['binding_status']=='INVALID'


def test_trailing_quote_clock_checked():
    q=fixture();append(q);q['receipts'][-1]['received_monotonic_ns']=1001
    assert verify(q)['reason_code']=='CLOCK_INVALID'


def test_64_receipts_supported():
    q=fixture()
    for _ in range(63):append(q,'BAR_FINAL')
    r=verify(q);assert r['binding_status']=='VALID' and r['last_receipt_seq']==64 and len(r['bar_hashes'])==1


def test_valid_but_unreferenced_payload_refused():
    q=fixture();p=copy.deepcopy(next(iter(q['payloads'].values())));p.update(open_ms=120000,close_ms=180000)
    q['payloads'][H(p)]=p
    assert verify(q)['reason_code']=='INVENTORY_MISMATCH'
