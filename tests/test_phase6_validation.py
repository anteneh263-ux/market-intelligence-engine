import copy
import json
from pathlib import Path
import pytest
from app.research.phase6_validation import validate, canonical_hash

H='1'*64

def plan():
    p=dict(schema_version='p6.plan.v1',profile='p6.shadow.diagnostic.v1',owner='test',provider='synthetic',instrument='TEST:USD',timeframe_ms=60000,start_ms=60000,end_ms=240000,warmup_bars=0,max_lateness_ms=1000,max_clock_skew_ms=10,max_decision_delay_ms=100,max_quote_delay_ms=100,tick_size='0.01',quantity_step='0.01',initial_cash='1000',max_target='0.5',fee_bps='10',slippage_bps='5',max_drawdown='0.2',intake_limit_bytes=10000)
    for k in ('program_context_hash','known_lineage_hash','strategy_code_hash','params_hash','method_contract_hash','anchor_trust_hash'):p[k]=H
    return p

def request(kind='BAR'):
    p=plan();b=dict(schema_version='p6.bar.v1',provider=p['provider'],instrument=p['instrument'],open_ms=60000,close_ms=120000,open='100',high='102',low='99',close='101',volume='10')
    r=dict(schema_version='p6.reservation.v1',plan_hash=canonical_hash(p),authorization_hash=H,registry_identity=H,sequence=1,reserved_utc_ms=1000,previous_reservation_hash='0'*64)
    t=dict(schema_version='p6.clock_probe.v1',clock_session_id='clock1',measured_utc_ms=119000,measured_monotonic_ns=10,valid_until_monotonic_ns=1000,offset_bound_ms=1,source_identity='test-clock')
    e=dict(schema_version='p6.receipt.v1',run_id=canonical_hash(r),receipt_seq=1,kind='BAR_FINAL',provider_event_ms=120000,received_utc_ms=120010,received_monotonic_ns=100,clock_session_id='clock1',clock_probe_hash=canonical_hash(t),raw_payload_hash=H,normalized_payload_hash=canonical_hash(b),previous_receipt_hash='0'*64)
    s=dict(schema_version='p6.segment.v1',run_id=canonical_hash(r),segment_seq=1,start_ms=60000,end_ms=120000,bar_hash=canonical_hash(b),accepted_receipt_hash=canonical_hash(e),previous_segment_hash='0'*64)
    return dict(document={'PLAN':p,'BAR':b,'RECEIPT':e,'SEGMENT':s}[kind],plan=None if kind=='PLAN' else p,reservation=r if kind in ('RECEIPT','SEGMENT') else None,prior_receipts=[e] if kind=='SEGMENT' else [],prior_segment=None,payloads={canonical_hash(b):b} if kind in ('RECEIPT','SEGMENT') else {},clock_probes={canonical_hash(t):t} if kind in ('RECEIPT','SEGMENT') else {})

@pytest.mark.parametrize('kind',['PLAN','BAR','RECEIPT','SEGMENT'])
def test_valid_and_no_authority(kind):
    q=request(kind);old=copy.deepcopy(q);r=validate(q)
    assert r['structural_status']=='VALID',r
    assert r['external_reference_verification']=='UPSTREAM_REQUIRED'
    assert r['runtime_accepted'] is False and q==old
    assert r['canonical_hash']==canonical_hash(q['document'])

@pytest.mark.parametrize('value',[True,1.0,-1,2**63])
def test_invalid_integer(value):
    q=request();q['document']['open_ms']=value
    assert validate(q)['reason_code']=='SCHEMA_INVALID'

@pytest.mark.parametrize('value',['-0','01','1.0','NaN','1e2','+1'])
def test_noncanonical_decimal(value):
    q=request();q['document']['open']=value
    assert validate(q)['reason_code']=='SCHEMA_INVALID'

def test_duplicate_json_key():
    q=request();raw=json.dumps(q).replace('"open": "100"','"open": "100", "open": "100"')
    assert validate(raw)['reason_code']=='SCHEMA_INVALID'

def test_context_schema_precedes_identity():
    q=request('RECEIPT');q['document']['run_id']='2'*64;q['plan']['warmup_bars']=True
    assert validate(q)['reason_code']=='SCHEMA_INVALID'

def test_run_is_bound_to_plan():
    q=request('RECEIPT');q['plan']['owner']='other'
    assert validate(q)['reason_code']=='IDENTITY_MISMATCH'

def test_receipt_lateness():
    q=request('RECEIPT');q['document']['received_utc_ms']=121001
    assert validate(q)['reason_code']=='CLOCK_INVALID'

def test_hash_map_keys_are_not_trusted():
    q=request('RECEIPT');next(iter(q['payloads'].values()))['volume']='11'
    assert validate(q)['reason_code']=='HASH_MISMATCH'

def test_segment_receipt_binds_same_bar():
    q=request('SEGMENT');q['document']['bar_hash']='2'*64
    assert validate(q)['reason_code']=='IDENTITY_MISMATCH'

def test_prefix_gap():
    q=request('RECEIPT');q['document']['receipt_seq']=2
    assert validate(q)['reason_code']=='SEQUENCE_INVALID'

def test_clock_probe_expired():
    q=request('RECEIPT');t=next(iter(q['clock_probes'].values()));t['valid_until_monotonic_ns']=99
    assert validate(q)['reason_code']=='CLOCK_INVALID'

