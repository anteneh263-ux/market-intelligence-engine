import copy
import json
import pytest
from app.paper.session import diagnostic_strategy_v1 as strategy
from app.research.phase6_validation import canonical_hash as H


def params(warmup=2):
    return dict(schema_version='p6.diagnostic_params.v1',warmup_bars=warmup,targets=['0.25','0.75'])


@pytest.mark.parametrize('warmup',[0,1,2,3])
def test_state_continuity_and_warmup_parity(warmup):
    p=params(warmup);s=strategy.initial(p);before=copy.deepcopy(p);targets=[]
    assert s==dict(schema_version='p6.diagnostic_state.v1',params_hash=H(p),bar_count=0,last_segment_hash='0'*64)
    for seq in range(1,warmup+5):
        prior=copy.deepcopy(s)
        result=strategy.advance(s,p,segment_seq=seq,segment_hash=f'{seq:064x}')
        assert s==prior and p==before
        s=strategy.decode(strategy.encode(result['state']));targets.append(result['target'])
        assert s['bar_count']==seq and s['last_segment_hash']==f'{seq:064x}'
        assert result['kind']==('WARMUP' if seq<=warmup else 'DECISION')
    assert targets==[None]*warmup+['0.25','0.75','0.25','0.75']


def test_exact_canonical_roundtrip():
    p=params();s=strategy.initial(p)
    for record in (p,s):
        encoded=strategy.encode(record)
        assert encoded==json.dumps(record,sort_keys=True,separators=(',',':')).encode()
        assert strategy.decode(encoded)==record


@pytest.mark.parametrize('raw',[
    b'{}', b'[]', b'null', b'\xff', b'{}{}', b'{"x":NaN}',
    b'{"schema_version":"p6.diagnostic_params.v1","targets":["0.25","0.75"],"warmup_bars":1,"warmup_bars":2}',
    b'{"schema_version":"p6.diagnostic_params.v1","targets":["0.25","0.75"],"warmup_bars":1.0}',
    b'{"schema_version":"p6.diagnostic_params.v1","targets":["0.25","0.75"],"warmup_bars":true}',
    b' '*4097,
])
def test_bad_wire_input(raw):
    with pytest.raises(ValueError,match='INPUT_INVALID'):strategy.decode(raw)


@pytest.mark.parametrize('transform',[
    lambda raw:raw+b'\n',lambda raw:b' '+raw,
    lambda raw:raw.replace(b'0.25',b'0.250'),
    lambda raw:raw.replace(b'params',b'param\\u0073'),
    lambda raw:json.dumps(json.loads(raw),indent=2).encode(),
])
def test_noncanonical_wire_rejected(transform):
    with pytest.raises(ValueError):strategy.decode(transform(strategy.encode(params())))


@pytest.mark.parametrize('bad',[True,-1,2**63,'2',2.0])
def test_invalid_counter_type(bad):
    with pytest.raises(ValueError):strategy.initial(params(bad))


def test_parameters_cannot_be_replaced():
    p=params();s=strategy.initial(p)
    with pytest.raises(ValueError,match='IDENTITY_MISMATCH'):strategy.advance(s,params(3),segment_seq=1,segment_hash='1'*64)
    p['targets']=['0.1','0.9']
    with pytest.raises(ValueError):strategy.initial(p)


@pytest.mark.parametrize('seq,segment',[(0,'1'*64),(2,'1'*64),(True,'1'*64),(1,'0'*64),(1,'X'*64)])
def test_invalid_next_segment(seq,segment):
    p=params();s=strategy.initial(p)
    with pytest.raises(ValueError):strategy.advance(s,p,segment_seq=seq,segment_hash=segment)


def test_duplicate_segment_and_overflow():
    p=params();s=strategy.advance(strategy.initial(p),p,segment_seq=1,segment_hash='1'*64)['state']
    with pytest.raises(ValueError,match='SEQUENCE_INVALID'):strategy.advance(s,p,segment_seq=2,segment_hash='1'*64)
    s['bar_count']=2**63-1
    with pytest.raises(ValueError):strategy.advance(s,p,segment_seq=2**63,segment_hash='2'*64)


def test_unknown_fields_and_zero_hash_invariants():
    p=params();s=strategy.initial(p);s['hidden_state']=0
    with pytest.raises(ValueError):strategy.encode(s)
    del s['hidden_state'];s['last_segment_hash']='1'*64
    with pytest.raises(ValueError):strategy.encode(s)
    s['bar_count']=1;s['last_segment_hash']='0'*64
    with pytest.raises(ValueError):strategy.encode(s)


def test_custom_objects_rejected_without_hooks():
    class Custom:
        def __deepcopy__(self,*args):raise AssertionError('hook')
        def __eq__(self,*args):raise AssertionError('hook')
    p=params();p['targets']=[Custom(),'0.75']
    with pytest.raises(ValueError):strategy.initial(p)


def test_pure_strategy_target_handoff_to_reducer():
    from app.paper.session import shadow_reducer_v1 as reducer
    from test_phase6_shadow_reducer import config,bar,quote
    p=params(1);s=strategy.initial(p);r=reducer.initial(config(warmup_bars=1))
    for seq in range(1,5):
        result=strategy.advance(s,p,segment_seq=seq,segment_hash=f'{seq:064x}')
        s=result['state'];r=reducer.bar(r,bar(tick=seq*100),result['target'])
        if r['pending']:r=reducer.quote(r,quote(tick=seq*100+2))
    assert s['bar_count']==r['bar_count']==4
    assert r['units']=='2' and not r['stopped']
