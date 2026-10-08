import builtins
import copy
import hashlib
import json
import socket
import pytest

pytest.importorskip('cryptography', reason='Install the phase6-anchor extra for signature tests')
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from app.research.phase6_anchor_validation import verify_anchors

# Deterministic test-only key, never a deployment key.
KEY=Ed25519PrivateKey.from_private_bytes(bytes(range(32)))

def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def digest(x):return hashlib.sha256(canonical(x)).hexdigest()
def sign(x,key=KEY):
    x.pop('signature',None);x['signature']=key.sign(canonical(x)).hex();return x

def request():
    trust=dict(schema_version='p6.anchor_trust.v1',service_identity='test-service',endpoint_id='synthetic',algorithm='Ed25519',public_key_hex=KEY.public_key().public_bytes_raw().hex(),not_before_utc_ms=0,not_after_utc_ms=10000,max_head_age_ms=100,max_clock_skew_ms=5)
    subjects=[dict(subject_kind=k,subject_hash=str(i)*64) for i,k in enumerate(['RESERVATION','GENESIS','CHECKPOINT'],2)]
    receipts=[]
    for i,subject in enumerate(subjects,1):
        receipts.append(sign(dict(schema_version='p6.anchor_receipt.v1',run_id='1'*64,anchor_seq=i,**subject,previous_anchor_hash=digest(receipts[-1]) if receipts else '0'*64,retained_utc_ms=700+i,service_identity='test-service')))
    head=sign(dict(schema_version='p6.anchor_head.v1',run_id='1'*64,anchor_seq=3,anchor_hash=digest(receipts[-1]),service_identity='test-service',nonce='a'*64,observed_utc_ms=950,retention_until_ms=2000))
    return dict(trust=trust,expected_trust_hash=digest(trust),expected_run_id='1'*64,expected_subjects=subjects,receipts=receipts,head=head,challenge=dict(nonce='a'*64,issued_utc_ms=900),now_utc_ms=1000)

def refused(q,reason):
    result=verify_anchors(q)
    assert result==dict(schema_version='p6.anchor_validation.v1',cryptographic_status='INVALID',reason_code=reason,head_status='NOT_VALIDATED',anchor_count=0,last_anchor_hash=None,runtime_accepted=False,external_service_verification='UPSTREAM_REQUIRED')

def test_valid_signatures_and_no_runtime_authority():
    q=request();before=copy.deepcopy(q);r=verify_anchors(q)
    assert r['cryptographic_status']=='VALID' and r['head_status']=='MATCHED'
    assert r['anchor_count']==3 and r['last_anchor_hash']==digest(q['receipts'][-1])
    assert r['runtime_accepted'] is False and r['external_service_verification']=='UPSTREAM_REQUIRED' and q==before

def test_offline_never_proves_current_head():
    q=request();q.update(head=None,challenge=None);r=verify_anchors(q)
    assert r['cryptographic_status']=='VALID' and r['head_status']=='OFFLINE' and not r['runtime_accepted']

@pytest.mark.parametrize('target',['receipt','head'])
def test_signature_tamper(target):
    q=request();doc=q['head'] if target=='head' else q['receipts'][0];doc['signature']='0'*128
    refused(q,'SIGNATURE_INVALID')

def test_wrong_key_even_with_recommitted_trust():
    q=request();q['trust']['public_key_hex']=Ed25519PrivateKey.from_private_bytes(b'\xff'*32).public_key().public_bytes_raw().hex();q['expected_trust_hash']=digest(q['trust'])
    refused(q,'SIGNATURE_INVALID')

def test_trust_hash_pinned():
    q=request();q['trust']['max_head_age_ms']=101;refused(q,'TRUST_MISMATCH')

@pytest.mark.parametrize('field',['run_id','service_identity','subject_hash'])
def test_receipt_identity(field):
    q=request();q['receipts'][0][field]='f'*64;sign(q['receipts'][0]);refused(q,'IDENTITY_MISMATCH')

def test_nonce_replay():
    q=request();q['challenge']['nonce']='b'*64;refused(q,'IDENTITY_MISMATCH')

