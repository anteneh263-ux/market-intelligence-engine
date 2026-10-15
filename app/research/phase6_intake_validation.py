"""Bounded supplied receipt/segment prefix verification; no intake authority."""
from app.research.phase6_start_validation import _snapshot, _Refused, _need, _shape
from app.research.phase6_validation import validate, canonical_hash as H


def checked(document,plan=None,reservation=None,receipts=None,prior=None,payloads=None,probes=None):
    r=validate(dict(document=document,plan=plan,reservation=reservation,prior_receipts=receipts or [],
                    prior_segment=prior,payloads=payloads or {},clock_probes=probes or {}))
    _need(r['structural_status']=='VALID',r['reason_code'] or 'SCHEMA_INVALID')


def _check(q):
    _shape(q,'plan reservation receipts segments payloads clock_probes prefix')
    p,r,es,ss,ps,ts,prefix=(q[k] for k in ('plan','reservation','receipts','segments','payloads','clock_probes','prefix'))
    _need(type(es) is list and 1<=len(es)<=64 and type(ss) is list and 1<=len(ss)<=64)
    _need(type(ps) is dict and type(ts) is dict)
    _shape(prefix,'schema_version run_id segment_hashes')
    _need(prefix['schema_version']=='p6.accepted_prefix.v1' and type(prefix['segment_hashes']) is list)
    checked(p)
    # The final receipt check validates the complete mixed BAR_FINAL/QUOTE chain.
    checked(es[-1],p,r,es[:-1],payloads=ps,probes=ts)
    _need(set(ps)=={e['normalized_payload_hash'] for e in es}
          and set(ts)=={e['clock_probe_hash'] for e in es},'INVENTORY_MISMATCH')
    first=[];seen={}
    for index,e in enumerate(es):
        if e['kind']!='BAR_FINAL':continue
        b=ps[e['normalized_payload_hash']]
        key=(b['provider'],b['instrument'],b['open_ms'])
        if key in seen:
            _need(seen[key]==e['normalized_payload_hash'],'REVISION_CONFLICT')
        else:
            seen[key]=e['normalized_payload_hash'];first.append((index,e))
    _need(len(ss)==len(first),'SEQUENCE_INVALID')
    previous=None
    for s,(index,e) in zip(ss,first):
        _need(type(s) is dict and s.get('accepted_receipt_hash')==H(e)
              and s.get('bar_hash')==e['normalized_payload_hash'],'IDENTITY_MISMATCH')
        checked(s,p,r,es[:index+1],previous,ps,ts)
        previous=s
    expected=dict(schema_version='p6.accepted_prefix.v1',run_id=H(r),segment_hashes=[H(s) for s in ss])
    _need(prefix==expected,'HASH_MISMATCH')
    return H(prefix),[s['bar_hash'] for s in ss],es[-1]['receipt_seq']


def verify_intake_prefix(request):
    result=dict(schema_version='p6.intake_prefix_validation.v1',binding_status='INVALID',reason_code=None,
        accepted_prefix_hash=None,bar_hashes=[],last_receipt_seq=None,runtime_accepted=False,
        callback_inputs_verified=False,durability_verified=False,external_reference_verification='UPSTREAM_REQUIRED')
    try:
        digest,bars,last=_check(_snapshot(request))
    except _Refused as exc:result['reason_code']=exc.reason
    except (ValueError,TypeError,KeyError,RuntimeError,IndexError,OverflowError,RecursionError):result['reason_code']='SCHEMA_INVALID'
    else:result.update(binding_status='VALID',accepted_prefix_hash=digest,bar_hashes=bars,last_receipt_seq=last)
    return result
