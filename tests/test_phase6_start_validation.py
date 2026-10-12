import copy
import pytest
pytest.importorskip('cryptography',reason='Install phase6-anchor for signed integration tests')
from test_phase6_reservation_ledger import setup,inputs
from test_phase6_anchor_validation import request as anchor_fixture,sign,digest
from app.research.phase6_start_validation import verify_start_bindings


def case(tmp_path):
    l,root,e=setup(tmp_path);p,a=inputs(root,e);source=anchor_fixture();p['anchor_trust_hash']=digest(source['trust']);a['plan_hash']=digest(p)
    result=l.reserve(p,a,reserved_utc_ms=2);l.close();r=result['reservation'];run=result['run_id']
    g=dict(schema_version='p6.genesis.v1',run_id=run,plan_hash=digest(p),cash=p['initial_cash'],units='0',cost_basis='0',cumulative_fees='0',cumulative_realized_pnl='0',equity=p['initial_cash'],peak_equity=p['initial_cash'],strategy_state_hash='7'*64)
    receipts=source['receipts'][:2]
    for index,(receipt,subject) in enumerate(zip(receipts,[r,g])):
        receipt.update(run_id=run,subject_hash=digest(subject),previous_anchor_hash=digest(receipts[index-1]) if index else '0'*64)
        sign(receipt)
    head=source['head'];head.update(run_id=run,anchor_seq=2,anchor_hash=digest(receipts[-1]),retention_until_ms=300000);sign(head)
    anchor=dict(trust=source['trust'],receipts=receipts,head=head,challenge=source['challenge'],now_utc_ms=1000)
    return dict(plan=p,authorization=a,reservation=r,genesis=g,registry_identity=r['registry_identity'],strategy_state_hash=g['strategy_state_hash'],anchor=anchor)

def refused(q,reason,detail=None):
    r=verify_start_bindings(q)
    assert r==dict(schema_version='p6.start_validation.v1',binding_status='INVALID',reason_code=reason,anchor_reason_code=detail,run_id=None,genesis_hash=None,last_anchor_hash=None,runtime_accepted=False,external_authority_verification='UPSTREAM_REQUIRED')

def test_durable_reservation_to_signed_initial_state(tmp_path):
    q=case(tmp_path);before=copy.deepcopy(q);r=verify_start_bindings(q)
    assert r['binding_status']=='VALID' and r['run_id']==digest(q['reservation']) and r['genesis_hash']==digest(q['genesis'])
    assert r['last_anchor_hash']==digest(q['anchor']['receipts'][-1]) and not r['runtime_accepted'] and q==before

@pytest.mark.parametrize('field',['cash','units','cost_basis','cumulative_fees','cumulative_realized_pnl','equity','peak_equity'])
def test_initial_balances_cannot_be_substituted(tmp_path,field):
    q=case(tmp_path);q['genesis'][field]='99';refused(q,'IDENTITY_MISMATCH')

@pytest.mark.parametrize('part,field',[('authorization','owner'),('authorization','plan_hash'),('reservation','authorization_hash'),('reservation','registry_identity'),('genesis','run_id'),('genesis','strategy_state_hash')])
def test_identity_substitution(tmp_path,part,field):
    q=case(tmp_path);q[part][field]='a'*64;refused(q,'IDENTITY_MISMATCH')

def test_caller_cannot_choose_anchor_subjects(tmp_path):
    q=case(tmp_path);q['anchor']['expected_subjects']=[];refused(q,'SCHEMA_INVALID')

def test_offline_never_passes_start_bindings(tmp_path):
    q=case(tmp_path);q['anchor'].update(head=None,challenge=None);refused(q,'ANCHOR_INVALID')

def test_missing_genesis_receipt(tmp_path):
    q=case(tmp_path);q['anchor']['receipts'].pop();refused(q,'ANCHOR_INVALID')

def test_invalid_signature(tmp_path):
    q=case(tmp_path);q['anchor']['head']['signature']='0'*128;refused(q,'ANCHOR_INVALID','SIGNATURE_INVALID')