def test_valid_newer_head_rejects_truncation():
    q=request();q['receipts'].pop();q['expected_subjects'].pop();refused(q,'SEQUENCE_INVALID')

def test_predecessor_substitution():
    q=request();q['receipts'][1]['previous_anchor_hash']='f'*64;sign(q['receipts'][1]);refused(q,'SEQUENCE_INVALID')

def test_sequence_not_counter_inference():
    q=request();q['receipts'][1]['anchor_seq']=3;sign(q['receipts'][1]);refused(q,'SEQUENCE_INVALID')

def test_duplicate_subject_refused():
    q=request();q['expected_subjects'][2]['subject_hash']=q['expected_subjects'][1]['subject_hash'];q['receipts'][2]['subject_hash']=q['expected_subjects'][1]['subject_hash'];sign(q['receipts'][2]);q['head']['anchor_hash']=digest(q['receipts'][2]);sign(q['head']);refused(q,'SEQUENCE_INVALID')

@pytest.mark.parametrize('field,value',[('observed_utc_ms',899),('observed_utc_ms',1006),('retention_until_ms',1000),('retention_until_ms',999)])
def test_head_time_and_retention_boundaries(field,value):
    q=request();q['head'][field]=value;sign(q['head']);refused(q,'RETENTION_EXPIRED' if field=='retention_until_ms' else 'CLOCK_INVALID')

def test_exact_age_boundary_and_next_tick():
    q=request();q['head']['observed_utc_ms']=900;sign(q['head']);assert verify_anchors(q)['cryptographic_status']=='VALID'
    q['now_utc_ms']=1001;refused(q,'CLOCK_INVALID')

def test_head_after_challenge_with_skew_boundary():
    q=request();q['challenge']['issued_utc_ms']=955;assert verify_anchors(q)['cryptographic_status']=='VALID'
    q['challenge']['issued_utc_ms']=956;refused(q,'CLOCK_INVALID')

def test_receipt_future_and_trust_expiry():
    q=request();q.update(head=None,challenge=None);q['receipts'][-1]['retained_utc_ms']=1006;sign(q['receipts'][-1]);refused(q,'CLOCK_INVALID')
    q=request();q['now_utc_ms']=10001;refused(q,'CLOCK_INVALID')

def test_schema_precedence_over_bad_signature():
    q=request();q['receipts'][0]['signature']='0'*128;q['head']['anchor_seq']=True;refused(q,'SCHEMA_INVALID')

def test_signature_precedes_clock_anywhere():
    q=request();q['receipts'][0]['retained_utc_ms']=10001;sign(q['receipts'][0]);q['head']['signature']='0'*128;refused(q,'SIGNATURE_INVALID')

@pytest.mark.parametrize('bad',[None,[],{},'{}',b'{}'])
def test_invalid_request(bad):refused(bad,'SCHEMA_INVALID')

@pytest.mark.parametrize('field,value',[('anchor_seq',True),('retained_utc_ms',1.0),('signature','A'*128),('subject_kind','OTHER')])
def test_strict_receipt_schema(field,value):
    q=request();q['receipts'][0][field]=value;refused(q,'SCHEMA_INVALID')

def test_dependency_missing_is_closed(monkeypatch):
    q=request();original=builtins.__import__
    def missing(name,*args,**kwargs):
        if name.startswith('cryptography'):raise ImportError('synthetic unavailable backend')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',missing);refused(q,'DEPENDENCY_UNAVAILABLE')

def test_no_file_or_network_io(monkeypatch):
    q=request()
    def forbidden(*args,**kwargs):raise AssertionError('unexpected I/O')
    monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    assert verify_anchors(q)['cryptographic_status']=='VALID'

def test_rfc8032_empty_message_vector():
    # RFC8032 section7.1 test1; backend sanity, not our canonical-record fixture.
    public=bytes.fromhex('d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a')
    signature=bytes.fromhex('e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b')
    key=Ed25519PublicKey.from_public_bytes(public);key.verify(signature,b'')
    with pytest.raises(InvalidSignature):key.verify(signature,b'x')

