"""Pinned Binance BTCUSDT raw-message normalization. No transport or clock authority."""
import hashlib
import json
import re
from decimal import Decimal
from app.research.phase6_validation import canonical_hash as H, _pairs, _shape as receipt_shape
from app.research.phase6_start_validation import _snapshot

VERSION='p6.binance_spot_source.v1'
PROVIDER='binance-public-spot'
INSTRUMENT='BTC:USDT'
MAX_BYTES=65536


def need(ok,reason='SOURCE_SCHEMA_INVALID'):
    if not ok:raise ValueError(reason)


def integer(v):return type(v) is int and 0<=v<2**63


def number(v,*,signed=False,price=False):
    need(type(v) is str and len(v)<=64 and re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,18})?',v) is not None)
    d=Decimal(v);need(signed or d>=0,'SOURCE_DATA_INVALID')
    normalized=format(d,'f')
    if '.' in normalized:normalized=normalized.rstrip('0').rstrip('.')
    if d==0:normalized='0'
    if price:
        need(0<d<=Decimal('1000000000000') and ('.' not in normalized or len(normalized.split('.')[1])<=8),'SOURCE_DATA_INVALID')
    return normalized


def decode(raw):
    need(type(raw) is bytes and 0<len(raw)<=MAX_BYTES)
    def reject(_):raise ValueError('SOURCE_SCHEMA_INVALID')
    value=json.loads(raw.decode('ascii'),object_pairs_hook=_pairs,parse_float=reject,parse_constant=reject)
    need(type(value) is dict);return value


def normalize_message(raw):
    result=dict(schema_version='p6.source_normalization.v1',adapter_version=VERSION,status='INVALID',
        reason_code=None,raw_payload_hash=None,normalized_payload_hash=None,payload=None,
        provider_event_ms=None,kind=None,runtime_accepted=False,source_authenticated=False)
    try:
        m=decode(raw);digest=hashlib.sha256(raw).hexdigest()
        need(m.get('s')=='BTCUSDT','SOURCE_IDENTITY_MISMATCH');need(integer(m.get('E')))
        if m.get('e')=='kline':
            need(set(m)=={'e','E','s','k'});k=m['k']
            need(type(k) is dict and set(k)==set('t T s i f L o c h l v n x q V Q B'.split()))
            need(k['s']=='BTCUSDT' and k['i']=='1m','SOURCE_IDENTITY_MISMATCH')
            need(all(integer(k[x]) for x in ('t','T','f','L','n')) and type(k['x']) is bool)
            need(k['t']%60000==0 and k['T']+1==k['t']+60000 and k['T']+1<2**63,'SOURCE_DATA_INVALID')
            prices={x:number(k[x],price=True) for x in ('o','c','h','l')}
            volume=number(k['v'])
            for x in ('q','V','Q','B'):number(k[x])
            a,c,h,l=(Decimal(prices[x]) for x in ('o','c','h','l'))
            need(l<=min(a,c)<=max(a,c)<=h,'SOURCE_DATA_INVALID')
            result['raw_payload_hash']=digest
            if not k['x']:
                result.update(status='IGNORED',reason_code='BAR_NOT_CLOSED');return result
            need(m['E']>=k['T'],'SOURCE_CLOCK_INVALID')
            payload=dict(schema_version='p6.bar.v1',provider=PROVIDER,instrument=INSTRUMENT,
                open_ms=k['t'],close_ms=k['T']+1,open=prices['o'],high=prices['h'],low=prices['l'],close=prices['c'],volume=volume)
            kind='BAR_FINAL'
        elif m.get('e')=='24hrTicker':
            need(set(m)==set('e E s p P w x c Q b B a A o h l v q O C F L n'.split()))
            need(all(integer(m[x]) for x in ('O','C','F','L','n')))
            for x in ('p','P'):number(m[x],signed=True)
            for x in ('w','x','c','Q','B','A','o','h','l','v','q'):number(m[x])
            bid,ask=number(m['b'],price=True),number(m['a'],price=True)
            need(Decimal(bid)<=Decimal(ask),'SOURCE_DATA_INVALID')
            payload=dict(schema_version='p6.quote.v1',provider=PROVIDER,instrument=INSTRUMENT,bid=bid,ask=ask)
            kind='QUOTE'
        else:raise ValueError('SOURCE_EVENT_UNSUPPORTED')
    except (ValueError,TypeError,KeyError,OverflowError,RecursionError) as exc:
        reason=str(exc)
        result['reason_code']=reason if reason.startswith('SOURCE_') else 'SOURCE_SCHEMA_INVALID'
    else:result.update(status='ACCEPTED',raw_payload_hash=digest,normalized_payload_hash=H(payload),payload=payload,provider_event_ms=m['E'],kind=kind)
    return result


def verify_source_binding(raw,receipt,payload):
    """Check raw→normalized linkage only; receipt chronology/full intake stays upstream."""
    result=dict(schema_version='p6.source_binding_validation.v1',binding_status='INVALID',reason_code=None,
                runtime_accepted=False,source_authenticated=False,receipt_chronology_verified=False)
    try:
        q=_snapshot(dict(receipt=receipt,payload=payload));r=q['receipt'];n=normalize_message(raw)
        need(n['status']=='ACCEPTED','SOURCE_NOT_ACCEPTED')
        fields={'kind','provider_event_ms','raw_payload_hash','normalized_payload_hash'}
        need(type(r) is dict and (set(r)==fields or receipt_shape(r,'p6.receipt.v1')))
        need(all(type(r[k]) is type(n[k]) and r[k]==n[k] for k in fields),'SOURCE_BINDING_MISMATCH')
        need(json.dumps(q['payload'],sort_keys=True,separators=(',',':'))==json.dumps(n['payload'],sort_keys=True,separators=(',',':')),'SOURCE_BINDING_MISMATCH')
    except Exception:
        result['reason_code']='SOURCE_BINDING_INVALID'
    else:result['binding_status']='VALID'
    return result
