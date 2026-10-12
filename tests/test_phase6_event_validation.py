import copy
import pytest
from app.research.phase6_event_validation import verify_event
from app.research.phase6_validation import canonical_hash as H

KINDS=['MANIFEST_VERIFIED','BAR_ACCEPTED','WARMUP','DECISION','NO_TRADE','ORDER_INTENT','SIMULATED_FILL','REJECTED','CHECKPOINT_PREPARED','STOP']


def payload(kind):
    values={
        'MANIFEST_VERIFIED':dict(manifest_hash='1'*64),
        'BAR_ACCEPTED':dict(segment_hash='2'*64,receipt_hash='3'*64,bar_hash='4'*64,accepted_prefix_hash='5'*64),
        'WARMUP':dict(strategy_input_hash='3'*64,strategy_output_hash='4'*64,bar_count=1,target=None,callback_started_utc_ms=12,callback_finished_utc_ms=13,callback_started_monotonic_ns=120,callback_finished_monotonic_ns=130,clock_session_id='test'),
        'NO_TRADE':dict(decision_event_hash='3'*64,reason_code='ZERO_QUANTITY'),
        'ORDER_INTENT':dict(decision_event_hash='3'*64,side='BUY',quantity='1',clock_session_id='test'),
        'SIMULATED_FILL':dict(intent_event_hash='3'*64,quote_receipt_hash='4'*64,side='BUY',quantity='1',price='100',fee='0.1'),
        'REJECTED':dict(intent_event_hash='3'*64,quote_receipt_hash='4'*64,reason_code='CASH_CAP_ZERO'),
        'CHECKPOINT_PREPARED':dict(state_hash='3'*64,strategy_state_hash='4'*64,accepted_prefix_hash='5'*64,last_receipt_seq=1,pending_intent_hash=None),
        'STOP':dict(reason_code='OPERATOR_STOP',pending_intent_hash=None),
    }
    values['DECISION']=dict(values['WARMUP'],target='0.25')
    return dict(schema_version='p6.payload.'+kind.lower()+'.v1',**values[kind])


def case(kind='MANIFEST_VERIFIED'):
    m=dict(schema_version='p6.session_manifest.v1',run_id='1'*64,segment_id='2'*64,plan_hash='3'*64,engine_version='p6.shadow.reducer.v1',engine_code_hash='4'*64,strategy_code_hash='5'*64,params_hash='6'*64,prior_checkpoint_hash='7'*64,prior_anchor_hash='8'*64,persisted_utc_ms=10)
    p=payload(kind)
    if kind=='MANIFEST_VERIFIED':p['manifest_hash']=H(m)
    e=dict(schema_version='p6.event.v1',run_id=m['run_id'],session_id=H(m),event_seq=1,kind=kind,utc_ms=20,monotonic_ns=200,payload_hash=H(p),previous_event_hash=H(m))
    previous=None
    if kind!='MANIFEST_VERIFIED':
        previous=case()['event'];e.update(event_seq=2,previous_event_hash=H(previous),utc_ms=21,monotonic_ns=210)
    return dict(event=e,payload=p,manifest=m,previous_event=previous)


def refused(q,reason):
    result=verify_event(q)
    assert result==dict(schema_version='p6.event_validation.v1',structural_status='INVALID',reason_code=reason,canonical_hash=None,external_reference_verification='UPSTREAM_REQUIRED',runtime_accepted=False)


@pytest.mark.parametrize('kind',KINDS)
def test_exact_typed_payloads(kind):
    q=case(kind);before=copy.deepcopy(q);result=verify_event(q)
    assert result['structural_status']=='VALID' and result['canonical_hash']==H(q['event'])
    assert not result['runtime_accepted'] and q==before


@pytest.mark.parametrize('kind',KINDS)
def test_unknown_payload_fields_refused(kind):
    q=case(kind);q['payload']['extra']=1;refused(q,'SCHEMA_INVALID')


@pytest.mark.parametrize('kind',KINDS)
def test_tampered_payload_commitment(kind):
    q=case(kind);q['event']['payload_hash']='f'*64;refused(q,'HASH_MISMATCH')


@pytest.mark.parametrize('part,field',[('event','run_id'),('event','session_id'),('previous_event','run_id'),('previous_event','session_id'),('payload','segment_hash')])
def test_substituted_identity(part,field):
    q=case('BAR_ACCEPTED');q[part][field]='f'*64;refused(q,'IDENTITY_MISMATCH')


@pytest.mark.parametrize('mutation',['before_manifest','utc_rollback','monotonic_equal','callback_utc','callback_monotonic'])
def test_chronology(mutation):
    q=case('DECISION')
    if mutation=='before_manifest':q['event']['utc_ms']=9
    if mutation=='utc_rollback':q['event']['utc_ms']=19
    if mutation=='monotonic_equal':q['event']['monotonic_ns']=200
    if mutation=='callback_utc':q['payload']['callback_started_utc_ms']=9
    if mutation=='callback_monotonic':q['payload']['callback_finished_monotonic_ns']=211
    refused(q,'CLOCK_INVALID')


