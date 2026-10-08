"""Pure Phase 6 structural checks. Never authorizes intake or execution."""
import hashlib
import json
import re
from decimal import Decimal

ZERO = '0' * 64
ORDER = ('SCHEMA_INVALID', 'IDENTITY_MISMATCH', 'POLICY_UNSUPPORTED',
         'CLOCK_INVALID', 'DATA_INVALID', 'SEQUENCE_INVALID', 'HASH_MISMATCH')
TOKEN = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
DEC = re.compile(r'-?(0|[1-9][0-9]*)(\.[0-9]*[1-9])?\Z')
# s: ASCII token; h: SHA256; n: nonnegative signed64; d: canonical decimal.
SCHEMAS = {}
SCHEMAS['p6.plan.v1'] = dict(
    **{k:'s' for k in ('profile','owner','provider','instrument')},
    **{k:'h' for k in ('program_context_hash','known_lineage_hash','strategy_code_hash','params_hash','method_contract_hash','anchor_trust_hash')},
    **{k:'n' for k in ('timeframe_ms','start_ms','end_ms','warmup_bars','max_lateness_ms','max_clock_skew_ms','max_decision_delay_ms','max_quote_delay_ms','intake_limit_bytes')},
    **{k:'d' for k in ('tick_size','quantity_step','initial_cash','max_target','fee_bps','slippage_bps','max_drawdown')})
SCHEMAS['p6.bar.v1'] = dict(provider='s',instrument='s',open_ms='n',close_ms='n',open='d',high='d',low='d',close='d',volume='d')
SCHEMAS['p6.quote.v1'] = dict(provider='s',instrument='s',bid='d',ask='d')
SCHEMAS['p6.reservation.v1'] = dict(plan_hash='h',authorization_hash='h',registry_identity='h',sequence='n',reserved_utc_ms='n',previous_reservation_hash='h')
SCHEMAS['p6.receipt.v1'] = dict(run_id='h',receipt_seq='n',kind='s',provider_event_ms='n',received_utc_ms='n',received_monotonic_ns='n',clock_session_id='s',clock_probe_hash='h',raw_payload_hash='h',normalized_payload_hash='h',previous_receipt_hash='h')
SCHEMAS['p6.clock_probe.v1'] = dict(clock_session_id='s',measured_utc_ms='n',measured_monotonic_ns='n',valid_until_monotonic_ns='n',offset_bound_ms='n',source_identity='s')
SCHEMAS['p6.segment.v1'] = dict(run_id='h',segment_seq='n',start_ms='n',end_ms='n',bar_hash='h',accepted_receipt_hash='h',previous_segment_hash='h')
KINDS = {'p6.plan.v1':'PLAN','p6.bar.v1':'BAR','p6.receipt.v1':'RECEIPT','p6.segment.v1':'SEGMENT'}
FIELDS = {'document','plan','reservation','prior_receipts','prior_segment','payloads','clock_probes'}


def canonical_hash(value):
    """Canonical JSON digest, not validation or trust verification."""
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()


def _pairs(pairs):
    result = {}
    for k,v in pairs:
        if k in result: raise ValueError('duplicate key')
        result[k]=v
    return result


def _shape(value, schema):
    if type(value) is not dict or schema not in SCHEMAS: return False
    fields=SCHEMAS[schema]
    if set(value)!=set(fields)|{'schema_version'} or value['schema_version']!=schema:return False
    for k,t in fields.items():
        v=value[k]
        if t=='n':
            if type(v) is not int or not 0<=v<2**63:return False
        elif type(v) is not str or not v.isascii():return False
        elif t=='s' and not TOKEN.fullmatch(v):return False
        elif t=='h' and not HEX.fullmatch(v):return False
        elif t=='d' and (not DEC.fullmatch(v) or v=='-0'):return False
    return True