def test_unknown_fields_rejected_every_record():
    for location in ('trust','head','challenge','receipt','subject'):
        q=request();doc=q['receipts'][0] if location=='receipt' else q['expected_subjects'][0] if location=='subject' else q[location]
        doc['extra']='forbidden';refused(q,'SCHEMA_INVALID')

def test_head_without_challenge_and_reverse():
    q=request();q['challenge']=None;refused(q,'SCHEMA_INVALID')
    q=request();q['head']=None;refused(q,'SCHEMA_INVALID')

def test_signature_does_not_make_any_order_valid():
    q=request();q['receipts'][0],q['receipts'][1]=q['receipts'][1],q['receipts'][0]
    q['expected_subjects'][0],q['expected_subjects'][1]=q['expected_subjects'][1],q['expected_subjects'][0]
    refused(q,'SEQUENCE_INVALID')

def test_expired_retention_even_fresh_signed_head():
    q=request();q['head'].update(observed_utc_ms=1000,retention_until_ms=1000);sign(q['head']);refused(q,'RETENTION_EXPIRED')
    q['head']['retention_until_ms']=1001;sign(q['head']);assert verify_anchors(q)['cryptographic_status']=='VALID'

def test_trust_time_end_inclusive_offline():
    q=request();q.update(head=None,challenge=None,now_utc_ms=10000);assert verify_anchors(q)['cryptographic_status']=='VALID'
    q['now_utc_ms']=10001;refused(q,'CLOCK_INVALID')

def test_dependency_unsupported_is_closed(monkeypatch):
    from cryptography.exceptions import UnsupportedAlgorithm
    import cryptography.hazmat.primitives.asymmetric.ed25519 as ed
    class Missing:
        @classmethod
        def from_public_bytes(cls,raw):raise UnsupportedAlgorithm('synthetic unavailable algorithm')
    monkeypatch.setattr(ed,'Ed25519PublicKey',Missing);refused(request(),'DEPENDENCY_UNAVAILABLE')

def test_unexpected_backend_failure_not_positive(monkeypatch):
    import cryptography.hazmat.primitives.asymmetric.ed25519 as ed
    q=request()
    class Broken:
        @classmethod
        def from_public_bytes(cls,raw):raise RuntimeError('synthetic backend fault')
    monkeypatch.setattr(ed,'Ed25519PublicKey',Broken)
    with pytest.raises(RuntimeError):verify_anchors(q)

def test_caller_mutation_after_signature_cannot_change_signed_claim(monkeypatch):
    import app.research.phase6_anchor_validation as engine
    q=request();q['head']['retention_until_ms']=999;sign(q['head']);original=engine._signatures
    def mutate(snapshot):
        original(snapshot)
        q['head']['retention_until_ms']=2000
    monkeypatch.setattr(engine,'_signatures',mutate)
    refused(q,'RETENTION_EXPIRED')

def test_result_never_rereads_caller_request(monkeypatch):
    import app.research.phase6_anchor_validation as engine
    q=request();last=digest(q['receipts'][-1]);original=engine._signatures
    def mutate(snapshot):
        original(snapshot)
        q['head']=None;q['receipts'].clear()
    monkeypatch.setattr(engine,'_signatures',mutate)
    r=verify_anchors(q)
    assert r['cryptographic_status']=='VALID' and r['head_status']=='MATCHED' and r['anchor_count']==3 and r['last_anchor_hash']==last


def test_snapshot_rejects_custom_objects_without_hooks():
    class Custom:
        def __deepcopy__(self,*args):raise AssertionError('custom hook called')
    q=request();q['trust']=Custom();refused(q,'SCHEMA_INVALID')

def test_snapshot_rejects_cycle_and_oversized_values():
    q=request();q['trust']=q;refused(q,'SCHEMA_INVALID')
    q=request();q['trust']['service_identity']='x'*257;refused(q,'SCHEMA_INVALID')
    q=request();q['receipts']=[None]*10001;refused(q,'SCHEMA_INVALID')

def test_snapshot_total_budget_stops_shared_tree_expansion():
    tree=[0]*10000
    q=request();q['trust']=[tree]*40
    refused(q,'SCHEMA_INVALID')