@pytest.mark.parametrize('mutation',['missing_previous','gap','duplicate_manifest','genesis_seq'])
def test_sequence(mutation):
    q=case('BAR_ACCEPTED')
    if mutation=='missing_previous':q['previous_event']=None
    if mutation=='gap':q['event']['event_seq']=3
    if mutation=='duplicate_manifest':
        q=case();q['previous_event']=copy.deepcopy(q['event']);q['event'].update(event_seq=2,utc_ms=21,monotonic_ns=210)
    if mutation=='genesis_seq':q=case();q['event']['event_seq']=2
    refused(q,'SEQUENCE_INVALID')


def test_previous_hash_and_manifest_payload():
    q=case('BAR_ACCEPTED');q['event']['previous_event_hash']='f'*64;refused(q,'HASH_MISMATCH')
    q=case();q['payload']['manifest_hash']='f'*64;refused(q,'IDENTITY_MISMATCH')


@pytest.mark.parametrize('kind,field,bad',[
    ('DECISION','target','1.01'),('WARMUP','target','0'),('ORDER_INTENT','quantity','0'),
    ('ORDER_INTENT','quantity','1.000000001'),('SIMULATED_FILL','fee','-0'),
    ('SIMULATED_FILL','price','0'),('SIMULATED_FILL','side','SHORT'),
    ('STOP','reason_code','exception text'),('CHECKPOINT_PREPARED','last_receipt_seq',True),
    ('NO_TRADE','reason_code','AT_TARGET'),('REJECTED','quote_receipt_hash',None),
])
def test_payload_constraints(kind,field,bad):
    q=case(kind);q['payload'][field]=bad;refused(q,'SCHEMA_INVALID')


def test_invalid_previous_envelope_and_error_precedence():
    q=case('STOP');q['previous_event']['extra']=True;q['event']['session_id']='f'*64;refused(q,'SCHEMA_INVALID')
    del q['previous_event']['extra'];q['event']['utc_ms']=0;refused(q,'IDENTITY_MISMATCH')


def test_no_io_or_copy_hooks(tmp_path,monkeypatch):
    import builtins,socket,os
    q=case('DECISION')
    def forbidden(*a,**kw):raise AssertionError('unexpected I/O')
    with monkeypatch.context() as m:
        m.setattr(builtins,'open',forbidden);m.setattr(socket,'socket',forbidden);m.setattr(os,'stat',forbidden)
        assert verify_event(q)['structural_status']=='VALID'
    class Custom:
        def __deepcopy__(self,*args):raise AssertionError('hook')
    refused(Custom(),'SCHEMA_INVALID')


def test_strategy_serialization_to_decision_event():
    from app.paper.session import diagnostic_strategy_v1 as strategy
    p=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=0,targets=['0.25','0.75'])
    state=strategy.initial(p);q=case('DECISION')
    out=strategy.advance(state,p,segment_seq=1,segment_hash=q['manifest']['segment_id'])
    q['payload'].update(strategy_input_hash=H(state),strategy_output_hash=H(out['state']),target=out['target'],bar_count=out['state']['bar_count'])
    q['event']['payload_hash']=H(q['payload'])
    assert verify_event(q)['structural_status']=='VALID'


def test_fill_price_matches_reducer_slippage_output():
    from app.paper.session import shadow_reducer_v1 as reducer
    from test_phase6_shadow_reducer import config,bar,quote
    c=config(initial_cash='1000000000000',quantity_step='0.00000001',tick_size='1',fee_bps='0',slippage_bps='5000')
    before=reducer.bar(reducer.initial(c),bar(close='1000000000000'),'0.25')
    after=reducer.quote(before,quote(price='1000000000000'))
    # With fee zero and no old position, actual cash debit / quantity is modeled fill price.
    from fractions import Fraction
    price=(Fraction(before['cash'])-Fraction(after['cash']))/Fraction(after['units'])
    assert price==1500000000000
    q=case('SIMULATED_FILL');q['payload'].update(quantity=after['units'],price=str(price),fee=after['cumulative_fees'])
    q['event']['payload_hash']=H(q['payload'])
    assert verify_event(q)['structural_status']=='VALID'


@pytest.mark.parametrize('mutation',['kind','genesis_hash','late_manifest'])
def test_previous_genesis_invariants(mutation):
    q=case('BAR_ACCEPTED')
    if mutation=='kind':q['previous_event']['kind']='STOP'
    if mutation=='genesis_hash':q['previous_event']['previous_event_hash']='f'*64
    if mutation=='late_manifest':q['previous_event']['event_seq']=2;q['event']['event_seq']=3
    q['event']['previous_event_hash']=H(q['previous_event'])
    refused(q,'HASH_MISMATCH' if mutation=='genesis_hash' else 'SEQUENCE_INVALID')


def test_detached_result_after_input_mutation(monkeypatch):
    import app.research.phase6_event_validation as module
    q=case('DECISION');expected=H(q['event']);original=module.H
    def mutate(value):
        result=original(value);q['event']['run_id']='f'*64;q['payload'].clear();return result
    monkeypatch.setattr(module,'H',mutate)
    result=module.verify_event(q)
    assert result['structural_status']=='VALID' and result['canonical_hash']==expected
