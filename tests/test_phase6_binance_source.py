import copy,hashlib,json
import pytest
from app.research.phase6_validation import canonical_hash as H


def message(kind='bar'):
    if kind=='bar':
        return dict(e='kline',E=120000,s='BTCUSDT',k=dict(t=60000,T=119999,s='BTCUSDT',i='1m',f=1,L=2,o='100.00000000',c='101.00000000',h='102.00000000',l='99.00000000',v='10.00000000',n=2,x=True,q='1000.00000000',V='5.00000000',Q='500.00000000',B='0'))
    return dict(e='24hrTicker',E=120001,s='BTCUSDT',p='-1.00000000',P='-1.00',w='100.00000000',x='100.00000000',c='100.00000000',Q='1.00000000',b='99.99000000',B='2.00000000',a='100.01000000',A='3.00000000',o='101.00000000',h='102.00000000',l='98.00000000',v='1000.00000000',q='100000.00000000',O=0,C=120000,F=1,L=2,n=2)


def raw(m):return json.dumps(m,separators=(',',':')).encode()
def normalize(b):
    from app.research.phase6_binance_source import normalize_message
    return normalize_message(b)


@pytest.mark.parametrize('kind',['bar','quote'])
def test_exact_raw_and_normalized_binding(kind):
    m=message(kind);b=raw(m);r=normalize(b)
    assert r['status']=='ACCEPTED' and r['raw_payload_hash']==hashlib.sha256(b).hexdigest()
    assert r['normalized_payload_hash']==H(r['payload']) and r['provider_event_ms']==m['E']
    assert r['payload']['provider']=='binance-public-spot' and r['payload']['instrument']=='BTC:USDT'
    assert not r['runtime_accepted'] and not r['source_authenticated']
    if kind=='bar':assert r['payload']['close_ms']==120000 and r['payload']['open']=='100'
    else:assert r['payload']['bid']=='99.99' and r['payload']['ask']=='100.01'


def test_unclosed_is_ignored_without_bar():
    m=message();m['k']['x']=False;m['E']=60001;r=normalize(raw(m))
    assert r['status']=='IGNORED' and r['reason_code']=='BAR_NOT_CLOSED' and r['payload'] is None


@pytest.mark.parametrize('fault',['symbol','nested_symbol','interval','timeframe','alignment','flag','event_float','bool_time','high','low','negative_volume','zero_price','exponent','nan','numeric_price','leading_zero','precision','early_final','extra','missing','wrapper'])
def test_invalid_bar(fault):
    m=message()
    if fault=='symbol':m['s']='ETHUSDT'
    if fault=='nested_symbol':m['k']['s']='ETHUSDT'
    if fault=='interval':m['k']['i']='5m'
    if fault=='timeframe':m['k']['T']+=1
    if fault=='alignment':m['k']['t']+=1;m['k']['T']+=1
    if fault=='flag':m['k']['x']=1
    if fault=='event_float':m['E']=120000.0
    if fault=='bool_time':m['k']['t']=True
    if fault=='high':m['k']['h']='90'
    if fault=='low':m['k']['l']='110'
    if fault=='negative_volume':m['k']['v']='-1'
    if fault=='zero_price':m['k']['o']='0'
    if fault=='exponent':m['k']['o']='1e2'
    if fault=='nan':m['k']['o']='NaN'
    if fault=='numeric_price':m['k']['o']=100.0
    if fault=='leading_zero':m['k']['o']='0100'
    if fault=='precision':m['k']['o']='100.00000000000000000001'
    if fault=='early_final':m['E']=119998
    if fault=='extra':m['extra']=1
    if fault=='missing':del m['k']['x']
    if fault=='wrapper':m=dict(stream='btcusdt@kline_1m',data=m)
    assert normalize(raw(m))['status']=='INVALID'


@pytest.mark.parametrize('field,value',[('b','101'),('a','0'),('b','-1'),('e','bookTicker'),('s','ETHUSDT'),('E',True),('n',1.0),('B','NaN')])
def test_invalid_quote(field,value):
    m=message('quote');m[field]=value;assert normalize(raw(m))['status']=='INVALID'


@pytest.mark.parametrize('b',[None,{},'',b'',b'[]',b'{"e":"kline","e":"24hrTicker"}',b'{"x":NaN}',b'\xff',b' '*65537])
def test_malformed_bytes(b):assert normalize(b)['status']=='INVALID'


def test_whitespace_changes_raw_hash_only():
    m=message();a=normalize(raw(m));b=normalize(json.dumps(m,indent=2).encode())
    assert a['raw_payload_hash']!=b['raw_payload_hash'] and a['normalized_payload_hash']==b['normalized_payload_hash']


def test_link_checker_cannot_trust_supplied_payload_or_receipt():
    from app.research.phase6_binance_source import verify_source_binding
    b=raw(message());n=normalize(b);receipt=dict(kind=n['kind'],provider_event_ms=n['provider_event_ms'],raw_payload_hash=n['raw_payload_hash'],normalized_payload_hash=n['normalized_payload_hash'])
    assert verify_source_binding(b,receipt,n['payload'])['binding_status']=='VALID'
    bad=copy.deepcopy(n['payload']);bad['close']='99';receipt['normalized_payload_hash']=H(bad)
    assert verify_source_binding(b,receipt,bad)['binding_status']=='INVALID'


def test_normalized_data_passes_existing_intake_validator():
    from test_phase6_intake_validation import fixture
    from app.research.phase6_intake_validation import verify_intake_prefix
    q=fixture();p=q['plan'];p.update(provider='binance-public-spot',instrument='BTC:USDT');q['reservation']['plan_hash']=H(p);run=H(q['reservation'])
    b=raw(message());n=normalize(b);q['payloads']={H(n['payload']):n['payload']}
    e=q['receipts'][0];e.update(run_id=run,provider_event_ms=n['provider_event_ms'],raw_payload_hash=n['raw_payload_hash'],normalized_payload_hash=n['normalized_payload_hash'])
    s=q['segments'][0];s.update(run_id=run,bar_hash=n['normalized_payload_hash'],accepted_receipt_hash=H(e));q['prefix'].update(run_id=run,segment_hashes=[H(s)])
    assert verify_intake_prefix(q)['binding_status']=='VALID'
    from app.research.phase6_binance_source import verify_source_binding
    assert verify_source_binding(b,e,n['payload'])['binding_status']=='VALID'


@pytest.mark.parametrize('kind',['bar','quote'])
@pytest.mark.parametrize('price',['100.000000001','1000000000000.01'])
def test_raw_price_matches_reducer_input_domain(kind,price):
    m=message(kind)
    if kind=='bar':
        for field in ('o','c','h','l'):m['k'][field]=price
    else:m.update(b=price,a=price)
    assert normalize(raw(m))['status']=='INVALID'
