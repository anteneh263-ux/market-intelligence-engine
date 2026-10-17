"""Pure pre-start bindings; never grants intake or execution authority."""
import os
import re
from app.research.phase6_validation import validate, canonical_hash as H
from app.research.phase6_anchor_validation import verify_anchors


class _Refused(Exception):
    def __init__(self,reason,detail=None):self.reason,self.detail=reason,detail


def _need(ok,reason='SCHEMA_INVALID',detail=None):
    if not ok:raise _Refused(reason,detail)


def _snapshot(v,depth=0,budget=None,*,capacity=None):
    from app.research.phase6_capacity import limits
    maxdepth,maxlist,maxmap,maxvalues,maxchars,_ = limits(capacity)
    if budget is None:budget=[maxvalues,maxchars]
    budget[0]-=1;_need(depth<=maxdepth and budget[0]>=0)
    if v is None or type(v) in (bool,int):return v
    if type(v) is str:
        _need(v.isascii() and len(v)<=4096)
        if maxchars is not None:
            budget[1]-=len(v);_need(budget[1]>=0)
        return v
    if type(v) is list:
        _need(len(v)<=maxlist);return [_snapshot(v[i],depth+1,budget,capacity=capacity) for i in range(len(v))]
    _need(type(v) is dict and len(v)<=maxmap)
    result={}
    for k,value in v.items():
        _need(type(k) is str and k.isascii() and len(k)<=128)
        if maxchars is not None:
            budget[1]-=len(k);_need(budget[1]>=0)
        result[k]=_snapshot(value,depth+1,budget,capacity=capacity)
    return result


def _shape(x,fields):_need(type(x) is dict and set(x)==set(fields.split()))
def _hex(x):return type(x) is str and re.fullmatch('[0-9a-f]{64}',x) is not None
def _int(x):return type(x) is int and 0<=x<2**63
def _token(x):return type(x) is str and re.fullmatch('[A-Za-z0-9_.:-]{1,128}',x) is not None


def _path(x):
    _need(type(x) is str and '\x00' not in x and x.startswith('/') and not x.startswith('//') and x!='/' and os.path.normpath(x)==x)
    _need(not any(re.search(r'(?:^|[^a-z0-9])h[-_ ]?0?25(?:[^0-9]|$)',p,re.I) for p in x.split('/')))


def _check(q, *,capacity=None):
    _shape(q,'plan authorization reservation genesis registry_identity strategy_state_hash anchor')
    _need(_hex(q['registry_identity']) and _hex(q['strategy_state_hash']))
    p,a,r,g,b=(q[k] for k in ('plan','authorization','reservation','genesis','anchor'))
    _shape(a,'schema_version plan_hash owner evidence_root registry_root anchor_endpoint_id approved_utc_ms approval_reference')
    _need(a['schema_version']=='p6.authorization.v1' and _hex(a['plan_hash']) and _token(a['owner']) and _token(a['anchor_endpoint_id']) and _int(a['approved_utc_ms']) and type(a['approval_reference']) is str and 0<len(a['approval_reference'])<=512)
    _path(a['evidence_root']);_path(a['registry_root'])
    _need(os.path.commonpath([a['evidence_root'],a['registry_root']]) not in (a['evidence_root'],a['registry_root']))
    _shape(r,'schema_version plan_hash authorization_hash registry_identity sequence reserved_utc_ms previous_reservation_hash')
    _need(r['schema_version']=='p6.reservation.v1' and all(_hex(r[k]) for k in ('plan_hash','authorization_hash','registry_identity','previous_reservation_hash')) and _int(r['sequence']) and r['sequence']>=1 and _int(r['reserved_utc_ms']))
    _shape(g,'schema_version run_id plan_hash cash units cost_basis cumulative_fees cumulative_realized_pnl equity peak_equity strategy_state_hash')
    _need(g['schema_version']=='p6.genesis.v1' and all(_hex(g[k]) for k in ('run_id','plan_hash','strategy_state_hash')) and all(type(g[k]) is str for k in ('cash','units','cost_basis','cumulative_fees','cumulative_realized_pnl','equity','peak_equity')))
    _shape(b,'trust receipts head challenge now_utc_ms')
    _need(type(b['trust']) is dict and _token(b['trust'].get('endpoint_id')) and type(b['receipts']) is list and _int(b['now_utc_ms']))
    result=validate(dict(document=p,plan=None,reservation=None,prior_receipts=[],prior_segment=None,payloads={},clock_probes={}))
    _need(result['structural_status']=='VALID' and result['document_kind']=='PLAN','PLAN_INVALID')
    ph,rh,gh=H(p),H(r),H(g)
    _need(a['owner']==p['owner'] and a['plan_hash']==r['plan_hash']==g['plan_hash']==ph and r['authorization_hash']==H(a) and r['registry_identity']==q['registry_identity'] and (r['previous_reservation_hash']=='0'*64)==(r['sequence']==1) and g['run_id']==rh and g['strategy_state_hash']==q['strategy_state_hash'] and a['anchor_endpoint_id']==b['trust']['endpoint_id'],'IDENTITY_MISMATCH')
    _need(all(g[k]==p['initial_cash'] for k in ('cash','equity','peak_equity')) and all(g[k]=='0' for k in ('units','cost_basis','cumulative_fees','cumulative_realized_pnl')),'IDENTITY_MISMATCH')
    now=b['now_utc_ms'];start=p['start_ms']
    _need(a['approved_utc_ms']<=r['reserved_utc_ms']<=now<start,'CLOCK_INVALID')
    _need(b['head'] is not None and b['challenge'] is not None and len(b['receipts'])==2,'ANCHOR_INVALID')
    ar=verify_anchors(dict(**b,expected_trust_hash=p['anchor_trust_hash'],expected_run_id=rh,expected_subjects=[dict(subject_kind='RESERVATION',subject_hash=rh),dict(subject_kind='GENESIS',subject_hash=gh)]))
    _need(ar['cryptographic_status']=='VALID' and ar['head_status']=='MATCHED','ANCHOR_INVALID',ar['reason_code'])
    _need(all(r['reserved_utc_ms']<=x['retained_utc_ms']<=now and x['retained_utc_ms']<start for x in b['receipts']) and b['challenge']['issued_utc_ms']>=r['reserved_utc_ms'] and b['head']['observed_utc_ms']<start,'CLOCK_INVALID')
    horizon=p['end_ms']+p['max_lateness_ms']+p['max_decision_delay_ms']+p['max_quote_delay_ms']
    _need(b['head']['retention_until_ms']>horizon,'ANCHOR_INVALID','RETENTION_HORIZON_INSUFFICIENT')
    return rh,gh,ar['last_anchor_hash']


def verify_start_bindings(request, *,capacity=None):
    result=dict(schema_version='p6.start_validation.v1',binding_status='INVALID',reason_code=None,anchor_reason_code=None,run_id=None,genesis_hash=None,last_anchor_hash=None,runtime_accepted=False,external_authority_verification='UPSTREAM_REQUIRED')
    try:
        q=_snapshot(request, capacity=capacity)
        run,genesis,last=_check(q, capacity=capacity)
    except _Refused as exc:
        result.update(reason_code=exc.reason,anchor_reason_code=exc.detail);return result
    result.update(binding_status='VALID',run_id=run,genesis_hash=genesis,last_anchor_hash=last)
    return result