def test_trust_commitment_not_caller_override(tmp_path):
    q=case(tmp_path);q['anchor']['trust']['max_head_age_ms']=101;refused(q,'ANCHOR_INVALID','TRUST_MISMATCH')

def test_start_boundary_and_error_staging(tmp_path):
    q=case(tmp_path);q['anchor']['now_utc_ms']=q['plan']['start_ms'];q['anchor']['head']['signature']='0'*128;refused(q,'CLOCK_INVALID')

def test_pre_reservation_receipt_with_valid_signature(tmp_path):
    q=case(tmp_path);receipts=q['anchor']['receipts'];receipts[0]['retained_utc_ms']=1;sign(receipts[0]);receipts[1]['previous_anchor_hash']=digest(receipts[0]);sign(receipts[1]);q['anchor']['head']['anchor_hash']=digest(receipts[1]);sign(q['anchor']['head']);refused(q,'CLOCK_INVALID')
    q['anchor']['head']['signature']='0'*128;refused(q,'ANCHOR_INVALID','SIGNATURE_INVALID')

def test_retention_must_cover_run_window(tmp_path):
    q=case(tmp_path);p=q['plan'];h=q['anchor']['head'];h['retention_until_ms']=p['end_ms']+p['max_lateness_ms']+p['max_decision_delay_ms']+p['max_quote_delay_ms'];sign(h)
    refused(q,'ANCHOR_INVALID','RETENTION_HORIZON_INSUFFICIENT')
    h['retention_until_ms']+=1;sign(h);assert verify_start_bindings(q)['binding_status']=='VALID'

def test_caller_mutation_cannot_change_result(tmp_path,monkeypatch):
    import app.research.phase6_start_validation as m
    q=case(tmp_path);oldrun=digest(q['reservation']);original=m.verify_anchors
    def mutate(request):
        result=original(request);q['reservation'].clear();q['genesis'].clear();return result
    monkeypatch.setattr(m,'verify_anchors',mutate)
    r=verify_start_bindings(q);assert r['binding_status']=='VALID' and r['run_id']==oldrun

@pytest.mark.parametrize('value',[None,{},[],True])
def test_bad_envelope(value):refused(value,'SCHEMA_INVALID')

def test_lexical_root_containment_refused(tmp_path):
    q=case(tmp_path);q['authorization']['evidence_root']=q['authorization']['registry_root']+'/child';refused(q,'SCHEMA_INVALID')

def test_null_byte_root_not_a_valid_filesystem_path(tmp_path):
    q=case(tmp_path);q['authorization']['evidence_root']='/private/tmp/invalid\x00root';refused(q,'SCHEMA_INVALID')

def test_plan_and_genesis_schema_refusals(tmp_path):
    q=case(tmp_path);q['plan']['profile']='unsupported';refused(q,'PLAN_INVALID')
    q['plan']['profile']='p6.shadow.diagnostic.v1';q['genesis']['mark_price']='100';refused(q,'SCHEMA_INVALID')

def test_no_filesystem_or_network_access_in_verifier(tmp_path,monkeypatch):
    import builtins,os,socket
    q=case(tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('unexpected I/O')
    with monkeypatch.context() as m:
        m.setattr(builtins,'open',forbidden);m.setattr(os,'stat',forbidden);m.setattr(os,'lstat',forbidden);m.setattr(socket,'socket',forbidden)
        assert verify_start_bindings(q)['binding_status']=='VALID'

def test_absent_backend_cannot_pass(tmp_path,monkeypatch):
    import builtins
    q=case(tmp_path);original=builtins.__import__
    def missing(name,*args,**kwargs):
        if name.startswith('cryptography'):raise ImportError('synthetic missing backend')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',missing);refused(q,'ANCHOR_INVALID','DEPENDENCY_UNAVAILABLE')

def test_custom_and_cyclic_input_rejected_without_hooks():
    class Custom:
        def __deepcopy__(self,*args):raise AssertionError('unexpected hook')
    refused(Custom(),'SCHEMA_INVALID')
    q={};q['self']=q;refused(q,'SCHEMA_INVALID')