def test_unknown_fields():
    q=request();q['document']['scientific_pass']=True
    assert validate(q)['reason_code']=='SCHEMA_INVALID'

def test_golden_vectors():
    vectors=json.loads((Path(__file__).parents[1]/'docs/specs/phase6-wp0-synthetic-vectors.json').read_text())
    for v in vectors['vectors']:
        q=request();q['plan'].update(start_ms=0,end_ms=180000);q['document']=v['input']
        r=validate(q);assert r['structural_status']==v['expected'],(v['id'],r)
        if v['expected']=='VALID':assert r['canonical_hash']==v['sha256']
        else:assert r['reason_code']==v['reason_code']

def next_segment():
    q=request('SEGMENT');first=copy.deepcopy(q['document']);q['prior_segment']=first
    b=copy.deepcopy(next(iter(q['payloads'].values())));b.update(open_ms=120000,close_ms=180000)
    e=copy.deepcopy(q['prior_receipts'][0]);e.update(receipt_seq=2,provider_event_ms=180000,received_utc_ms=180010,received_monotonic_ns=200,previous_receipt_hash=canonical_hash(q['prior_receipts'][0]),normalized_payload_hash=canonical_hash(b))
    q['payloads'][canonical_hash(b)]=b;q['prior_receipts'].append(e)
    q['document'].update(segment_seq=2,start_ms=120000,end_ms=180000,bar_hash=canonical_hash(b),accepted_receipt_hash=canonical_hash(e),previous_segment_hash=canonical_hash(first))
    return q

def test_second_segment():assert validate(next_segment())['structural_status']=='VALID'

@pytest.mark.parametrize('field',['bar_hash','accepted_receipt_hash'])
def test_predecessor_references_are_verified(field):
    q=next_segment();q['prior_segment'][field]='3'*64
    q['document']['previous_segment_hash']=canonical_hash(q['prior_segment'])
    assert validate(q)['reason_code']=='IDENTITY_MISMATCH'

def test_duplicate_receipt_cannot_be_another_segment():
    q=request('SEGMENT');e=copy.deepcopy(q['prior_receipts'][0]);e.update(receipt_seq=2,received_monotonic_ns=101,previous_receipt_hash=canonical_hash(q['prior_receipts'][0]))
    q['prior_receipts'].append(e);q['document']['accepted_receipt_hash']=canonical_hash(e)
    assert validate(q)['reason_code']=='SEQUENCE_INVALID'

def test_conflicting_bar_not_accepted():
    q=request('SEGMENT');b=copy.deepcopy(next(iter(q['payloads'].values())));b['volume']='11';q['payloads'][canonical_hash(b)]=b
    e=copy.deepcopy(q['prior_receipts'][0]);e.update(receipt_seq=2,received_monotonic_ns=101,previous_receipt_hash=canonical_hash(q['prior_receipts'][0]),normalized_payload_hash=canonical_hash(b))
    q['prior_receipts'].append(e);q['document'].update(bar_hash=canonical_hash(b),accepted_receipt_hash=canonical_hash(e))
    assert validate(q)['reason_code']=='SEQUENCE_INVALID'

def test_utf16_rejected():
    assert validate(json.dumps(request()).encode('utf-16'))['reason_code']=='SCHEMA_INVALID'

@pytest.mark.parametrize('bad',[b'\xff', '{', 'null', {'document':{}}, [1], None])
def test_bad_envelopes(bad):
    r=validate(bad);assert r['structural_status']=='INVALID' and r['canonical_hash'] is None and not r['runtime_accepted']

def test_missing_reservation():
    q=request('RECEIPT');q['reservation']=None
    assert validate(q)['reason_code']=='IDENTITY_MISMATCH'

def test_prefix_clock_rollback():
    q=next_segment();q['prior_receipts'][1]['received_monotonic_ns']=99
    q['document']['accepted_receipt_hash']=canonical_hash(q['prior_receipts'][1])
    assert validate(q)['reason_code']=='CLOCK_INVALID'

def test_quote_receipt():
    q=request('RECEIPT');b=dict(schema_version='p6.quote.v1',provider='synthetic',instrument='TEST:USD',bid='100',ask='101')
    q['document'].update(kind='QUOTE',normalized_payload_hash=canonical_hash(b));q['payloads']={canonical_hash(b):b}
    assert validate(q)['structural_status']=='VALID'
    b['bid']='102'
    assert validate(q)['reason_code']=='DATA_INVALID'

def test_pure_no_io(monkeypatch):
    import builtins,socket
    q=request('SEGMENT')
    def forbidden(*a,**kw):pytest.fail('unexpected I/O')
    monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    assert validate(q)['structural_status']=='VALID'

def test_unsupported_kind_reason():
    q=request('RECEIPT');q['document']['kind']='OTHER'
    assert validate(q)['reason_code']=='POLICY_UNSUPPORTED'

def test_unused_clock_probe_domain():
    q=request('RECEIPT');t=copy.deepcopy(next(iter(q['clock_probes'].values())))
    t.update(measured_monotonic_ns=2000,valid_until_monotonic_ns=1,offset_bound_ms=100)
    q['clock_probes'][canonical_hash(t)]=t
    assert validate(q)['reason_code']=='CLOCK_INVALID'