def validate(request):
    """Validate an in-memory envelope or raw UTF-8 JSON, without I/O.

    Raw JSON parsing rejects duplicate keys; decoded mappings cannot prove whether
    their original serialized form contained duplicates. All external trust remains
    UPSTREAM_REQUIRED even when every structural/content check passes.
    """
    kind='PLAN'
    try:
        if type(request) is bytes:request=request.decode('utf-8')
        if type(request) is str:
            request=json.loads(request,object_pairs_hook=_pairs,parse_constant=lambda x:(_ for _ in ()).throw(ValueError(x)))
        if type(request) is not dict or set(request)!=FIELDS:raise ValueError()
        q=request;doc=q['document']
        if type(doc) is not dict:raise ValueError()
        kind=KINDS.get(doc.get('schema_version'),'PLAN')
        if doc.get('schema_version') not in KINDS or not _shape(doc,doc['schema_version']):raise ValueError()
        if type(q['prior_receipts']) is not list or type(q['payloads']) is not dict or type(q['clock_probes']) is not dict:raise ValueError()
        for name,schema in [('plan','p6.plan.v1'),('reservation','p6.reservation.v1'),('prior_segment','p6.segment.v1')]:
            if q[name] is not None and not _shape(q[name],schema):raise ValueError()
        if not all(_shape(e,'p6.receipt.v1') for e in q['prior_receipts']):raise ValueError()
        for mapping,allowed in [(q['payloads'],('p6.bar.v1','p6.quote.v1')),(q['clock_probes'],('p6.clock_probe.v1',))]:
            for k,v in mapping.items():
                if type(k) is not str or not HEX.fullmatch(k) or type(v) is not dict or v.get('schema_version') not in allowed or not _shape(v,v['schema_version']):raise ValueError()
        errors=set()
        def check(condition,code):
            if not condition:errors.add(code)
        p=doc if kind=='PLAN' else q['plan']
        check(p is not None,'IDENTITY_MISMATCH')
        if kind in ('PLAN','BAR'):
            check(q['reservation'] is None and not q['prior_receipts'] and q['prior_segment'] is None and not q['payloads'] and not q['clock_probes'],'IDENTITY_MISMATCH')
            if kind=='PLAN':check(q['plan'] is None,'IDENTITY_MISMATCH')
        if kind=='RECEIPT':check(q['prior_segment'] is None,'IDENTITY_MISMATCH')
        if p is not None:
            check(p['profile']=='p6.shadow.diagnostic.v1','POLICY_UNSUPPORTED')
            tf=p['timeframe_ms'];start=p['start_ms'];end=p['end_ms']
            check(tf>0 and start<end and start%max(tf,1)==0 and end%max(tf,1)==0 and (end-start)//max(tf,1)>=p['warmup_bars']+2,'DATA_INVALID')
            check(all(Decimal(p[k])>0 for k in ('tick_size','quantity_step','initial_cash')) and 0<Decimal(p['max_target'])<=1 and 0<Decimal(p['max_drawdown'])<1 and all(0<=Decimal(p[k])<10000 for k in ('fee_bps','slippage_bps')) and p['intake_limit_bytes']>0,'DATA_INVALID')
        def market(v):
            if p is None:return
            check(v['provider']==p['provider'] and v['instrument']==p['instrument'],'IDENTITY_MISMATCH')
            if v['schema_version']=='p6.bar.v1':
                check(v['close_ms']==v['open_ms']+p['timeframe_ms'] and p['start_ms']<=v['open_ms']<v['close_ms']<=p['end_ms'] and (v['open_ms']-p['start_ms'])%max(p['timeframe_ms'],1)==0,'DATA_INVALID')
                a,b,c,d=(Decimal(v[k]) for k in ('open','high','low','close'))
                check(c>0 and c<=min(a,d)<=max(a,d)<=b and Decimal(v['volume'])>=0,'DATA_INVALID')
            else:check(0<Decimal(v['bid'])<=Decimal(v['ask']),'DATA_INVALID')
        if kind=='BAR':market(doc)
        for v in q['payloads'].values():market(v)
        for mapping in (q['payloads'],q['clock_probes']):
            for k,v in mapping.items():check(canonical_hash(v)==k,'HASH_MISMATCH')
        for t in q['clock_probes'].values():
            check(t['measured_monotonic_ns']<=t['valid_until_monotonic_ns'],'CLOCK_INVALID')
            if p:check(t['offset_bound_ms']<=p['max_clock_skew_ms'],'CLOCK_INVALID')
        r=q['reservation'];rid=canonical_hash(r) if r else None
        if kind in ('RECEIPT','SEGMENT'):
            check(r is not None,'IDENTITY_MISMATCH')
            if r is not None and p is not None:
                check(r['plan_hash']==canonical_hash(p),'IDENTITY_MISMATCH')
                check(r['reserved_utc_ms']<p['start_ms'],'CLOCK_INVALID')
                check(r['sequence']>=1 and ((r['sequence']==1)==(r['previous_reservation_hash']==ZERO)),'SEQUENCE_INVALID')
        receipts=q['prior_receipts']+([doc] if kind=='RECEIPT' else [])
        prior=None
        for i,e in enumerate(receipts,1):
            check(e['run_id']==rid,'IDENTITY_MISMATCH')
            check(e['kind'] in ('BAR_FINAL','QUOTE'),'POLICY_UNSUPPORTED')
            check(e['receipt_seq']==i,'SEQUENCE_INVALID')
            check(e['previous_receipt_hash']==(canonical_hash(prior) if prior else ZERO),'HASH_MISMATCH')
            if prior:
                check(e['clock_session_id']==prior['clock_session_id'] and e['received_monotonic_ns']>prior['received_monotonic_ns'] and e['received_utc_ms']>=prior['received_utc_ms'],'CLOCK_INVALID')
            t=q['clock_probes'].get(e['clock_probe_hash']);v=q['payloads'].get(e['normalized_payload_hash'])
            check(t is not None and v is not None,'IDENTITY_MISMATCH')
            if p is not None:
                check(e['provider_event_ms']<=e['received_utc_ms']+p['max_clock_skew_ms'] and e['received_utc_ms']>=p['start_ms'],'CLOCK_INVALID')
                if t:
                    check(t['clock_session_id']==e['clock_session_id'] and t['measured_monotonic_ns']<=e['received_monotonic_ns']<=t['valid_until_monotonic_ns'] and t['offset_bound_ms']<=p['max_clock_skew_ms'] and t['measured_utc_ms']<=e['received_utc_ms'],'CLOCK_INVALID')
                if v:
                    wanted={'BAR_FINAL':'p6.bar.v1','QUOTE':'p6.quote.v1'}.get(e['kind'])
                    if wanted is not None:check(v['schema_version']==wanted,'IDENTITY_MISMATCH')
                    if wanted==v['schema_version']=='p6.bar.v1':check(v['close_ms']<=e['received_utc_ms']<=v['close_ms']+p['max_lateness_ms'],'CLOCK_INVALID')
            prior=e
        def segment(s, is_current):
            bar=q['payloads'].get(s['bar_hash'])
            accepted=next((e for e in receipts if canonical_hash(e)==s['accepted_receipt_hash']),None)
            check(s['run_id']==rid and bar is not None and accepted is not None,'IDENTITY_MISMATCH')
            if accepted:
                check(accepted['kind']=='BAR_FINAL' and accepted['normalized_payload_hash']==s['bar_hash'],'IDENTITY_MISMATCH')
                if is_current:check(accepted is receipts[-1],'IDENTITY_MISMATCH')
                # Only the first observation of this bar key may create a segment.
                for previous in receipts:
                    if previous is accepted:break
                    old=q['payloads'].get(previous['normalized_payload_hash'])
                    if old and previous['kind']=='BAR_FINAL' and old['schema_version']=='p6.bar.v1':
                        check(old['open_ms']!=s['start_ms'],'SEQUENCE_INVALID')
            if bar:
                check(bar['schema_version']=='p6.bar.v1','IDENTITY_MISMATCH')
                if bar['schema_version']=='p6.bar.v1':check(s['start_ms']==bar['open_ms'] and s['end_ms']==bar['close_ms'],'DATA_INVALID')
            check(s['segment_seq']>=1,'SEQUENCE_INVALID')
            check((s['segment_seq']==1)==(s['previous_segment_hash']==ZERO),'SEQUENCE_INVALID')
            if p:
                check(s['start_ms']<s['end_ms'] and s['end_ms']-s['start_ms']==p['timeframe_ms'] and p['start_ms']<=s['start_ms']<s['end_ms']<=p['end_ms'] and s['start_ms']==p['start_ms']+(s['segment_seq']-1)*p['timeframe_ms'],'DATA_INVALID')
        if kind=='SEGMENT':
            segment(doc,True);ps=q['prior_segment']
            if ps:
                segment(ps,False)
                check(doc['segment_seq']==ps['segment_seq']+1 and doc['start_ms']==ps['end_ms'],'SEQUENCE_INVALID')
                check(doc['previous_segment_hash']==canonical_hash(ps),'HASH_MISMATCH')
            else:
                check(doc['segment_seq']==1,'SEQUENCE_INVALID')
                check(doc['previous_segment_hash']==ZERO,'HASH_MISMATCH')
        reason=next((e for e in ORDER if e in errors),None)
    except (ValueError,TypeError,KeyError,UnicodeError,OverflowError,RecursionError):
        reason='SCHEMA_INVALID';doc=None
    return dict(schema_version='p6.validation.v1',document_kind=kind,
        structural_status='INVALID' if reason else 'VALID',reason_code=reason,
        canonical_hash=None if reason else canonical_hash(doc),
        external_reference_verification='UPSTREAM_REQUIRED',runtime_accepted=False)
